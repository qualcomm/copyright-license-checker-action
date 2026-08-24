# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import json
import os
import subprocess
import tempfile
from collections import namedtuple

import scanner.config as config
from scanner.full_scanner import confident_license_expression

"""
Robust repo-license resolution for the full-repo scan.

This is a full_scan-owned replacement for main.get_license (the PR path). It
exists because scancode's verdict on a repo's own LICENSE file is not always
trustworthy: scancode 32.2.1 mis-detects some standard OSS licenses as its
catch-all "proprietary-license" at high confidence (e.g. the canonical
"BSD 3-Clause License" template text -- as used by this repo's own LICENSE --
detects as LicenseRef-scancode-proprietary-license, matched_length 219,
score 99.1). When that (mis)detection becomes the repo's resolved license, the
allowed-set is [proprietary] and every compliant file in the repo is flagged
incompatible -- poisoning the whole baseline.

The resolution order is LICENSE file -> config map -> abort, with two full_scan-only
differences from main.get_license:
  * a LICENSE-file detection that yields ONLY scancode's unreliable proprietary
    catch-all is treated as inconclusive and falls through to the config map,
    rather than trusting it; and
  * there is NO fabricated default. When nothing above establishes a baseline,
    resolution returns None so the caller can abort with a clear "No Root-Level
    Licence Found" status instead of scanning every file against a fabricated
    permissive baseline (which mass-flagged a GPL repo with no LICENSE file, e.g.
    qualcomm-linux/eva-driver). A real detection is reported as its actual SPDX id
    (BSD variants are NOT normalized to a canonical BSD).

Consequence to note: a genuine BSD-3-Clause LICENSE that scancode 32.2.1 mis-tags as
proprietary -- this repo's own LICENSE, and qualcomm/commit-emails-check-action -- now
resolves to None and aborts rather than being recovered. This is an accepted regression
until the scancode version upgrade, which detects that text correctly as BSD-3-Clause.

main.get_license / main.detect_license_from_file are intentionally left as-is
(they belong to the PR/patch-scan path, owned separately); the same mis-resolution
there is tracked as a patch-scan issue for that owner, not fixed in this repo.
"""

LOG_PREFIX = "< full-repo license/copyright check >"

# Structured result of resolving a repo's baseline license, so callers can explain
# WHY a license was chosen (not just what).
#   license      -- resolved SPDX id, or None when no baseline could be established
#   source       -- "license_file" | "config" | "none"
#   license_file -- the root file the baseline came from / that is present (or None)
#   num_license_files -- how many NON-EMPTY root-level license files exist (for the "N present" note)
#   config_project    -- the config.py PROJECT_NAME matched (when source == "config")
#   empty_license_files -- root license files that exist but are empty/whitespace-only
#                          (treated as absent); named so the abort can explain itself
LicenseResolution = namedtuple(
    "LicenseResolution",
    ["license", "source", "license_file", "num_license_files", "config_project",
     "empty_license_files"],
    defaults=((),),          # empty_license_files defaults to () -> old 5-arg calls still work
)

# Root-level license-file candidates. Superset of the list main.get_license
# checks: because resolution now ABORTS ("No Root-Level Licence Found") when no
# candidate exists, a missed-but-valid filename would produce a misleading "no
# licence" verdict, so the recognized set is broadened to cover the British
# spelling (LICENCE), the all-lowercase form (license), and COPYING.md. Order is
# preference order; the first existing file wins.
LICENSE_FILE_CANDIDATES = [
    'LICENSE', 'LICENSE.txt', 'LICENSE.TXT', 'LICENSE.md', 'LICENSE.MD',
    'LICENCE', 'LICENCE.txt', 'LICENCE.TXT', 'LICENCE.md', 'LICENCE.MD',
    'COPYING', 'COPYING.txt', 'COPYING.TXT', 'COPYING.md',
    'License', 'License.txt', 'License.md',
    'Licence', 'Licence.txt', 'Licence.md',
    'license', 'license.txt', 'license.md',
    'licence', 'licence.txt', 'licence.md',
]

# scancode's catch-all for text that looks proprietary but matches no real OSS
# license. On a repo's own LICENSE file this verdict is unreliable (see module
# docstring), so when it is the ONLY thing detected we do not trust it as the
# repo's declared license.
UNRELIABLE_LICENSE_REFS = {"LicenseRef-scancode-proprietary-license"}


def _detect_license_from_file(license_file_path: str) -> str:
    """
    Run scancode on a LICENSE file and return a confident SPDX expression.

    Uses confident_license_expression (the same noise-filtering the full-repo
    scan applies to source files) so short bare-word matches do not pollute the
    result.

    Args:
        license_file_path (str): Path to the LICENSE file.

    Returns:
        str: A confident SPDX expression, or None if nothing was detected / the
            scan failed.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = os.path.join(tmpdir, 'scancode_results.json')
            subprocess.run(
                ['scancode', '--license', '--strip-root', '--quiet',
                 '--json-pp', output_file, license_file_path],
                check=True, capture_output=True,
            )
            with open(output_file, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
    except (subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        print(f"{LOG_PREFIX} Warning: license detection failed for "
              f"{license_file_path}: {exc}")
        return None

    for file_result in data.get('files', []):
        if file_result.get('type') == 'file':
            return confident_license_expression(file_result)
    return None


def _has_license_text(path: str) -> bool:
    """
    Whether a candidate license file has any actual (non-whitespace) content.

    An empty or whitespace-only license file has no license to stand on, so it is
    treated as absent (see resolve_license_details) rather than fabricating a
    baseline from nothing.
    """
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            return bool(handle.read().strip())
    except OSError:
        return False


def resolve_license_details(repo_name: str) -> LicenseResolution:
    """
    Resolve the repo's top-level license AND explain where the answer came from.

    Order: scancode detection -> config map -> abort. A LICENSE-file detection that
    is only scancode's unreliable proprietary catch-all (UNRELIABLE_LICENSE_REFS) is
    ignored and falls through; any other detection is reported as its actual SPDX id
    (BSD variants are NOT normalized -- the real id is preserved).

    There is NO fabricated default. When nothing above establishes a baseline the
    result is source "none" (license None) and the caller must abort. The three
    no-baseline cases are distinguishable from the fields: `license_file` set ->
    a file is present but its license was not conclusively detected; else
    `empty_license_files` set -> a file is present but empty; else -> no file.

    All root-level candidates are inspected (not just the first) so `num_license_files`
    reports how many NON-EMPTY exist; the first by LICENSE_FILE_CANDIDATES priority is
    the one used for detection.

    Args:
        repo_name (str): The GitHub "owner/repo", used for the config-map lookup.

    Returns:
        LicenseResolution: license + source metadata (see the namedtuple).
    """
    # An empty / whitespace-only license file has no license content, so treat it as
    # absent: only NON-empty candidates count as usable license files. This keeps an
    # empty LICENSE distinguishable from a NON-empty file whose license scancode merely
    # can't classify -- both abort, but with different "no baseline" messages (see below).
    present, empty_present = [], []
    for candidate in LICENSE_FILE_CANDIDATES:
        path = os.path.join(os.getcwd(), candidate)
        if not os.path.exists(path):
            continue
        (present if _has_license_text(path) else empty_present).append(candidate)
    num_files = len(present)
    license_file = present[0] if present else None

    detected = None
    if license_file:
        print(f"{LOG_PREFIX} Found license file: {license_file}"
              + (f" ({num_files} present)" if num_files > 1 else ""))
        detected = _detect_license_from_file(os.path.join(os.getcwd(), license_file))

    if detected:
        # Trust the detection and report its actual SPDX id (BSD variants are NOT
        # normalized) unless it is only scancode's unreliable proprietary catch-all;
        # in that case fall through to the config map below.
        if detected not in UNRELIABLE_LICENSE_REFS:
            print(f"{LOG_PREFIX} Detected license: {detected}")
            return LicenseResolution(detected, "license_file", license_file,
                                     num_files, None)
        print(f"{LOG_PREFIX} Ignoring unreliable '{detected}' detected on the "
              f"LICENSE file; trying the config map.")

    # Config-map fallback (same suffix match main.get_license uses). An explicit
    # config entry is a human-declared license, so it is honored even with no LICENSE.
    for project in config.data['projects']:
        if (repo_name.endswith(f"/{project['PROJECT_NAME']}")
                or repo_name == project['PROJECT_NAME']):
            print(f"{LOG_PREFIX} Using license from config: {project['MARKINGS']}")
            return LicenseResolution(project['MARKINGS'], "config", None,
                                     num_files, project['PROJECT_NAME'])

    # Nothing established a baseline. Do NOT fabricate a default -- signal the caller to
    # abort, distinguishing the three cases so the report can explain itself:
    #   1. a non-empty license file is present but its license was not detected,
    #   2. a license file is present but empty, or
    #   3. there is no root-level license file at all.
    if license_file:
        print(f"{LOG_PREFIX} License file {license_file} is present but its license "
              f"could not be conclusively detected; no baseline established.")
        return LicenseResolution(None, "none", license_file, num_files, None,
                                 tuple(empty_present))
    if empty_present:
        print(f"{LOG_PREFIX} Root-level license file(s) {', '.join(empty_present)} "
              f"are empty; no license content and no config entry to establish a baseline.")
    else:
        print(f"{LOG_PREFIX} No root-level license file found and no config entry; "
              f"cannot establish a repository license baseline.")
    return LicenseResolution(None, "none", None, 0, None, tuple(empty_present))


def resolve_license(repo_name: str) -> str | None:
    """
    Resolve the repo's top-level license (back-compat wrapper over
    resolve_license_details). Returns the SPDX id, or None when no root-level
    license file and no config-map entry establish a baseline.
    """
    return resolve_license_details(repo_name).license


def describe_resolution(res: LicenseResolution) -> str:
    """
    Plain-text parenthetical explaining where a resolution came from, for console
    output (no links). Returns "" when there is nothing to say (no baseline).
    """
    if res.source == "license_file":
        note = (f"; {res.num_license_files} license files present"
                if res.num_license_files > 1 else "")
        return f"(based on license file {res.license_file}{note})"
    if res.source == "config":
        return f"(from scanner/config.py entry for {res.config_project})"
    if res.source == "none":
        if res.license_file:
            return "(license file present but license not conclusively detected)"
        if res.empty_license_files:
            return ("(a root-level license file is present but empty; "
                    "no license text to scan)")
    return ""

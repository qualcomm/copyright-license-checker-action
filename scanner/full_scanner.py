import os
import re
import sys
import json
import shutil
import tempfile
import subprocess
import warnings
from pathlib import Path
from scanner.full_repo import RepoScan

warnings.filterwarnings("ignore", message="Libmagic magic database not found")

LOG_PREFIX = "< full-repo license/copyright check >"

"""
Module to run a full-repository scan.

This is intentionally separate from scanner.license_scancode / scanner.copyright_checker,
which are diff-based: they reason about lines ADDED and DELETED in a pull-request
patch. A full-repo scan has a different job -- it inspects whole files that
already exist in the repository -- so it asks different questions:

    * Missing license header  -- the file has no detectable license at all
    * Incompatible license    -- the file carries a concrete, disallowed license
    * Missing copyright        -- the file has no copyright statement at all
    * Unexpected copyright     -- a copyright IS present but does not match the
                                  expected Qualcomm/Linux Foundation holder
                                  (non-blocking warning; see
                                  EXPECTED_COPYRIGHT_PATTERN)

Uncertain / unknown licenses (LicenseRef-scancode-unknown*) and copyright holders
that do not match the expected pattern are reported as non-blocking warnings,
matching the semantics of main.is_uncertain_license_issue on the PR path.

License-optional build files (RepoScan.LICENSE_OPTIONAL_EXTENSIONS -- .mk/.bp/.bb/.bbclass)
are the exception to the first and third checks: a missing license header or
missing copyright is NOT flagged for them, but an incompatible license they
carry is still a blocking error and an uncertain one still warns.
"""


def split_license_expression(expression: str) -> list:
    """
    Flatten an SPDX license expression into its individual license ids.

    Args:
        expression (str): An SPDX expression, e.g. "BSD-3-Clause AND (MIT OR GPL-2.0-only)".

    Returns:
        list: The individual license identifiers found in the expression.
    """
    licenses = []
    for part in expression.replace('(', '').replace(')', '').split(' AND '):
        for lic in part.split(' OR '):
            lic = lic.strip()
            if lic:
                licenses.append(lic)
    return licenses


# scancode attaches short "bare word" license references -- e.g. the word "gpl"
# in a comment or an identifier such as `lgpl_gpl_bsd` (its tokenizer splits on
# underscores) -- as license matches. When such a reference sits far from any
# real license, scancode demotes it to a low-confidence "license clue" and keeps
# it out of detected_license_expression_spdx. But when it lands ADJACENT to a
# genuine license header, scancode merges it into that detection and pollutes the
# expression -- e.g. a clean BSD file whose header comment mentions gpl becomes
# "BSD-3-Clause-Clear AND GPL-1.0-or-later AND LGPL-2.0-or-later", producing a
# false "incompatible license" finding. To avoid that we rebuild the expression
# from CONFIDENT matches only: an SPDX-identifier tag, or a match covering at
# least this many tokens. Score is NOT a usable filter here (a 2-token "lgpl"
# reference can score 100); matched token length is the reliable discriminator.
# The value is 3 because measured bare-word noise reaches 2 tokens (e.g. a "lgpl"
# reference matched via lgpl-2.0-plus_360.RULE), so 3 is the lowest floor that
# drops all of it. SPDX-identifier tags are kept regardless of length, so real
# SPDX headers are never affected by this threshold.
MIN_CONFIDENT_MATCH_LENGTH = 3


def confident_license_expression(file_result: dict) -> str:
    """
    Rebuild a file's SPDX license expression from confident matches only.

    Drops the short bare-word license references that scancode merged into a
    detection when they sat next to a real header (see MIN_CONFIDENT_MATCH_LENGTH
    for the rationale), keeping SPDX-identifier tags and matches that cover a
    meaningful number of tokens.

    Args:
        file_result (dict): One scancode 'files' entry.

    Returns:
        str: The combined SPDX expression built from confident matches, or None
            when no confident license match remains.
    """
    kept = []
    for detection in file_result.get('license_detections', []):
        for match in detection.get('matches', []):
            is_spdx_tag = match.get('matcher') == '1-spdx-id'
            long_enough = (match.get('matched_length') or 0) >= MIN_CONFIDENT_MATCH_LENGTH
            if not (is_spdx_tag or long_enough):
                continue
            expr = match.get('spdx_license_expression')
            if expr:
                kept.append(expr)

    if not kept:
        return None

    # Combine distinct sub-expressions with AND -- scancode's own default when a
    # file carries multiple detections -- parenthesizing any OR group so the
    # AND/OR structure survives for is_expression_permissive.
    parts = []
    for expr in dict.fromkeys(kept):
        parts.append(f'({expr})' if ' OR ' in expr else expr)
    return ' AND '.join(parts)


# Expected copyright holder pattern. A file whose detected copyright does NOT
# match this is reported as a NON-BLOCKING warning (a file with no copyright at
# all is still a blocking error -- see run). This mirrors repolinter's Qualcomm
# header rules (source-qualcomm-license-headers-exist /
# qualcomm-source-license-headers-exist) so the two tools agree on holder policy.
#
# The pattern is matched against scancode's detected copyright statements (not raw
# file text). Case-insensitive. Note the regex's alternation precedence -- it is
# three top-level alternatives:
#   (Copyright|©) ... Qualcomm Innovation Center, Inc          OR
#   Qualcomm Technologies, Inc                                 OR
#   Copyright (c)/© <year 2012-2022> The Linux Foundation
# (kept byte-for-byte identical to the repolinter ruleset, including its quirks:
# the Linux-Foundation branch only covers years 2012-2022, and the "Qualcomm
# Technologies, Inc" branch matches anywhere in a statement.)
EXPECTED_COPYRIGHT_PATTERN = re.compile(
    r"(Copyright|©).*Qualcomm Innovation Center, Inc"
    r"|Qualcomm Technologies, Inc"
    r"|Copyright (\(c\)|©) (20(1[2-9]|2[0-2])(-|,|\s)*)+ The Linux Foundation",
    re.IGNORECASE,
)


def copyright_matches_expected(copyrights: list) -> bool:
    """
    Report whether any detected copyright statement matches the expected holder.

    Args:
        copyrights (list): The file's detected copyright statements (truthy only).

    Returns:
        bool: True if at least one statement matches EXPECTED_COPYRIGHT_PATTERN.
    """
    return any(EXPECTED_COPYRIGHT_PATTERN.search(c) for c in copyrights if c)


class FullScanner:
    """
    Class to scan every file surfaced by RepoScan (git-tracked, and optionally
    untracked-but-not-ignored) for license and copyright compliance. Source
    files are fully checked; license-optional build files (.mk/.bp/.bb/.bbclass) are
    checked only for an incompatible/uncertain license (see run).
    """

    def __init__(self, repo_scan: RepoScan, permissive_licenses: list) -> None:
        """
        Initialize the FullScanner.

        Args:
            repo_scan (RepoScan): The set of source files to scan.
            permissive_licenses (list): Licenses considered allowed for this repo.
                Also used to recognize known-permissive LicenseRef ids (e.g.
                LicenseRef-scancode-unicode) so they are not treated as uncertain.
        """
        self.repo_scan = repo_scan
        self.permissive_licenses = permissive_licenses

    def is_uncertain_license(self, lic: str) -> bool:
        """
        Check whether a single license id is uncertain / unknown.

        Mirrors main.is_uncertain_license_issue's inner predicate: a license is
        uncertain when it is a LicenseRef-scancode-* id that is not in the known
        permissive list.

        Args:
            lic (str): A single license identifier.

        Returns:
            bool: True if the license is uncertain, False otherwise.
        """
        if not lic.startswith('LicenseRef-scancode-'):
            return False
        if lic in self.permissive_licenses:
            return False
        return True

    def is_expression_permissive(self, expression: str) -> bool:
        """
        Evaluate whether an SPDX license expression is permissive, honoring
        AND / OR structure: an OR group passes if at least one option is
        permissive; an AND group requires every group to be permissive.
        Flattening the expression (as a naive check would) is wrong -- it would
        block a valid dual-license such as "MIT OR GPL-2.0-only" and would fail
        to notice a disallowed license hidden in an AND group.

        NOTE: this intentionally does NOT mirror LicenseChecker.is_license_permissive
        on the PR path. That path short-circuits on a leading "(X OR Y) AND ..."
        group and ignores the trailing AND terms (treating them as comment-derived
        noise it cannot filter). FullScanner filters that noise upstream via
        confident_license_expression, so here every AND term is a real detection and
        MUST be evaluated -- otherwise an incompatible license after a permissive OR
        group (e.g. "(MIT OR Apache-2.0) AND GPL-2.0") would wrongly pass. The
        PR-path short-circuit is tracked as a separate patch-scan-owner issue.

        Args:
            expression (str): The SPDX license expression to evaluate.

        Returns:
            bool: True if the expression is permissive, False otherwise.
        """
        expression = expression.strip()

        # Every AND group must be permissive; within a group with an OR, at least
        # one option must be permissive. This correctly handles a leading
        # "(X OR Y) AND Z" -- the OR group passes on a permissive option AND the
        # trailing Z is still checked (unlike the PR path; see the docstring).
        for and_group in expression.split(' AND '):
            and_group = and_group.strip()
            if ' OR ' in and_group:
                or_licenses = [lic.strip() for lic in and_group.strip('()').split(' OR ')]
                if not any(lic in self.permissive_licenses for lic in or_licenses):
                    return False
            else:
                if and_group.strip('()') not in self.permissive_licenses:
                    return False
        return True

    def classify_license(self, expression: str) -> str:
        """
        Classify a file's detected SPDX license expression.

        Reproduces main.py's two-stage decision:
          1. Permissiveness is decided structurally (AND / OR aware) via
             is_expression_permissive -- if permissive, the file is 'ok'.
          2. Otherwise error-vs-warning is decided on the flattened license list
             (mirrors main.is_uncertain_license_issue): a warning only if EVERY
             license is uncertain; any concrete disallowed license (e.g. GPL) is
             an error. A lone proprietary marker is always an error.

        Args:
            expression (str): The file's detected SPDX license expression.

        Returns:
            str: 'ok', 'error', or 'warning'.
        """
        licenses = split_license_expression(expression)
        if not licenses:
            return 'ok'

        if self.is_expression_permissive(expression):
            return 'ok'

        # Not permissive -- decide whether it is a hard error or a warning.
        # Special case, matching main.py: a lone proprietary marker is blocking.
        if len(licenses) == 1 and licenses[0] == "LicenseRef-scancode-proprietary-license":
            return 'error'

        # Only uncertain / unknown licenses -> non-blocking warning. Anything
        # concrete and disallowed (GPL, a proprietary marker mixed in, etc.) is
        # a blocking error.
        if all(self.is_uncertain_license(lic) for lic in licenses):
            return 'warning'
        return 'error'

    def scan_files(self, files: list) -> dict:
        """
        Run scancode once over whole copies of the given files.

        Args:
            files (list): Repository-relative file paths to scan.

        Returns:
            dict: Mapping of relative path -> {'license': spdx_expr_or_None,
                  'copyrights': [statements]}.
        """
        results = {}
        if not files:
            return results

        root = self.repo_scan.root
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy each file into the temp dir preserving its relative path so
            # scancode reports back the same path (via --strip-root) and there
            # are no basename collisions.
            expected = set()
            for rel_path in files:
                src = os.path.join(root, rel_path)
                if not os.path.isfile(src):
                    continue
                dst = Path(tmpdir, rel_path)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst)
                expected.add(rel_path)

            if not expected:
                return results

            output_file = os.path.join(tmpdir, 'scancode_results.json')
            # Do NOT use check=True: scancode exits non-zero when it records
            # per-file scan warnings/errors even though it still writes a valid
            # results file. We treat the run as usable as long as parseable JSON
            # was produced, and only fail when it was not -- surfacing scancode's
            # captured output so the failure is diagnosable (it is otherwise
            # hidden by --quiet).
            proc = subprocess.run([
                'scancode',
                '--license',
                '--copyright',
                '--strip-root',
                '--quiet',
                '--json-pp', output_file,
                tmpdir,
            ], capture_output=True, text=True)

            try:
                with open(output_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"scancode failed to produce usable results "
                    f"(exit code {proc.returncode}).\n"
                    f"stdout: {proc.stdout.strip()}\n"
                    f"stderr: {proc.stderr.strip()}"
                ) from exc

            # scancode produced results but exited non-zero: surface its output
            # so the underlying warning/error is visible (it is hidden by
            # --quiet), rather than silently trusting possibly-degraded results.
            if proc.returncode != 0:
                print(f"{LOG_PREFIX} WARNING: scancode exited {proc.returncode} "
                      f"but produced results; continuing.", file=sys.stderr)
                if proc.stdout.strip():
                    print(f"{LOG_PREFIX} scancode stdout: {proc.stdout.strip()}",
                          file=sys.stderr)
                if proc.stderr.strip():
                    print(f"{LOG_PREFIX} scancode stderr: {proc.stderr.strip()}",
                          file=sys.stderr)

            for file_result in data.get('files', []):
                if file_result.get('type') != 'file':
                    continue
                path = file_result.get('path')
                if path not in expected:
                    continue

                # Per-file scan errors mean detection was unreliable for this
                # file -- surface them so an empty copyright/license result is
                # not silently reported as "missing".
                scan_errors = file_result.get('scan_errors') or []
                if scan_errors:
                    print(f"{LOG_PREFIX} WARNING: scancode reported scan errors "
                          f"for {path}: {scan_errors}", file=sys.stderr)

                results[path] = {
                    # Rebuilt from confident matches only, so short bare-word
                    # license references adjacent to a real header do not create
                    # a false "incompatible license" (see confident_license_expression).
                    'license': confident_license_expression(file_result),
                    # Keep only truthy statements: a scancode detection with a
                    # missing/empty 'copyright' value would otherwise leave a
                    # [None]/[''] list -- truthy, so `not copyrights` is False --
                    # making an empty detection look like a present copyright and
                    # silently suppressing the "No copyright statement found" check.
                    'copyrights': [
                        c.get('copyright')
                        for c in (file_result.get('copyrights') or [])
                        if c.get('copyright')
                    ],
                    'scan_errors': scan_errors,
                }

        return results

    def run(self) -> tuple:
        """
        Run the scan over all files surfaced by RepoScan. Source files are
        checked for a missing/incompatible license and a missing copyright
        (blocking), plus a copyright whose holder does not match the expected
        pattern (non-blocking warning). License-optional build files
        (.mk/.bp/.bb/.bbclass) skip the missing-license and all copyright findings but are
        still flagged for an incompatible license.

        Returns:
            tuple: (flagged_files, warning_files). Each is a dict mapping a file
                path to {'license_issues': [...], 'copyright_issues': [...]}.
                flagged_files holds blocking issues; warning_files holds
                non-blocking ones (uncertain license and/or unexpected copyright
                holder).
        """
        files = self.repo_scan.get_files()
        scan_results = self.scan_files(files)

        flagged_files = {}
        warning_files = {}

        for path in files:
            result = scan_results.get(path)
            if result is None:
                # scancode returned nothing for this file (unreadable / skipped).
                continue

            # License-optional build files (.mk/.bp/.bb/.bbclass) relax the "missing
            # license" and "missing copyright" findings, but an incompatible or
            # uncertain license they DO carry is still classified normally.
            license_optional = self.repo_scan.is_license_optional(path)

            error_license = []
            warning_license = []

            license_expr = result['license']
            if not license_expr:
                # Missing license header -- no detectable license anywhere.
                if not license_optional:
                    error_license.append("No license header found")
            else:
                severity = self.classify_license(license_expr)
                if severity == 'error':
                    error_license.append(f"Incompatible license: {license_expr}")
                elif severity == 'warning':
                    warning_license.append(f"Uncertain license, review manually: {license_expr}")

            error_copyright = []
            warning_copyright = []
            if result.get('scan_errors'):
                # scancode's copyright scanner errored for this file (see the
                # warning emitted in scan_files). An errored scan is "unknown",
                # not "absent" -- do NOT report a missing copyright, or we would
                # emit a false blocking finding for a file that may well have a
                # copyright. License detection is independent and still applies.
                pass
            elif license_optional:
                # License-optional build files are not required to carry a
                # copyright statement.
                pass
            elif not result['copyrights']:
                # Missing copyright -- no copyright statement anywhere in the file.
                # This stays a BLOCKING error.
                error_copyright.append("No copyright statement found")
            elif not copyright_matches_expected(result['copyrights']):
                # Copyright IS present but does not match the expected holder
                # pattern (see EXPECTED_COPYRIGHT_PATTERN). Non-blocking WARNING --
                # a missing copyright blocks, a wrong holder only warns.
                warning_copyright.append(
                    "Copyright holder does not match the expected Qualcomm/Linux "
                    "Foundation pattern, review manually"
                )

            if error_license or error_copyright:
                flagged_files[path] = {
                    'license_issues': error_license,
                    'copyright_issues': error_copyright,
                }
            if warning_license or warning_copyright:
                warning_files[path] = {
                    'license_issues': warning_license,
                    'copyright_issues': warning_copyright,
                }

        return flagged_files, warning_files

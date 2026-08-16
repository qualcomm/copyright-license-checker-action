import json
import os
import subprocess
import tempfile

import scanner.config as config
from scanner.full_scanner import confident_license_expression

"""
Robust repo-license resolution for the full-repo scan.

This is a full_scan-owned replacement for main.get_license (the PR path). It
exists because scancode's verdict on a repo's own LICENSE file is not always
trustworthy: scancode 32.2.1 mis-detects some standard OSS licenses as its
catch-all "proprietary-license" at high confidence (e.g. a plain BSD-3-Clause
LICENSE with a year-less Qualcomm copyright detects as
LicenseRef-scancode-proprietary-license, matched_length 219, score 99.1). When
that (mis)detection becomes the repo's resolved license, the allowed-set is
[proprietary] and every compliant file in the repo is flagged incompatible --
poisoning the whole baseline.

The resolution order mirrors main.get_license (LICENSE file -> config map ->
default), with one hardening step: a LICENSE-file detection that yields ONLY
scancode's unreliable proprietary catch-all is treated as inconclusive and falls
through to the config map / default, rather than trusting it.

main.get_license / main.detect_license_from_file are intentionally left as-is
(they belong to the PR/patch-scan path, owned separately); the same mis-resolution
there is tracked as a patch-scan issue for that owner, not fixed in this repo.
"""

LOG_PREFIX = "< full-repo license/copyright check >"

DEFAULT_LICENSE = "BSD-3-Clause-Clear"

# LICENSE-file candidates, in the same order main.get_license checks them.
LICENSE_FILE_CANDIDATES = [
    'LICENSE', 'LICENSE.txt', 'LICENSE.TXT', 'LICENSE.md', 'LICENSE.MD',
    'COPYING', 'COPYING.txt', 'COPYING.TXT',
    'License', 'License.txt', 'License.md',
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


def resolve_license(repo_name: str) -> str:
    """
    Resolve the repo's top-level license for the full-repo scan.

    Order: LICENSE-file detection -> config map -> default (BSD-3-Clause-Clear).
    A LICENSE-file detection that is only scancode's unreliable proprietary
    catch-all (UNRELIABLE_LICENSE_REFS) is ignored and resolution falls through,
    so a scancode misclassification of a real OSS LICENSE cannot poison the
    baseline. Any detected BSD variant normalizes to BSD-3-Clause-Clear, matching
    main.get_license.

    Args:
        repo_name (str): The GitHub "owner/repo", used for the config-map lookup.

    Returns:
        str: The resolved SPDX license id.
    """
    detected = None
    for candidate in LICENSE_FILE_CANDIDATES:
        path = os.path.join(os.getcwd(), candidate)
        if os.path.exists(path):
            print(f"{LOG_PREFIX} Found license file: {candidate}")
            detected = _detect_license_from_file(path)
            break

    if detected:
        # Any BSD variant -> the org default (mirrors main.get_license).
        if "bsd" in detected.lower():
            print(f"{LOG_PREFIX} License contains 'bsd', using default: {DEFAULT_LICENSE}")
            return DEFAULT_LICENSE
        # Trust the detection unless it is only scancode's unreliable proprietary
        # catch-all; in that case fall through to config/default below.
        if detected not in UNRELIABLE_LICENSE_REFS:
            print(f"{LOG_PREFIX} Detected license: {detected}")
            return detected
        print(f"{LOG_PREFIX} Ignoring unreliable '{detected}' detected on the "
              f"LICENSE file; falling back to config/default.")

    # Config-map fallback (same suffix match main.get_license uses).
    for project in config.data['projects']:
        if (repo_name.endswith(f"/{project['PROJECT_NAME']}")
                or repo_name == project['PROJECT_NAME']):
            print(f"{LOG_PREFIX} Using license from config: {project['MARKINGS']}")
            return project['MARKINGS']

    print(f"{LOG_PREFIX} Using default license: {DEFAULT_LICENSE}")
    return DEFAULT_LICENSE

import os
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

Uncertain / unknown licenses (LicenseRef-scancode-unknown*) are reported as
non-blocking warnings, matching the semantics of main.is_uncertain_license_issue
on the PR path.
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


class FullScanner:
    """
    Class to scan every tracked source file in a repository for license and
    copyright compliance.
    """

    def __init__(self, repo_scan: RepoScan, permissive_licenses: list) -> None:
        """
        Initialize the FullScanner.

        Args:
            repo_scan (RepoScan): The set of tracked source files to scan.
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
        AND / OR structure.

        This mirrors LicenseChecker.is_license_permissive on the PR path so the
        two scans agree: for an OR group at least one option must be permissive;
        for an AND group every group must be permissive. Flattening the
        expression (as a naive check would) is wrong -- it would block a valid
        dual-license such as "MIT OR GPL-2.0-only" and would fail to notice a
        disallowed license hidden in an AND group.

        Args:
            expression (str): The SPDX license expression to evaluate.

        Returns:
            bool: True if the expression is permissive, False otherwise.
        """
        expression = expression.strip()

        # Dual-license pattern: "(X OR Y) AND ..." -- if the leading OR group has
        # a permissive option we accept it (matches the PR path's handling).
        if expression.startswith('(') and ' OR ' in expression.split(')')[0]:
            or_part = expression.split(')')[0] + ')'
            or_licenses = [lic.strip() for lic in or_part.strip('()').split(' OR ')]
            return any(lic in self.permissive_licenses for lic in or_licenses)

        # Standard evaluation: every AND group must be permissive.
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
                    'license': file_result.get('detected_license_expression_spdx'),
                    'copyrights': [
                        c.get('copyright')
                        for c in (file_result.get('copyrights') or [])
                    ],
                    'scan_errors': scan_errors,
                }

        return results

    def run(self) -> tuple:
        """
        Run the scan over all tracked source files.

        Returns:
            tuple: (flagged_files, warning_files). Each is a dict mapping a file
                path to {'license_issues': [...], 'copyright_issues': [...]}.
                flagged_files holds blocking issues; warning_files holds
                non-blocking ones.
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

            error_license = []
            warning_license = []

            license_expr = result['license']
            if not license_expr:
                # Missing license header -- no detectable license anywhere.
                error_license.append("No license header found")
            else:
                severity = self.classify_license(license_expr)
                if severity == 'error':
                    error_license.append(f"Incompatible license: {license_expr}")
                elif severity == 'warning':
                    warning_license.append(f"Uncertain license, review manually: {license_expr}")

            error_copyright = []
            if result.get('scan_errors'):
                # scancode's copyright scanner errored for this file (see the
                # warning emitted in scan_files). An errored scan is "unknown",
                # not "absent" -- do NOT report a missing copyright, or we would
                # emit a false blocking finding for a file that may well have a
                # copyright. License detection is independent and still applies.
                pass
            elif not result['copyrights']:
                # Missing copyright -- no copyright statement anywhere in the file.
                error_copyright.append("No copyright statement found")

            if error_license or error_copyright:
                flagged_files[path] = {
                    'license_issues': error_license,
                    'copyright_issues': error_copyright,
                }
            if warning_license:
                warning_files[path] = {
                    'license_issues': warning_license,
                    'copyright_issues': [],
                }

        return flagged_files, warning_files

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import logging
import os
import sys

import click

from scanner.licenses import PERMISSIVE_LICENSES, COPYLEFT_LICENSES
from scanner.full_repo import RepoScan
from scanner.full_scanner import FullScanner, expression_allowed_by
from scanner.license_resolver import resolve_license_details, describe_resolution

"""
Entry point for the full-repository scan.

This is a SEPARATE entry point from main.py, which runs the pull-request patch
scan. The patch scan looks only at a commit diff, so on a repo enabled after
many commits it never inspects the legacy files. This full-repo scan walks every
source file (and license-optional build file) in the working tree, which makes
it suitable for:

    * periodic (e.g. scheduled) compliance checks, and
    * establishing a baseline on repos onboarded with existing history.

By default it scans git-tracked files only; --include-untracked widens the net
to untracked-but-not-ignored files as well.

Usage:
    python full_scan.py <repo_name> [fail_on_findings] [--repo-path PATH]
                                                        [--include-untracked]

    repo_name          -- github "owner/repo", used to resolve the repo license.
    fail_on_findings   -- optional; defaults to "true". "true" exits non-zero
                          when blocking issues are found; anything else reports
                          only and exits 0.
    --repo-path        -- optional path to the repository working tree to scan.
                          Defaults to the current directory, which is what the
                          action uses (it runs with cwd = the consumer checkout).
                          Provide this to scan a local clone without cd-ing into
                          it, e.g. `python full_scan.py owner/repo true
                          --repo-path ~/clones/some-repo`.
    --include-untracked -- also scan untracked files that are not .gitignore'd
                          (not just git-tracked files). Off by default.
    --include-licenseignore -- also scan files matched by the repo's
                          .licenseignore (skipped by default). Off by default.

    repo_name and fail_on_findings stay positional so the existing action
    invocation (full-scan/action.yml) keeps working unchanged; fail_on_findings
    is now optional and defaults to "true" when omitted on the command line.
"""

LOG_PREFIX = "< full-repo license/copyright check >"


def report_missing_root_license(repo_name: str, fail_on_findings: bool,
                                log_prefix: str, resolution=None) -> None:
    """
    Report that no root-level license file exists and stop the scan.

    Called when resolve_license_details finds no baseline -- the repo has no usable
    root-level license file (LICENSE/COPYING/...) and no config-map entry, so a
    license baseline cannot be established. Rather than fabricate a default and flag
    every file against it, the full-repo scan is aborted here with a clear status.
    Exits non-zero when fail_on_findings is set (so CI fails until a license is
    declared or the repo is onboarded in config.py), otherwise exits 0 (report-only).

    Args:
        repo_name (str): The GitHub "owner/repo" that was scanned.
        fail_on_findings (bool): Whether the missing license should fail the run.
        log_prefix (str): The prefix to use for logging.
        resolution: The LicenseResolution (optional). Its fields select which of the
            three no-baseline cases is reported: a present-but-undetected file, a
            present-but-empty file, or no file at all.
    """
    license_file = getattr(resolution, "license_file", None)
    empty = getattr(resolution, "empty_license_files", ()) or ()
    if license_file:
        status = "License Not Conclusively Detected"
        body = [
            f"A root-level license file ({license_file}) is present, but its license "
            f"could not be",
            "identified (scancode did not recognize it, it is not BSD-3-Clause, and the "
            "repo is not",
            "configured in scanner/config.py). No repository license baseline could be "
            "established,",
            "so no per-file license or copyright analysis was performed.",
        ]
    elif empty:
        status = "No Root-Level Licence Found"
        body = [
            f"A root-level license file ({', '.join(empty)}) exists but is empty, so it "
            f"declares no",
            "license. With no configured license either, a repository license baseline "
            "could not be",
            "established. No per-file license or copyright analysis was performed.",
        ]
    else:
        status = "No Root-Level Licence Found"
        body = [
            "License analysis was stopped: the repository has no root-level license file",
            "(LICENSE / LICENSE.txt / LICENSE.md / COPYING) and no configured license, so a",
            "repository license baseline could not be established. No per-file license or",
            "copyright analysis was performed.",
        ]

    print(f"{log_prefix} ┌─────────────────────────────────────────────┐")
    print(f"{log_prefix} │ {status}")
    print(f"{log_prefix} └─────────────────────────────────────────────┘")
    print(f"{log_prefix} Repository: {repo_name}")
    for line in body:
        print(f"{log_prefix} {line}")
    print(f"{log_prefix} Please fix this issue OR reach out to ossops.support "
          f"team for help.")

    # Respect fail_on_findings: a missing declared license blocks CI when the
    # caller opts in, otherwise this is report-only.
    sys.exit(1 if fail_on_findings else 0)


def beautify_scan_output(flagged_files: dict, warning_files: dict,
                         license: str, fail_on_findings: bool,
                         log_prefix: str, license_reason: str = "") -> None:
    """
    Print the full-repo scan report and exit with the appropriate status.

    Args:
        flagged_files (dict): Files with blocking issues.
        warning_files (dict): Files with non-blocking (warning) issues.
        license (str): The resolved top-level license of the repo.
        fail_on_findings (bool): Whether blocking issues should fail the run.
        log_prefix (str): The prefix to use for logging.
        license_reason (str): Optional parenthetical explaining how the license was
            resolved (e.g. "(based on license file LICENSE)"); appended to the
            "Repository license" line.
    """
    license_line = f"Repository license: {license}"
    if license_reason:
        license_line += f" {license_reason}"

    if not flagged_files and not warning_files:
        print(f"{log_prefix} ✅ No license or copyright issues detected across the repository")
        print(f"{log_prefix} {license_line}")
        sys.exit(0)

    output = []
    output.append(f"{log_prefix} ┌───────────────────────────────────────────┐")
    output.append(f"{log_prefix} │        **Full-Repo Scan Report**           │")
    output.append(f"{log_prefix} ├───────────────────────────────────────────┤")
    output.append(f"{log_prefix} │ {license_line}")
    output.append(f"{log_prefix} │")
    output.append(f"{log_prefix} │ 📖 For more information, see: COMPLIANCE.md")
    output.append(f"{log_prefix} │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md")
    output.append(f"{log_prefix} ├───────────────────────────────────────────┤")

    if flagged_files:
        header = "B L O C K I N G   E R R O R S" if fail_on_findings else "F I N D I N G S  (report-only)"
        output.append(f"{log_prefix} │")
        output.append(f"{log_prefix} │ ═══════════════════════════════════════════")
        output.append(f"{log_prefix} │ 🚨  {header}")
        output.append(f"{log_prefix} │ ═══════════════════════════════════════════")
        for file, issues in flagged_files.items():
            output.append(f"{log_prefix} │")
            output.append(f"{log_prefix} │ ┌─ 📄 F I L E: {file}")
            if issues['license_issues']:
                output.append(f"{log_prefix} │ │")
                output.append(f"{log_prefix} │ ├─ 🚨 LICENSE ISSUES:")
                for issue in issues['license_issues']:
                    output.append(f"{log_prefix} │ │  • {issue}")
            if issues['copyright_issues']:
                output.append(f"{log_prefix} │ │")
                output.append(f"{log_prefix} │ ├─ 🚨 COPYRIGHT ISSUES:")
                for issue in issues['copyright_issues']:
                    output.append(f"{log_prefix} │ │  • {issue}")
            output.append(f"{log_prefix} │ └─────────────────────────────────────────")

    if warning_files:
        output.append(f"{log_prefix} │")
        output.append(f"{log_prefix} │ ═══════════════════════════════════════════")
        output.append(f"{log_prefix} │ ⚠️   W A R N I N G S  (Non-blocking)")
        output.append(f"{log_prefix} │ ═══════════════════════════════════════════")
        for file, issues in warning_files.items():
            output.append(f"{log_prefix} │")
            output.append(f"{log_prefix} │ ┌─ 📄 F I L E: {file}")
            if issues['license_issues']:
                output.append(f"{log_prefix} │ │")
                output.append(f"{log_prefix} │ ├─ ⚠️  LICENSE WARNINGS:")
                for issue in issues['license_issues']:
                    output.append(f"{log_prefix} │ │  • {issue}")
            if issues['copyright_issues']:
                output.append(f"{log_prefix} │ │")
                output.append(f"{log_prefix} │ ├─ ⚠️  COPYRIGHT WARNINGS:")
                for issue in issues['copyright_issues']:
                    output.append(f"{log_prefix} │ │  • {issue}")
            output.append(f"{log_prefix} │ └─────────────────────────────────────────")

    output.append(f"{log_prefix} └───────────────────────────────────────────┘")

    # Summary line.
    output.append(
        f"{log_prefix} Summary: {len(flagged_files)} file(s) with blocking issues, "
        f"{len(warning_files)} file(s) with warnings"
    )

    print("\n".join(output))

    # Exit behavior is configurable so a periodic scan of a legacy repo does
    # not break CI unless the caller opts in. Use a fixed non-zero code rather
    # than the flagged-file count: exit codes are truncated mod 256, so a count
    # that is a multiple of 256 would otherwise wrongly report success.
    if flagged_files and fail_on_findings:
        sys.exit(1)
    sys.exit(0)


@click.command()
@click.argument("repo_name")
@click.argument("fail_on_findings", required=False, default="true")
@click.option(
    "--repo-path",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Path to the repository working tree to scan. Defaults to the current "
         "directory, which is what the action uses (it runs with cwd = the "
         "consumer checkout). Provide this to scan a local clone without "
         "cd-ing into it.",
)
@click.option(
    "--include-untracked",
    is_flag=True,
    default=False,
    show_default=True,
    help="Also scan untracked files that are not .gitignore'd, not just "
         "git-tracked files (uses `git ls-files --cached --others "
         "--exclude-standard`). Use this to catch files that were added but "
         "not yet committed. Ignored files are still skipped.",
)
@click.option(
    "--include-licenseignore",
    is_flag=True,
    default=False,
    show_default=True,
    help="Also scan files matched by the repo's .licenseignore (by default "
         "those files are skipped). Use this to audit vendored/upstream paths "
         "that .licenseignore normally excludes.",
)
def main(repo_name: str, fail_on_findings: str, repo_path: str,
         include_untracked: bool, include_licenseignore: bool) -> None:
    """
    Scan a repository's source files for copyright and license compliance.

    Source files are fully checked; build files (.mk/.bp) are license-optional
    -- scanned for an incompatible license but not required to carry a header or
    copyright. By default only git-tracked files are scanned; pass
    --include-untracked to also cover untracked-but-not-ignored files, and
    --include-licenseignore to also cover files the repo's .licenseignore skips.

    REPO_NAME is the GitHub "owner/repo", used to resolve the repo license.
    FAIL_ON_FINDINGS is optional and defaults to "true": "true" exits non-zero
    when blocking issues are found, anything else reports only and exits 0. Both
    stay positional so the existing action invocation (full-scan/action.yml)
    keeps working unchanged.
    """
    # Clamp chatty logging from license_identifier
    logging.basicConfig(level=logging.WARNING)

    fail_on_findings = fail_on_findings.strip().lower() == "true"

    # Both get_license (reads LICENSE from cwd) and RepoScan (git ls-files with
    # cwd=".") are relative to the current directory. click.Path already
    # validated repo_path (exists, is a directory) and resolved it to an
    # absolute path, so chdir once here points both at the requested repo --
    # and keeps main.py (get_license) untouched.
    os.chdir(repo_path)

    resolution = resolve_license_details(repo_name)
    license = resolution.license
    if license is None:
        # No root-level license file and no config entry -> no baseline. Abort
        # rather than scan every file against a fabricated default license.
        report_missing_root_license(repo_name, fail_on_findings, LOG_PREFIX, resolution)
        return  # report_missing_root_license exits; return keeps intent explicit.
    license_reason = describe_resolution(resolution)

    # Select the allow-list every file is judged against, honoring the resolved
    # license's AND/OR structure rather than exact string membership: a compound
    # all-permissive license such as "BSD-3-Clause-Clear AND BSD-3-Clause" (what
    # scancode reports for some Qualcomm LICENSE files) must select the full
    # permissive set, not a singleton [license] bucket that would then flag every
    # compliant BSD file as an incompatible license.
    if expression_allowed_by(license, PERMISSIVE_LICENSES):
        allowed_licenses = PERMISSIVE_LICENSES
    elif expression_allowed_by(license, COPYLEFT_LICENSES):
        allowed_licenses = COPYLEFT_LICENSES
    else:
        allowed_licenses = [license]

    repo_scan = RepoScan(include_untracked=include_untracked,
                         include_licenseignore=include_licenseignore)
    scanner = FullScanner(repo_scan, allowed_licenses)

    flagged_files, warning_files = scanner.run()

    beautify_scan_output(flagged_files, warning_files, license, fail_on_findings,
                         LOG_PREFIX, license_reason)


if __name__ == '__main__':
    main()  # pylint: disable=no-value-for-parameter

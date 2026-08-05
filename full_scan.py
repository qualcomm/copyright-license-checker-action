import logging
import sys

from main import get_license, PERMISSIVE_LICENSES, COPYLEFT_LICENSES
from scanner.full_repo import RepoScan
from scanner.full_scanner import FullScanner

"""
Entry point for the full-repository scan.

This is a SEPARATE entry point from main.py, which runs the pull-request patch
scan. The patch scan looks only at a commit diff, so on a repo enabled after
many commits it never inspects the legacy files. This full-repo scan walks every
tracked source file in the working tree, which makes it suitable for:

    * periodic (e.g. scheduled) compliance checks, and
    * establishing a baseline on repos onboarded with existing history.

Usage:
    python full_scan.py <repo_name> <fail_on_findings>

    Both arguments are always supplied by the action (full-scan/action.yml
    defaults fail_on_findings to 'false'), so they are read positionally.

    repo_name        -- github "owner/repo", used to resolve the repo license.
    fail_on_findings -- "true" to exit non-zero when blocking issues are found;
                        anything else reports only and exits 0.
"""

LOG_PREFIX = "< full-repo license/copyright check >"


def beautify_scan_output(flagged_files: dict, warning_files: dict,
                         license: str, fail_on_findings: bool,
                         log_prefix: str) -> None:
    """
    Print the full-repo scan report and exit with the appropriate status.

    Args:
        flagged_files (dict): Files with blocking issues.
        warning_files (dict): Files with non-blocking (warning) issues.
        license (str): The resolved top-level license of the repo.
        fail_on_findings (bool): Whether blocking issues should fail the run.
        log_prefix (str): The prefix to use for logging.
    """
    if not flagged_files and not warning_files:
        print(f"{log_prefix} ✅ No license or copyright issues detected across the repository")
        sys.exit(0)

    output = []
    output.append(f"{log_prefix} ┌───────────────────────────────────────────┐")
    output.append(f"{log_prefix} │        **Full-Repo Scan Report**           │")
    output.append(f"{log_prefix} ├───────────────────────────────────────────┤")
    output.append(f"{log_prefix} │ Repository license: {license}")
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


def main() -> None:
    """
    The main function of the full-repo scan.
    """
    # Clamp chatty logging from license_identifier
    logging.basicConfig(level=logging.WARNING)

    repo_name = sys.argv[1]
    fail_on_findings = sys.argv[2].strip().lower() == "true"

    license = get_license(repo_name)
    if license in PERMISSIVE_LICENSES:
        allowed_licenses = PERMISSIVE_LICENSES
    elif license in COPYLEFT_LICENSES:
        allowed_licenses = COPYLEFT_LICENSES
    else:
        allowed_licenses = [license]

    repo_scan = RepoScan()
    scanner = FullScanner(repo_scan, allowed_licenses)

    flagged_files, warning_files = scanner.run()

    beautify_scan_output(flagged_files, warning_files, license, fail_on_findings, LOG_PREFIX)


if __name__ == '__main__':
    main()

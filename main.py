import logging
import sys
import scanner.config as config
from scanner.patch import Patch
from scanner.license_scancode import LicenseChecker
from scanner.copyright_checker import CopyrightChecker

LOG_PREFIX = "< file license/copyright check >"

# Define a dictionary of permissive licenses
PERMISSIVE_LICENSES = [
    "BSD-3-Clause",
    "MIT",
    "Apache-1.0",
    "Apache-1.1",
    "Apache-2.0",
    "BSD-3-Clause-Clear",
    "FreeBSD-DOC",
    "Zlib",
    "BSD-1-Clause",
    "BSD-2-Clause",
    "BSD-2-Clause-first-lines",
    "BSD-2-Clause-Views",
    "BSD-3-Clause-Sun",
    "BSD-4-Clause-Shortened",
    "BSD-3-Clause-Attribution",
    "BSD-4-Clause",
    "ISC",
    "CC0-1.0",
    "ICU",
    "LicenseRef-scancode-unicode"
]

COPYLEFT_LICENSES = [
    "GPL-1.0-only",
    "GPL-1.0-or-later",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only"
    "GPL-3.0",
    "GPL-3.0-or-later",
    "AGPL-3.0",
    "LGPL-3.0",
    "GPL-2.0",
    "GPL-2.0+",
    "GPL-2.0-only WITH Linux-syscall-note",
    "AGPL-1.0-only",
    "AGPL-1.0-or-later",
    "LicenseRef-scancode-agpl-2.0",
    "AGPL-3.0-only",
    "AGPL-3.0-or-later"
]

def get_license(repo_name: str) -> str:
    """
    Search for the repository name in the config file and return its license.
    the repository name is not found, return the default license (BSD-3-Clause-Clear).

    Args:
        repo_name (str): The name of the repository.

    Returns:
        str: The license of the repository.
    """
    for project in config.data['projects']:
        if repo_name.endswith(f"/{project['PROJECT_NAME']}") or repo_name == project['PROJECT_NAME']:
            return project['MARKINGS']
    # Return the default license if the repository name is not found
    return "BSD-3-Clause-Clear"


def beautify_output(flagged_files: dict, warning_files: dict, license: str, log_prefix: str) -> None:
    """
    Print the flagged files report in a beautified format.

    Args:
        flagged_files (dict): A dictionary of flagged files with blocking issues.
        warning_files (dict): A dictionary of files with warning issues (non-blocking).
        license (str) : The default/top level license of the repo
        log_prefix (str): The prefix to use for logging.
    """
    # Only show the report header if there are issues to report
    if not flagged_files and not warning_files:
        print(f"{log_prefix} ✅ No license or copyright issues detected")
        sys.exit(0)
    
    output = []
    output.append(f"{log_prefix} ┌───────────────────────────────────────────┐")
    output.append(f"{log_prefix} │           **Flagged Files Report**         │")
    output.append(f"{log_prefix} ├───────────────────────────────────────────┤")
    
    # Add COMPLIANCE.md reference
    output.append(f"{log_prefix} │")
    output.append(f"{log_prefix} │ 📖 For more information, see: COMPLIANCE.md")
    output.append(f"{log_prefix} │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md")
    output.append(f"{log_prefix} ├───────────────────────────────────────────┤")

    # Print blocking errors first
    if flagged_files:
        output.append(f"{log_prefix} │")
        output.append(f"{log_prefix} │ ═══════════════════════════════════════════")
        output.append(f"{log_prefix} │ 🚨  B L O C K I N G   E R R O R S")
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

    # Print warnings (non-blocking)
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

    # Print the entire output block
    print("\n".join(output))

    # Only exit with error if there are blocking issues
    if flagged_files:
        sys.exit(len(flagged_files))
    else:
        sys.exit(0)

def is_uncertain_license_issue(issue: str) -> bool:
    """
    Check if a license issue is ONLY related to uncertain/unknown licenses.
    Only treats it as a warning if the unknown license is the sole problem.
    If there are other incompatible licenses, it remains a blocking error.
    
    Special case: If the ONLY license is exactly "LicenseRef-scancode-proprietary-license",
    it's a blocking error. If mixed with other licenses, proceed with normal logic.
    
    Uncertain licenses (warnings) include:
    - LicenseRef-scancode-unknown-*
    - LicenseRef-scancode-warranty-*
    - LicenseRef-scancode-proprietary-* (when mixed with other uncertain licenses)
    - Any other LicenseRef-scancode-* that's not in the known permissive list
    
    Args:
        issue (str): The license issue string.
    
    Returns:
        bool: True if the issue is ONLY about uncertain licenses, False otherwise.
    """
    # Extract the license expression from the issue
    if "Incompatible license added:" in issue:
        license_expr = issue.split("Incompatible license added:")[1].strip()
    elif "License deleted:" in issue and "and license added:" in issue:
        # For license change issues, check the added license
        license_expr = issue.split("and license added:")[1].strip()
    else:
        # For other issue types, check if it contains LicenseRef-scancode
        return "LicenseRef-scancode-" in issue
    
    # Parse the license expression to check if ALL licenses are unknown/uncertain
    # Remove parentheses and split by AND/OR
    licenses = []
    for part in license_expr.replace('(', '').replace(')', '').split(' AND '):
        for lic in part.split(' OR '):
            lic = lic.strip()
            if lic:
                licenses.append(lic)
    
    # Check if all licenses are unknown/uncertain
    if not licenses:
        return False
    
    # SPECIAL CASE: If the ONLY license is exactly "LicenseRef-scancode-proprietary-license", block it
    if len(licenses) == 1 and licenses[0] == "LicenseRef-scancode-proprietary-license":
        return False
    
    # A license is considered uncertain if:
    # 1. It starts with LicenseRef-scancode- AND
    # 2. It's not in the known permissive list (like LicenseRef-scancode-unicode)
    def is_uncertain_license(lic: str) -> bool:
        if not lic.startswith('LicenseRef-scancode-'):
            return False
        # Check if it's a known permissive LicenseRef
        if lic in PERMISSIVE_LICENSES:
            return False
        return True
    
    # If ALL licenses are uncertain, it's a warning
    # If ANY license is a known incompatible license (like GPL), it's an error
    all_uncertain = all(is_uncertain_license(lic) for lic in licenses)
    
    return all_uncertain


def main() -> None:
    """
    The main function of the script.
    """
    # Clamp chatty logging from license_identifier
    logging.basicConfig(level=logging.WARNING)

    patch = Patch(sys.argv[1])
    repo_name = sys.argv[2]
    license = get_license(repo_name)
    if license in PERMISSIVE_LICENSES:
        allowed_licenses = PERMISSIVE_LICENSES
    elif license in COPYLEFT_LICENSES:
        allowed_licenses = COPYLEFT_LICENSES
    else:
        allowed_licenses = [license]

    license_checker = LicenseChecker(patch, repo_name, allowed_licenses)
    copyright_checker = CopyrightChecker(patch)

    flagged_license_files = license_checker.run()
    flagged_copyright_files = copyright_checker.run()

    # Combine flagged files and their issues, separating errors from warnings
    flagged_files = {}  # Blocking errors
    warning_files = {}  # Non-blocking warnings
    
    for file, issues in flagged_license_files.items():
        # Separate uncertain license issues (warnings) from real errors
        error_issues = [issue for issue in issues if not is_uncertain_license_issue(issue)]
        warning_issues = [issue for issue in issues if is_uncertain_license_issue(issue)]
        
        if error_issues:
            flagged_files[file] = {'license_issues': error_issues, 'copyright_issues': []}
        if warning_issues:
            warning_files[file] = {'license_issues': warning_issues, 'copyright_issues': []}
    
    for file, issues in flagged_copyright_files.items():
        if file in flagged_files:
            flagged_files[file]['copyright_issues'] = issues
        else:
            flagged_files[file] = {'license_issues': [], 'copyright_issues': issues}

    beautify_output(flagged_files, warning_files, license, LOG_PREFIX)

if __name__ == '__main__':
    main()

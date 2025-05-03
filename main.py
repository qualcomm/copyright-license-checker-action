import logging
import sys
import config
from patch import Patch
from license_scancode import LicenseChecker
from copyright_checker import CopyrightChecker

LOG_PREFIX = "< file license/copyright check >"

# Define a dictionary of permissive licenses
PERMISSIVE_LICENSES = [
    "BSD-3-Clause",
    "MIT",
    "Apache-2.0",
    "BSD-3-Clause-Clear",
]

COPIRIGHT_LICENSES = [
    "GPL-3.0",
    "AGPL-3.0",
    "LGPL-3.0",
    "GPL-2.0",
    "GPL-2.0+",
    "GPL-2.0-only WITH Linux-syscall-note",
    "GPL-2.0-only",
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
        if project['PROJECT_NAME'] == repo_name:
            return project['MARKINGS']
    # Return the default license if the repository name is not found
    return "BSD-3-Clause-Clear"


def beautify_output(flagged_files: dict, log_prefix: str) -> None:
    """
    Print the flagged files report in a beautified format.

    Args:
        flagged_files (dict): A dictionary of flagged files and their issues.
        log_prefix (str): The prefix to use for logging.
    """
    output = []
    output.append(f"{log_prefix} ┌───────────────────────────────────────────┐")
    output.append(f"{log_prefix} │           **Flagged Files Report**         │")
    output.append(f"{log_prefix} ├───────────────────────────────────────────┤")
    for file, issues in flagged_files.items():
        output.append(f"{log_prefix} │ 📄 **File:** {file}")
        if issues['license_issues']:
            output.append(f"{log_prefix} │ 🚨 **License issues detected:**")
            for issue in issues['license_issues']:
                output.append(f"{log_prefix} │   - {issue}")
        if issues['copyright_issues']:
            output.append(f"{log_prefix} │ ⚠️ **Copyright issues detected:**")
            for issue in issues['copyright_issues']:
                output.append(f"{log_prefix} │   - {issue}")
        if not issues['license_issues'] and not issues['copyright_issues']:
            output.append(f"{log_prefix} │ ✅ **No issues detected**")
    output.append(f"{log_prefix} └───────────────────────────────────────────┘")

    # Print the entire output block
    print("\n".join(output))

    sys.exit(len(flagged_files))

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
    elif license in COPIRIGHT_LICENSES:
        allowed_licenses = COPIRIGHT_LICENSES
    else:
        allowed_licenses = []

    license_checker = LicenseChecker(patch, repo_name, allowed_licenses)
    copyright_checker = CopyrightChecker(patch)

    flagged_license_files = license_checker.run()
    flagged_copyright_files = copyright_checker.run()

    # Combine flagged files and their issues
    flagged_files = {}
    for file, issues in flagged_license_files.items():
        flagged_files[file] = {'license_issues': issues, 'copyright_issues': []}
    for file, issues in flagged_copyright_files.items():
        if file in flagged_files:
            flagged_files[file]['copyright_issues'] = issues
        else:
            flagged_files[file] = {'license_issues': [], 'copyright_issues': issues}

    beautify_output(flagged_files, LOG_PREFIX)

if __name__ == '__main__':
    main()

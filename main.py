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
        if project['PROJECT_NAME'] == repo_name:
            return project['MARKINGS']
    # Return the default license if the repository name is not found
    return "BSD-3-Clause-Clear"


def beautify_output(flagged_files: dict, license: str, log_prefix: str) -> None:
    """
    Print the flagged files report in a beautified format.

    Args:
        flagged_files (dict): A dictionary of flagged files and their issues.
        license (str) : The default/top level license of the repo
        log_prefix (str): The prefix to use for logging.
    """
    output = []
    output.append(f"{log_prefix} ┌───────────────────────────────────────────┐")
    output.append(f"{log_prefix} │           **Flagged Files Report**         │")
    output.append(f"{log_prefix} ├───────────────────────────────────────────┤")
    output.append(f"{log_prefix} │ Top level/default license of the repo is {license}")
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
    elif license in COPYLEFT_LICENSES:
        allowed_licenses = COPYLEFT_LICENSES
    else:
        allowed_licenses = [license]

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

    beautify_output(flagged_files, license, LOG_PREFIX)

if __name__ == '__main__':
    main()

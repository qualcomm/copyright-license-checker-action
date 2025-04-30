import json
import logging
import sys
import os
from dotenv import load_dotenv
import config
from patch import Patch
from license_scancode import LicenseChecker
from copyright_checker import CopyrightChecker

log_prefix = "< file license/copyright check >"


# Define a dictionary of permissive licenses

permissive_licenses = ["BSD-3-Clause", "MIT", "Apache-2.0", "BSD-3-Clause-Clear"]
copyleft_licenses = ["GPL-3.0", "AGPL-3.0", "LGPL-3.0", "GPL-2.0", "GPL-2.0+"]


# def get_license(repo_name):
#     # Load the JSON data from the file
#     with open('config.json', 'r') as file:
#         data = json.load(file)

#     # Search for the repository name and print its license
#     for project in data['projects']:
#         if project['PROJECT_NAME'] == repo_name:
#             return project['MARKINGS']
#     return None

def get_license(repo_name):
    # Search for the repository name and return its license
    for project in config.data['projects']:
        if project['PROJECT_NAME'] == repo_name:
            return project['MARKINGS']
    return None


def main():
    # clamp chatty logging from license_identifier
    logging.basicConfig(level=logging.WARNING)

    load_dotenv()

    patch = Patch(sys.argv[1])
    #TODO repo_name will be sent by the GH action event
    repo_name = "meta-qcom-robotics" # Testing
    #repo_name = os.environ['PROJECT_NAME']
    license = get_license(repo_name)
    if license in permissive_licenses:
        allowed_licenses = permissive_licenses
    elif license in copyleft_licenses:
        allowed_licenses = copyleft_licenses
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

    # Print results
    for file, issues in flagged_files.items():
        print(f"{log_prefix} {file}")
        if issues['license_issues']:
            print(f"{log_prefix} - License issues detected:")
            for issue in issues['license_issues']:
                print(f"{log_prefix}   - {issue}")
        if issues['copyright_issues']:
            print(f"{log_prefix} - Copyright issues detected:")
            for issue in issues['copyright_issues']:
                print(f"{log_prefix}   - {issue}")
        if not issues['license_issues'] and not issues['copyright_issues']:
            print(f"{log_prefix} - No issues detected")

    sys.exit(len(flagged_files))

if __name__ == '__main__':
    main()

import argparse
import logging
import sys
import os
import json
import subprocess
import tempfile
from scanner import config
from scanner.copyright_checker import CopyrightChecker
from scanner.license_scancode import LicenseChecker
from scanner.licenses import (
    COPYLEFT_LICENSES,
    PERMISSIVE_LICENSES,
    is_copyleft,
    split_license_components,
)
from scanner.patch import Patch

LOG_PREFIX = "< file license/copyright check >"


def detect_license_from_file(license_file_path: str) -> str:
    """
    Detect the license from a LICENSE file using scancode.

    Args:
        license_file_path (str): Path to the LICENSE file.

    Returns:
        str: The detected SPDX license identifier, or None if detection fails.
    """
    if not os.path.exists(license_file_path):
        return None

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = os.path.join(tmpdir, "scancode_results.json")

            # Run scancode on the LICENSE file
            subprocess.run(
                [
                    "scancode",
                    "--license",
                    "--strip-root",
                    "--quiet",
                    "--json-pp",
                    output_file,
                    license_file_path,
                ],
                check=True,
                capture_output=True,
            )

            # Parse the results
            with open(output_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Extract the license from the first file result
            for file_result in data.get("files", []):
                if file_result["type"] == "file":
                    license_detections = file_result.get("license_detections", [])
                    if license_detections:
                        # Return the SPDX license expression
                        return license_detections[0].get("license_expression_spdx", None)

            return None
    # TODO: pylint wants specific exception types here (broad-exception-caught).
    # subprocess failures, JSON parse errors, and missing keys are all handled
    # identically (log + return None), so a broad catch is deliberate; revisit
    # if scancode's failure modes are enumerated more precisely later.
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"Warning: Failed to detect license from {license_file_path}: {e}")
        return None


def get_license(repo_name: str) -> str:
    """
    Detect the license from the project's LICENSE file.
    Falls back to config file lookup if LICENSE file is not found or detection fails.
    If neither works, returns the default license (BSD-3-Clause-Clear).

    Args:
        repo_name (str): The name of the repository.

    Returns:
        str: The license of the repository.
    """
    # Try to find and read LICENSE file in current directory
    # Include both lowercase and uppercase variations for case-insensitive matching
    license_file_candidates = [
        "LICENSE",
        "LICENSE.txt",
        "LICENSE.TXT",
        "LICENSE.md",
        "LICENSE.MD",
        "COPYING",
        "COPYING.txt",
        "COPYING.TXT",
        "License",
        "License.txt",
        "License.md",
    ]

    detected_license = None
    for license_file in license_file_candidates:
        license_path = os.path.join(os.getcwd(), license_file)
        if os.path.exists(license_path):
            print(f"{LOG_PREFIX} Found license file: {license_file}")
            detected_license = detect_license_from_file(license_path)
            if detected_license:
                print(f"{LOG_PREFIX} Detected license: {detected_license}")
                # If detected license contains "bsd" (case-insensitive), treat as default license
                if "bsd" in detected_license.lower():
                    print(f"{LOG_PREFIX} License contains 'bsd', using default: BSD-3-Clause-Clear")
                    return "BSD-3-Clause-Clear"
                return detected_license
            break

    # Fallback to config file lookup
    print(f"{LOG_PREFIX} License file not found or detection failed, checking config...")
    for project in config.data["projects"]:
        if (
            repo_name.endswith(f"/{project['PROJECT_NAME']}")
            or repo_name == project["PROJECT_NAME"]
        ):
            print(f"{LOG_PREFIX} Using license from config: {project['MARKINGS']}")
            return project["MARKINGS"]

    # Return the default license if nothing else works
    print(f"{LOG_PREFIX} Using default license: BSD-3-Clause-Clear")
    return "BSD-3-Clause-Clear"


def _render_issue_section(output: list, log_prefix: str, files: dict, labels: dict) -> None:
    """
    Append one report section (blocking errors or warnings) to output.

    Args:
        output (list): The report's lines so far; appended to in place.
        log_prefix (str): The prefix to use for logging.
        files (dict): path -> {"license_issues": [...], "copyright_issues": [...]}.
        labels (dict): "title" (section header line), "license" and
            "copyright" (the per-file "├─ ..." lines introducing each issue
            type).
    """
    if not files:
        return
    output.append(f"{log_prefix} │")
    output.append(f"{log_prefix} │ ═══════════════════════════════════════════")
    output.append(f"{log_prefix} │ {labels['title']}")
    output.append(f"{log_prefix} │ ═══════════════════════════════════════════")
    for file, issues in files.items():
        output.append(f"{log_prefix} │")
        output.append(f"{log_prefix} │ ┌─ 📄 F I L E: {file}")
        if issues["license_issues"]:
            output.append(f"{log_prefix} │ │")
            output.append(f"{log_prefix} │ ├─ {labels['license']}")
            for issue in issues["license_issues"]:
                output.append(f"{log_prefix} │ │  • {issue}")
        if issues["copyright_issues"]:
            output.append(f"{log_prefix} │ │")
            output.append(f"{log_prefix} │ ├─ {labels['copyright']}")
            for issue in issues["copyright_issues"]:
                output.append(f"{log_prefix} │ │  • {issue}")
        output.append(f"{log_prefix} │ └─────────────────────────────────────────")


def beautify_output(
    flagged_files: dict, warning_files: dict, _license: str, log_prefix: str
) -> None:
    """
    Print the flagged files report in a beautified format.

    Purely a rendering concern: it prints the report and returns. The
    process exit code is decided by the caller, based on whether
    flagged_files is non-empty (see main()).

    Args:
        flagged_files (dict): A dictionary of flagged files with blocking issues.
        warning_files (dict): A dictionary of files with warning issues (non-blocking).
        _license (str) : The default/top level license of the repo (currently unused
            by the report body, kept for a stable call signature).
        log_prefix (str): The prefix to use for logging.
    """
    # Only show the report header if there are issues to report
    if not flagged_files and not warning_files:
        print(f"{log_prefix} ✅ No license or copyright issues detected")
        return

    output = []
    output.append(f"{log_prefix} ┌───────────────────────────────────────────┐")
    output.append(f"{log_prefix} │           **Flagged Files Report**         │")
    output.append(f"{log_prefix} ├───────────────────────────────────────────┤")

    # Add COMPLIANCE.md reference
    output.append(f"{log_prefix} │")
    output.append(f"{log_prefix} │ 📖 For more information, see: COMPLIANCE.md")
    output.append(
        f"{log_prefix} │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md"  # noqa: E501
    )
    output.append(f"{log_prefix} ├───────────────────────────────────────────┤")

    _render_issue_section(
        output,
        log_prefix,
        flagged_files,
        {
            "title": "🚨  B L O C K I N G   E R R O R S",
            "license": "🚨 LICENSE ISSUES:",
            "copyright": "🚨 COPYRIGHT ISSUES:",
        },
    )
    _render_issue_section(
        output,
        log_prefix,
        warning_files,
        {
            "title": "⚠️   W A R N I N G S  (Non-blocking)",
            "license": "⚠️  LICENSE WARNINGS:",
            "copyright": "⚠️  COPYRIGHT WARNINGS:",
        },
    )

    output.append(f"{log_prefix} └───────────────────────────────────────────┘")

    # Print the entire output block
    print("\n".join(output))


def parse_args(argv: list) -> argparse.Namespace:
    """Parse the patch path and repository name from command-line arguments."""
    parser = argparse.ArgumentParser(description="Copyright and license compliance checker.")
    parser.add_argument("patch_file", help="Path to the patch file to check.")
    parser.add_argument("repo_name", help="The name of the GitHub repository.")
    return parser.parse_args(argv)


def resolve_allowed_licenses(repo_name: str) -> tuple:
    """
    Resolve the repository license and allowed-license baseline.

    Args:
        repo_name (str): The name of the repository.

    Returns:
        tuple: (repo_license, allowed_licenses).
    """
    repo_license = get_license(repo_name)
    if repo_license in PERMISSIVE_LICENSES:
        return repo_license, PERMISSIVE_LICENSES
    if is_copyleft(repo_license):
        return repo_license, COPYLEFT_LICENSES

    # Handle complex license expressions (e.g., "GPL-2.0-only AND GPL-2.0-or-later")
    allowed_licenses = split_license_components(repo_license)
    return repo_license, allowed_licenses or [repo_license]


def _route_issues(
    flagged_license_files: dict, warning_license_files: dict, flagged_copyright_files: dict
) -> tuple:
    """
    Combine license and copyright checker output into report dictionaries.

    Args:
        flagged_license_files (dict): path -> blocking license issues.
        warning_license_files (dict): path -> non-blocking license issues.
        flagged_copyright_files (dict): path -> blocking copyright issues.

    Returns:
        tuple: (flagged_files, warning_files), each mapping
            path -> {"license_issues": [...], "copyright_issues": [...]}.
    """
    flagged_files = {}
    warning_files = {}

    for file, issues in warning_license_files.items():
        warning_files[file] = {"license_issues": list(issues), "copyright_issues": []}

    for file, issues in flagged_license_files.items():
        flagged_files[file] = {"license_issues": list(issues), "copyright_issues": []}

    for file, issues in flagged_copyright_files.items():
        if file in flagged_files:
            flagged_files[file]["copyright_issues"] = issues
        else:
            flagged_files[file] = {"license_issues": [], "copyright_issues": issues}

    return flagged_files, warning_files


def main() -> None:
    """
    The main function of the script.
    """
    # Clamp chatty logging from license_identifier
    logging.basicConfig(level=logging.WARNING)

    args = parse_args(sys.argv[1:])
    patch = Patch(args.patch_file)
    repo_name = args.repo_name
    repo_license, allowed_licenses = resolve_allowed_licenses(repo_name)

    license_checker = LicenseChecker(patch, allowed_licenses)
    copyright_checker = CopyrightChecker(patch)

    flagged_license_files, warning_license_files = license_checker.run()
    flagged_copyright_files = copyright_checker.run()
    flagged_files, warning_files = _route_issues(
        flagged_license_files, warning_license_files, flagged_copyright_files
    )

    beautify_output(flagged_files, warning_files, repo_license, LOG_PREFIX)

    sys.exit(1 if flagged_files else 0)


if __name__ == "__main__":
    main()

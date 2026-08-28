"""
Module to check for licenses in a patch file using scancode.
"""

import json
import tempfile
import subprocess
import warnings
import os
from pathlib import Path

from scanner.file_types import SOURCE_EXTENSIONS
from scanner.licenses import is_license_allowed, is_uncertain_expression
from scanner.patch import Patch

warnings.filterwarnings("ignore", message="Libmagic magic database not found")


def _route_license_message(
    message: str, expression: str, issues: list, warning_messages: list
) -> None:
    """Append message to warning_messages if expression is uncertain, otherwise to issues."""
    target = warning_messages if is_uncertain_expression(expression) else issues
    target.append(message)


class LicenseChecker:
    """
    Class to check for licenses in a patch file.
    """

    def __init__(self, patch: Patch, permissive_licenses: list) -> None:
        """
        Initialize the LicenseChecker object.

        Args:
            patch (Patch): The patch file to check.
            permissive_licenses (list): A list of permissive licenses.
        """
        self.patch = patch
        self.permissive_licenses = permissive_licenses

    # TODO: exceeds team max-complexity=10 and local-variable count (batches
    # added/deleted line groups across all changes into one scancode
    # invocation; revisit extraction if proprietary mode needs to change how
    # content is batched).
    def detect_licenses_batch(self, changes: list) -> dict:  # noqa: C901
        # pylint: disable=too-many-locals
        """
        Detect licenses for multiple changes in a single scancode run.
        Args:
            changes (list): List of changes to check.
        Returns:
            dict: Dictionary mapping (change_index, content_type) -> licenses.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            file_map = {}

            for idx, change in enumerate(changes):
                content = change["content"]
                # Check if content is None
                if not content:
                    continue

                added_lines = []
                deleted_lines = []
                # Separate added and deleted lines
                for line in content.split("\n"):
                    if line.startswith("+"):
                        added_lines.append(line[1:])
                    elif line.startswith("-"):
                        deleted_lines.append(line[1:])

                # Join added and deleted lines as-is
                if added_lines:
                    added_file = f"{idx}_added.txt"
                    Path(tmpdir, added_file).write_text("\n".join(added_lines), encoding="utf-8")
                    file_map[added_file] = (idx, "added")

                if deleted_lines:
                    deleted_file = f"{idx}_deleted.txt"
                    Path(tmpdir, deleted_file).write_text(
                        "\n".join(deleted_lines), encoding="utf-8"
                    )
                    file_map[deleted_file] = (idx, "deleted")

            if not file_map:
                return {}

            output_file = os.path.join(tmpdir, "scancode_results.json")
            subprocess.run(
                [
                    "scancode",
                    "--license",
                    "--strip-root",
                    "--quiet",
                    "--json-pp",
                    output_file,
                    tmpdir,
                ],
                check=True,
            )

            with open(output_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            results = {}
            for file_result in data.get("files", []):
                if file_result["type"] != "file":
                    continue

                filename = os.path.basename(file_result["path"])
                if filename not in file_map:
                    continue

                licenses = ""
                if len(file_result.get("license_detections", [])):
                    licenses = file_result["license_detections"][0]["license_expression_spdx"]

                change_idx, content_type = file_map[filename]
                results[(change_idx, content_type)] = licenses

            return results

    def is_source_file(self, file_name: str) -> bool:
        """
        Check if a file is a source file.

        Args:
            file_name (str): The file name.

        Returns:
            bool: True if the file is a source file, False otherwise.
        """
        return file_name.endswith(SOURCE_EXTENSIONS)

    def run(self) -> tuple:
        """
        Run the license checker.

        Returns:
            tuple: Dictionaries of blocking files and warning files.
        """
        source_files = [change for change in self.patch.changes if change["file_type"] == "source"]

        flagged_files = {}
        warning_files = {}
        if not source_files:
            return flagged_files, warning_files

        license_results = self.detect_licenses_batch(source_files)

        for idx, change in enumerate(source_files):
            added_licenses = license_results.get((idx, "added"), "")
            deleted_licenses = license_results.get((idx, "deleted"), "")

            if change["change_type"] in ("MODIFIED", "RENAMED_MODIFIED", "ADDED"):
                self._classify_license_change(
                    change,
                    {"added": added_licenses, "deleted": deleted_licenses},
                    flagged_files,
                    warning_files,
                )
            if change["change_type"] == "ADDED":
                no_license_message = self._check_new_file_license(change, added_licenses)
                if no_license_message:
                    flagged_files.setdefault(change["path_name"], []).append(no_license_message)
        return flagged_files, warning_files

    def _check_new_file_license(self, change: dict, added_licenses: str) -> str | None:
        """
        Check a newly-added source file for a missing license.

        Args:
            change (dict): The ADDED change under consideration.
            added_licenses (str): SPDX expression detected on the added lines.

        Returns:
            str | None: A blocking message if the file has no detected
                license; None if the file should raise no issue.
        """
        if added_licenses or not self.is_source_file(change["path_name"]):
            return None
        return f"No license added for source file: {change['path_name']}"

    def _classify_license_change(
        self, change: dict, license_info: dict, flagged_files: dict, warning_files: dict
    ) -> None:
        """
        Classify a MODIFIED/ADDED change's license issues into output buckets.

        Args:
            change (dict): The source change being evaluated.
            license_info (dict): Detected "added" and "deleted" SPDX expressions.
            flagged_files (dict): Blocking result bucket to update in place.
            warning_files (dict): Warning result bucket to update in place.
        """
        added_licenses = license_info["added"]
        deleted_licenses = license_info["deleted"]

        issues = []
        warnings_for_file = []
        if added_licenses and deleted_licenses and added_licenses != deleted_licenses:
            # Only flag if the new license is NOT allowed. This allows dual-license
            # scenarios like "BSD-3-Clause OR GPL-2.0-only" where at least one option
            # is allowed.
            if not is_license_allowed(added_licenses, self.permissive_licenses):
                message = (
                    f"License deleted: {deleted_licenses} and license added: " f"{added_licenses}"
                )
                _route_license_message(message, added_licenses, issues, warnings_for_file)
        elif added_licenses and not is_license_allowed(added_licenses, self.permissive_licenses):
            # New license added that is not allowed
            message = f"Incompatible license added: {added_licenses}"
            _route_license_message(message, added_licenses, issues, warnings_for_file)
        elif deleted_licenses and not added_licenses:
            # Preserve the legacy main.py fallback for deletion-only messages:
            # any ScanCode reference makes the issue a warning.
            message = f"License deleted: {deleted_licenses}"
            target = warnings_for_file if "LicenseRef-scancode-" in deleted_licenses else issues
            target.append(message)

        if issues:
            flagged_files[change["path_name"]] = issues
        if warnings_for_file:
            warning_files[change["path_name"]] = warnings_for_file

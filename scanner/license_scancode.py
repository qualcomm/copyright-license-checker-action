"""
Module to check for licenses in a patch file using scancode.
"""

import json
import tempfile
import subprocess
import warnings
import os
from pathlib import Path

from scanner.copyright_checker import DEFAULT_INTERNAL_ENTITIES, has_internal_copyright
from scanner.file_types import SOURCE_EXTENSIONS
from scanner.licenses import (
    PROPRIETARY_LICENSE,
    is_license_allowed,
    is_permissive,
    is_uncertain_expression,
    split_license_components,
)
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

    def __init__(
        self,
        patch: Patch,
        permissive_licenses: list,
        mode: str = "opensource",
        proprietary_entities: list | None = None,
    ) -> None:
        """
        Initialize the LicenseChecker object.

        Args:
            patch (Patch): The patch file to check.
            permissive_licenses (list): A list of permissive licenses.
            mode (str): "opensource" (default) or "proprietary".
            proprietary_entities (list): Copyright-holder substrings treated
                as internal authorship in proprietary mode.
        """
        self.patch = patch
        self.permissive_licenses = permissive_licenses
        self.mode = mode
        self.proprietary_entities = (
            proprietary_entities if proprietary_entities is not None else DEFAULT_INTERNAL_ENTITIES
        )

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

        In "opensource" mode, behavior is unchanged from before mode support
        existed. In "proprietary" mode:

        - Removing a proprietary marking is a blocking error.
        - Adding permissive OSS warns with a NOTICE reminder.
        - A solitary proprietary marker is expected and silent unless it
          replaces a real license.
        - New source files with an internal copyright do not need an OSS
          license header.

        Returns:
            tuple: Dictionaries of blocking files and warning files.
        """
        source_files = [change for change in self.patch.changes if change["file_type"] == "source"]

        flagged_files = {}
        warning_files = {}
        if not source_files:
            return flagged_files, warning_files

        license_results = self.detect_licenses_batch(source_files)
        proprietary = self.mode == "proprietary"

        for idx, change in enumerate(source_files):
            added_licenses = license_results.get((idx, "added"), "")
            deleted_licenses = license_results.get((idx, "deleted"), "")

            proprietary_removed = proprietary and self._proprietary_marking_removed(
                added_licenses, deleted_licenses
            )

            if proprietary and self._is_expected_internal_marking(added_licenses, deleted_licenses):
                continue

            if change["change_type"] in ("MODIFIED", "RENAMED_MODIFIED", "ADDED"):
                self._classify_license_change(
                    change,
                    {
                        "added": added_licenses,
                        "deleted": deleted_licenses,
                        "proprietary": proprietary,
                        "proprietary_removed": proprietary_removed,
                    },
                    flagged_files,
                    warning_files,
                )
            if change["change_type"] == "ADDED":
                no_license_message = self._check_new_file_license(
                    change, added_licenses, proprietary
                )
                if no_license_message:
                    flagged_files.setdefault(change["path_name"], []).append(no_license_message)
        return flagged_files, warning_files

    def _check_new_file_license(
        self, change: dict, added_licenses: str, proprietary: bool
    ) -> str | None:
        """
        Check a newly-added source file for a missing license.

        Args:
            change (dict): The ADDED change under consideration.
            added_licenses (str): SPDX expression detected on the added lines.
            proprietary (bool): Whether the checker is running in proprietary mode.

        Returns:
            str | None: A blocking message if the file has no detected
                license; None if the file should raise no issue.
        """
        if added_licenses or not self.is_source_file(change["path_name"]):
            return None
        if proprietary and has_internal_copyright(change["content"], self.proprietary_entities):
            return None
        return self._no_license_message(change["path_name"], proprietary)

    def _classify_license_change(
        self, change: dict, license_info: dict, flagged_files: dict, warning_files: dict
    ) -> None:
        # pylint: disable=too-many-locals
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
        proprietary = license_info["proprietary"]
        proprietary_removed = license_info["proprietary_removed"]

        added_for_permissiveness = (
            self._without_proprietary_marker(added_licenses) if proprietary else added_licenses
        )
        added_is_allowed = bool(added_for_permissiveness) and is_license_allowed(
            added_for_permissiveness, self.permissive_licenses
        )

        issues = []
        warnings_for_file = []
        if proprietary_removed:
            issues.append(self._proprietary_removed_message(deleted_licenses))
        elif proprietary and deleted_licenses and added_licenses == PROPRIETARY_LICENSE:
            issues.append(self._relicensed_as_proprietary_message(deleted_licenses))
        elif added_licenses and deleted_licenses and added_licenses != deleted_licenses:
            # Only flag if the new license is NOT allowed. This allows dual-license
            # scenarios like "Example-Permissive-3-Clause OR
            # Example-Copy-Left-2.0-only" where at least one option is allowed.
            if not added_is_allowed:
                message = (
                    f"License deleted: {deleted_licenses} and license added: " f"{added_licenses}"
                )
                _route_license_message(message, added_licenses, issues, warnings_for_file)
        elif added_licenses and not added_is_allowed:
            # New license added that is not allowed
            message = f"Incompatible license added: {added_licenses}"
            _route_license_message(message, added_licenses, issues, warnings_for_file)
        elif deleted_licenses and not added_licenses:
            # Preserve the legacy main.py fallback for deletion-only messages:
            # any ScanCode reference makes the issue a warning.
            message = f"License deleted: {deleted_licenses}"
            target = (
                warnings_for_file
                if not proprietary and "LicenseRef-scancode-" in deleted_licenses
                else issues
            )
            target.append(message)

        license_is_new_or_changed = added_licenses and added_licenses != deleted_licenses
        if (
            proprietary
            and not proprietary_removed
            and license_is_new_or_changed
            and added_for_permissiveness
            and is_permissive(added_for_permissiveness)
        ):
            warning_files.setdefault(change["path_name"], []).append(
                self._notice_reminder(added_licenses)
            )

        if issues:
            flagged_files[change["path_name"]] = issues
        if warnings_for_file:
            warning_files.setdefault(change["path_name"], []).extend(warnings_for_file)

    @staticmethod
    def _without_proprietary_marker(expression: str) -> str:
        """Return expression components without the proprietary marker."""
        remaining = [
            license_id
            for license_id in split_license_components(expression)
            if license_id != PROPRIETARY_LICENSE
        ]
        return " AND ".join(remaining)

    @classmethod
    def _is_expected_internal_marking(cls, added_licenses: str, deleted_licenses: str) -> bool:
        """Return whether a change only adds or reformats an internal proprietary marker."""
        if added_licenses != PROPRIETARY_LICENSE:
            return False
        return not cls._without_proprietary_marker(deleted_licenses)

    @staticmethod
    def _proprietary_marking_removed(added_licenses: str, deleted_licenses: str) -> bool:
        """Return whether the proprietary marker was removed from the expression."""
        deleted_components = split_license_components(deleted_licenses)
        if PROPRIETARY_LICENSE not in deleted_components:
            return False
        return PROPRIETARY_LICENSE not in split_license_components(added_licenses)

    @staticmethod
    def _proprietary_removed_message(deleted_licenses: str) -> str:
        """Build the blocking message for a removed proprietary marking."""
        return (
            f"Proprietary license statement removed: {deleted_licenses} -- removing a "
            "proprietary rights statement requires review; restore it, or route the "
            "change to the scan team/legal if the file's status has genuinely changed."
        )

    @staticmethod
    def _relicensed_as_proprietary_message(deleted_licenses: str) -> str:
        """Build the blocking message for a real license replaced by a proprietary marker."""
        return (
            f"License deleted: {deleted_licenses} and license added: {PROPRIETARY_LICENSE} -- "
            "a permissive license's attribution terms are not extinguished by marking the "
            "file proprietary; restore the deleted license, or route the change to the scan "
            "team/legal if the file's licensing has genuinely changed."
        )

    @staticmethod
    def _notice_reminder(added_licenses: str) -> str:
        """Build the proprietary-mode warning for a permissive OSS addition."""
        return (
            f"Permissive open-source license added: {added_licenses} -- review that this "
            "third-party code is approved for inclusion, and update the repo's NOTICE file "
            "with the required attribution."
        )

    @staticmethod
    def _no_license_message(path_name: str, proprietary: bool) -> str:
        """Build the no-license message for the active mode."""
        if not proprietary:
            return f"No license added for source file: {path_name}"
        return (
            f"No license or internal copyright found for source file: {path_name} -- "
            "if this is third-party code, do NOT add a Qualcomm copyright; route it to "
            "the scan team/legal for review. If this is Qualcomm-authored code, add the "
            "appropriate copyright marking."
        )

import re
from scanner.patch import Patch

"""
Module to check for copyright changes in a patch file.
"""

class CopyrightChecker:
    """
    Class to check for copyright changes in a patch file.
    """

    def __init__(self, patch: Patch) -> None:
        """
        Initialize the CopyrightChecker object.

        Args:
            patch (Patch): The patch file to check.
        """
        self.patch = patch

    def normalize_string(self, s: str) -> str:
        """
        Normalize a string by removing non-alphabetic characters.

        Args:
            s (str): The string to normalize.

        Returns:
            str: The normalized string.
        """
        return ''.join(filter(str.isalpha, s))

    def _check_allowed_transitions(self, deleted_copyrights_set: dict, 
                                   added_copyrights: list) -> set:
        """
        Check for allowed copyright transitions that should not be flagged.

        Args:
            deleted_copyrights_set (dict): Dictionary of normalized deleted copyrights.
            added_copyrights (list): List of tuples (original, normalized) added copyrights.

        Returns:
            set: Set of normalized deleted copyrights that are part of allowed transitions.
        """
        allowed = set()
        
        # Define the allowed transition patterns
        deleted_pattern = "Qualcomm Innovation Center, Inc. All rights"
        added_pattern = "Qualcomm Technologies, Inc. and/or its subsidiaries"
        
        # Check if any added copyright contains the allowed pattern
        has_allowed_addition = any(
            added_pattern in original for original, _ in added_copyrights
        )
        
        if has_allowed_addition:
            # Check deleted copyrights for the pattern
            for normalized, original in deleted_copyrights_set.items():
                if deleted_pattern in original:
                    allowed.add(normalized)
        
        return allowed

    def detect_copyright_changes(self, content: str) -> tuple:
        """
        Detect copyright changes in the content.

        Args:
            content (str): The content to check.

        Returns:
            tuple: A tuple of added and deleted copyrights.
        """
        if not isinstance(content, (str, bytes)):
            return [], []

        added_copyrights = [
            (line[1:], self.normalize_string(line[1:]))
            for line in re.findall(r"^\+.*Copyright.*", content, re.MULTILINE)
        ]
        deleted_copyrights = [
            (line[1:], self.normalize_string(line[1:]))
            for line in re.findall(r"^-.*Copyright.*", content, re.MULTILINE)
        ]
        return added_copyrights, deleted_copyrights

    def run(self) -> dict:
        """
        Run the copyright checker.

        Returns:
            dict: A dictionary of flagged files.
        """
        source_files = [
            change for change in self.patch.changes
            if change['file_type'] == 'source'
        ]

        flagged_files = {}
        for change in source_files:
            added_copyrights, deleted_copyrights = self.detect_copyright_changes(change['content'])

            issues = []
            if change['change_type'] == 'MODIFIED':
                added_copyrights_set = {
                    normalized: original for original, normalized in added_copyrights
                }
                deleted_copyrights_set = {
                    normalized: original for original, normalized in deleted_copyrights
                }

                flagged_changes = set()

                if deleted_copyrights_set:
                    flagged_changes = deleted_copyrights_set.keys() - added_copyrights_set.keys()

                # Filter out allowed copyright transitions
                if flagged_changes:
                    allowed_transitions = self._check_allowed_transitions(
                        deleted_copyrights_set, added_copyrights
                    )
                    flagged_changes = flagged_changes - allowed_transitions

                if flagged_changes:
                    original_flagged_changes = [
                        deleted_copyrights_set[change] for change in flagged_changes
                    ]
                    issues.append(f"Copyright deletions detected: {original_flagged_changes}")
                if issues:
                    flagged_files[change['path_name']] = issues

        return flagged_files

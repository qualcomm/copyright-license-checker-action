import re

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

    def detect_copyright_changes(self, content: str) -> tuple:
        """
        Detect copyright changes in the content.

        Args:
            content (str): The content to check.

        Returns:
            tuple: A tuple of added and deleted copyrights.
        """
        added_copyrights = [(line[1:], self.normalize_string(line[1:])) for line in re.findall(r"^\+.*Copyright.*", content, re.MULTILINE)]
        deleted_copyrights = [(line[1:], self.normalize_string(line[1:])) for line in re.findall(r"^-.*Copyright.*", content, re.MULTILINE)]
        return added_copyrights, deleted_copyrights

    def run(self) -> dict:
        """
        Run the copyright checker.

        Returns:
            dict: A dictionary of flagged files.
        """
        source_files = [change for change in self.patch.changes
                        if change['file_type'] == 'source']

        flagged_files = {}
        for change in source_files:
            added_copyrights, deleted_copyrights = self.detect_copyright_changes(change['content'])

            issues = []
            if change['change_type'] == 'MODIFIED':
                added_copyrights_set = {normalized: original for original, normalized in added_copyrights}
                deleted_copyrights_set = {normalized: original for original, normalized in deleted_copyrights}

                flagged_changes = set()

                if deleted_copyrights_set:
                    flagged_changes = deleted_copyrights_set.keys() - added_copyrights_set.keys()

                if flagged_changes:
                    original_flagged_changes = [deleted_copyrights_set[change] for change in flagged_changes]
                    issues.append(f"Copyright deletions detected: {original_flagged_changes}")
                if issues:
                    flagged_files[change['path_name']] = issues
                else:
                    pass

        return flagged_files

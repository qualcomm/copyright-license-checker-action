import re

class CopyrightChecker:
    def __init__(self, patch):
        self.patch = patch

    def normalize_string(self, s):
        return ''.join(filter(str.isalpha, s))

    def detect_copyright_changes(self, content):
        added_copyrights = [(line[1:], self.normalize_string(line[1:])) for line in re.findall(r"^\+.*Copyright.*", content, re.MULTILINE)]
        deleted_copyrights = [(line[1:], self.normalize_string(line[1:])) for line in re.findall(r"^-.*Copyright.*", content, re.MULTILINE)]
        return added_copyrights, deleted_copyrights

    def run(self):
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

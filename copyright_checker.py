import re

log_prefix = "< file copyright check >"

class CopyrightChecker:
    def __init__(self, patch):
        self.patch = patch


    def detect_copyright_changes(self, content):
        added_copyrights = [line[1:] for line in re.findall(r"\+.*Copyright.*", content)]
        deleted_copyrights = [line[1:] for line in re.findall(r"-.*Copyright.*", content)]
        return added_copyrights, deleted_copyrights


    def run(self):
        global log_prefix
        source_files = [change for change in self.patch.changes
                        if change['file_type'] == 'source']

        flagged_files = {}
        for change in source_files:
            # line = log_prefix + change['path_name']
            added_copyrights, deleted_copyrights = self.detect_copyright_changes(change['content'])

            issues = []
            if change['change_type'] == 'MODIFIED' or change['change_type'] == 'ADDED':
                # Group same copyrights together if they are being added and deleted
                unchanged_copyrights = set(added_copyrights) & set(deleted_copyrights)
                added_copyrights = [copyright for copyright in added_copyrights if copyright not in unchanged_copyrights]
                deleted_copyrights = [copyright for copyright in deleted_copyrights if copyright not in unchanged_copyrights]

                non_qualcomm_copyrights = [copyright for copyright in added_copyrights if "Qualcomm Technologies, Inc." not in copyright and "Qualcomm Innovation Center, Inc." not in copyright]

                if deleted_copyrights:
                    issues.append(f"Copyright deleted: {deleted_copyrights}")
                # if non_qualcomm_copyrights:
                #     issues.append(f"Non-Qualcomm copyright added: {non_qualcomm_copyrights}")
                if issues:
                    flagged_files[change['path_name']] = issues
                else:
                    pass

        return flagged_files


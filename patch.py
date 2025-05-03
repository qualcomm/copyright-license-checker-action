import re

class Patch:
    """
    Class to represent a patch file
    """

    def __init__(self, patchfile: str) -> None:
        """
        Initialize the Patch object.

        Args:
            patchfile (str): The path to the patch file.
        """
        self.patchfile = patchfile

        with open(self.patchfile, 'r') as f:
            self.patch_content = f.read()

        # Split patch into meta (git commit, summary) vs. code content
        file_delimiter_regex = r'^diff .* b\/(?P<file_name>.*)$'
        r = re.split(file_delimiter_regex, self.patch_content, flags=re.MULTILINE)
        # Can be used to filter out the patches for the QUIC authored commits only
        patch_meta = r[0]
        patch_content = r[1:]
        files_changes = list(zip(patch_content[::2], patch_content[1::2]))

        # Create the list of changes in each file
        self.changes = list()
        for path_name, file_change in files_changes:
            # figure change type
            change_type = re.search(r"(\w*) file mode", file_change, re.MULTILINE)
            if change_type and change_type.group(1) == "new":
                change_type = "ADDED"
            elif change_type and change_type.group(1) == "deleted":
                change_type = "DELETED"
            elif re.search("rename from .*\nrename to .*", file_change, re.MULTILINE):
                change_type = "RENAMED"
            else:
                change_type = "MODIFIED"

            # match[0] contains meta, match[1] contains file content if any
            file_content = re.split(r"(?:\+\+\+ .*|GIT binary patch)", file_change)
            file_content = file_content[1] if len(file_content) > 1 else None

            file_type = "binary" if "GIT binary patch" in file_change else "source"

            self.changes.append({
                'path_name': path_name,
                'file_type': file_type,
                'change_type': change_type,
                'content': file_content
            })

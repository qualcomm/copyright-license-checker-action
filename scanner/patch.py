# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

"""
Module to represent and process patch files.
"""

import re

from scanner.file_types import EXCLUDED_EXTENSIONS
from scanner.ignore_config import IgnoreConfig

# git diff wraps a path in double quotes and C-escapes it when the path has a
# '"', a backslash, a control character or, with the default core.quotePath,
# any byte outside printable ASCII: diff --git "a/\303\251.c" "b/\303\251.c"
_GIT_PATH_ESCAPES = {
    "a": 0x07,
    "b": 0x08,
    "t": 0x09,
    "n": 0x0A,
    "v": 0x0B,
    "f": 0x0C,
    "r": 0x0D,
    '"': 0x22,
    "\\": 0x5C,
}


def unquote_git_path(path: str) -> str:
    """
    Undo git's C-style quoting of a path, as printed in diff headers.

    Args:
        path (str): The path as printed by git, quoted or not.

    Returns:
        str: The path with quoting removed and octal escapes decoded as UTF-8.
    """
    if len(path) < 2 or not (path.startswith('"') and path.endswith('"')):
        return path
    raw = bytearray()
    i, end = 1, len(path) - 1
    while i < end:
        char = path[i]
        if char == "\\" and i + 1 < end:
            escaped = path[i + 1]
            if escaped in "01234567":
                start, stop = i + 1, i + 4
                raw.append(int(path[start:stop], 8))
                i += 4
                continue
            if escaped in _GIT_PATH_ESCAPES:
                raw.append(_GIT_PATH_ESCAPES[escaped])
                i += 2
                continue
        raw += char.encode("utf-8")
        i += 1
    return raw.decode("utf-8", errors="replace")


class Patch:
    """
    Class to represent a patch file.
    """

    def __init__(self, patchfile: str) -> None:
        """
        Initialize the Patch object.

        Args:
            patchfile (str): The path to the patch file.
        """
        self.patchfile = patchfile
        self.ignore_config = IgnoreConfig()

        with open(self.patchfile, "r", encoding="utf-8") as f:
            self.patch_content = f.read()

        # Split patch into meta (git commit, summary) vs. code content
        file_delimiter_regex = r'^diff .* (?P<file_name>"b/(?:[^"\\\n]|\\.)*"|b/.*[^"\n])$'
        r = re.split(file_delimiter_regex, self.patch_content, flags=re.MULTILINE)
        patch_content = r[1:]
        files_changes = list(zip(patch_content[::2], patch_content[1::2]))

        # Create the list of changes in each file
        self.changes = []
        for path_name, file_change in files_changes:
            path_name = unquote_git_path(path_name)
            path_name = path_name.removeprefix("b/")

            # figure change type
            change_type = re.search(r"(\w*) file mode", file_change, re.MULTILINE)
            if change_type and change_type.group(1) == "new":
                change_type = "ADDED"
            elif change_type and change_type.group(1) == "deleted":
                change_type = "DELETED"
            elif re.search("rename from .*\nrename to .*", file_change, re.MULTILINE):
                # A pure rename has no hunks. A rename with content changes
                # must receive the same downstream checks as a modification.
                has_content_hunks = re.search(r"^@@ ", file_change, re.MULTILINE)
                change_type = "RENAMED_MODIFIED" if has_content_hunks else "RENAMED"
            else:
                change_type = "MODIFIED"

            # match[0] contains meta, match[1] contains file content if any
            file_content = re.split(r"(?:\+\+\+ .*|GIT binary patch)", file_change)
            file_content = file_content[1] if len(file_content) > 1 else None

            file_type = "binary" if "GIT binary patch" in file_change else "source"

            # Skip files that match hardcoded exclusions or config-based exclusions
            if path_name.endswith(EXCLUDED_EXTENSIONS):
                continue

            if self.ignore_config.is_excluded(path_name):
                continue

            self.changes.append(
                {
                    "path_name": path_name,
                    "file_type": file_type,
                    "change_type": change_type,
                    "content": file_content,
                }
            )

    def get_changes(self):
        """
        Get the list of changes in the patch file.

        Returns:
            list: A list of dictionaries representing the changes in each file.
        """
        return self.changes

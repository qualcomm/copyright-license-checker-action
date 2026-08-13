"""
Tests for scanner.patch.Patch.

These document the parser's *current* behavior, including known coverage gaps,
so that later refactors and the proprietary-mode work cannot change it silently.
"""

import os
import tempfile
import unittest
from pathlib import Path

from scanner.patch import Patch
from tests.static_data import patches


def write_patch(tmpdir: str, content: str) -> str:
    """
    Write patch content to a file inside tmpdir and return its path.

    Args:
        tmpdir: Directory to create the patch file in.
        content: Raw patch text.

    Returns:
        Path to the written patch file.
    """
    path = Path(tmpdir, "test.patch")
    path.write_text(content, encoding="utf-8")
    return str(path)


class PatchTestCase(unittest.TestCase):
    """Base case that isolates tests from any real .licenseignore in the CWD."""

    def setUp(self):
        """Run each test in a scratch directory so .licenseignore never leaks in."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.original_cwd)

    def parse(self, content: str) -> Patch:
        """Write and parse a patch, returning the Patch object."""
        return Patch(write_patch(self.tmp.name, content))


class TestPatchChangeTypes(PatchTestCase):
    """Change-type classification."""

    def test_new_file_mode_is_added(self):
        """A 'new file mode' header classifies the change as ADDED."""
        patch = self.parse(patches.ADDED_SOURCE_FILE)
        self.assertEqual(len(patch.changes), 1)
        self.assertEqual(patch.changes[0]["change_type"], "ADDED")
        self.assertEqual(patch.changes[0]["path_name"], "src/new_module.c")

    def test_deleted_file_mode_is_deleted(self):
        """A 'deleted file mode' header classifies the change as DELETED."""
        patch = self.parse(patches.DELETED_SOURCE_FILE)
        self.assertEqual(patch.changes[0]["change_type"], "DELETED")

    def test_rename_headers_are_renamed(self):
        """'rename from'/'rename to' headers classify the change as RENAMED."""
        patch = self.parse(patches.RENAMED_SOURCE_FILE)
        self.assertEqual(patch.changes[0]["change_type"], "RENAMED")

    def test_plain_diff_is_modified(self):
        """A diff with no mode/rename headers defaults to MODIFIED."""
        patch = self.parse(patches.MODIFIED_WITH_ADDED_COPYRIGHT)
        self.assertEqual(patch.changes[0]["change_type"], "MODIFIED")


class TestPatchFileTypes(PatchTestCase):
    """Source vs. binary classification."""

    def test_source_file_type(self):
        """Text diffs are classified as source."""
        patch = self.parse(patches.MODIFIED_WITH_ADDED_COPYRIGHT)
        self.assertEqual(patch.changes[0]["file_type"], "source")

    def test_binary_patch_is_binary(self):
        """A 'GIT binary patch' marker classifies the change as binary."""
        patch = self.parse(patches.BINARY_FILE)
        self.assertEqual(patch.changes[0]["file_type"], "binary")

    def test_binary_patch_content_is_split_on_marker(self):
        """
        Binary changes still carry content: the split regex alternates on
        '+++ .*' OR 'GIT binary patch', so the text after the marker is kept.
        Documents current behavior — the binary payload is never license-scanned
        because run() filters on file_type == 'source'.
        """
        patch = self.parse(patches.BINARY_FILE)
        self.assertIn("literal 8", patch.changes[0]["content"])


class TestPatchExclusions(PatchTestCase):
    """Hardcoded extension exclusions."""

    def test_excluded_extensions_are_skipped(self):
        """.md, .bb, .json, .yml and .patch files are dropped entirely."""
        patch = self.parse(patches.EXCLUDED_EXTENSIONS)
        self.assertEqual(patch.changes, [])

    def test_licenseignore_excludes_matching_paths(self):
        """Paths matching .licenseignore patterns are dropped."""
        Path(self.tmp.name, ".licenseignore").write_text("src/**\n", encoding="utf-8")
        patch = self.parse(patches.MODIFIED_WITH_ADDED_COPYRIGHT)
        self.assertEqual(patch.changes, [])

    def test_licenseignore_absent_keeps_paths(self):
        """With no .licenseignore, source paths are retained."""
        patch = self.parse(patches.MODIFIED_WITH_ADDED_COPYRIGHT)
        self.assertEqual(len(patch.changes), 1)


class TestPatchContent(PatchTestCase):
    """Content extraction and the public accessor."""

    def test_content_contains_diff_lines(self):
        """Parsed content retains the +/- diff lines after the +++ header."""
        patch = self.parse(patches.MODIFIED_WITH_ADDED_COPYRIGHT)
        content = patch.changes[0]["content"]
        self.assertIn("+ * Copyright (c) 2024 Qualcomm Technologies, Inc.", content)

    def test_get_changes_returns_changes(self):
        """get_changes() returns the same list as the .changes attribute."""
        patch = self.parse(patches.MODIFIED_WITH_ADDED_COPYRIGHT)
        self.assertEqual(patch.get_changes(), patch.changes)

    def test_multiple_files_are_parsed_separately(self):
        """Each diff header produces its own change entry."""
        combined = patches.MODIFIED_WITH_ADDED_COPYRIGHT + patches.ADDED_SOURCE_FILE
        patch = self.parse(combined)
        self.assertEqual(len(patch.changes), 2)
        self.assertEqual(
            [change["path_name"] for change in patch.changes],
            ["src/foo.c", "src/new_module.c"],
        )

    def test_empty_patch_yields_no_changes(self):
        """An empty patch file parses to an empty change list."""
        patch = self.parse("")
        self.assertEqual(patch.changes, [])


if __name__ == "__main__":
    unittest.main()

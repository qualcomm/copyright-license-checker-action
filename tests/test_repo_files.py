# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
"""
Tests for scanner.repo_files: listing the files a full scan checks.

Listing runs real git in a scratch repository, because the quoting rules it
guards against only show up in git's actual output.
"""

import os
import subprocess
import unittest
from pathlib import Path

from scanner.repo_files import list_repo_files, list_tracked_files
from tests.scancode_mock import TempCwdMixin


def git_init_and_add(files: dict) -> None:
    """
    Create a git repository in the current directory holding the given files.

    Args:
        files: Maps a repository-relative path to its text content.
    """
    subprocess.run(["git", "init", "-q"], check=True)
    for path, content in files.items():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], check=True)


class TestListTrackedFiles(TempCwdMixin, unittest.TestCase):
    """list_tracked_files reports real paths, whatever characters they hold."""

    def test_non_ascii_paths_are_listed_unquoted(self):
        """A non-ASCII directory or file name comes back as the real path."""
        git_init_and_add({"café/main.c": "x\n", "naïve.c": "x\n", "plain.c": "x\n"})
        self.assertEqual(sorted(list_tracked_files()), ["café/main.c", "naïve.c", "plain.c"])

    def test_paths_with_spaces_are_listed(self):
        """A space in a path is kept rather than split."""
        git_init_and_add({"my dir/my file.c": "x\n"})
        self.assertEqual(list_tracked_files(), ["my dir/my file.c"])

    def test_non_utf8_path_is_listed(self):
        """A name that is not valid UTF-8 is still listed, and can still be opened."""
        git_init_and_add({"a.c": "x\n"})
        name = os.fsdecode(b"caf\xe9.c")
        try:
            Path(name).write_text("x\n", encoding="utf-8")
        except OSError:
            self.skipTest("this filesystem rejects file names that are not valid UTF-8")
        subprocess.run(["git", "add", "--", name], check=True)
        self.assertEqual(sorted(list_repo_files()), sorted(["a.c", name]))

    def test_untracked_files_are_not_listed(self):
        """Only files in git's index are scanned."""
        git_init_and_add({"tracked.c": "x\n"})
        Path("untracked.c").write_text("x\n", encoding="utf-8")
        self.assertEqual(list_tracked_files(), ["tracked.c"])


class TestListRepoFiles(TempCwdMixin, unittest.TestCase):
    """list_repo_files applies the shared exclusions before anything is scanned."""

    def test_excluded_extensions_are_skipped(self):
        """Files with an EXCLUDED_EXTENSIONS extension are never scanned."""
        git_init_and_add({"a.c": "x\n", "README.md": "x\n", "ci.yml": "x\n"})
        self.assertEqual(list_repo_files(), ["a.c"])

    def test_non_source_files_are_listed(self):
        """A file that needs no license header is still checked for one."""
        git_init_and_add({"Makefile": "x\n", "build.mk": "x\n"})
        self.assertEqual(list_repo_files(), ["Makefile", "build.mk"])

    def test_licenseignore_is_honored(self):
        """Paths matched by .licenseignore are skipped, as in the patch scan."""
        git_init_and_add({"a.c": "x\n", "vendor/b.c": "x\n", ".licenseignore": "vendor/\n"})
        self.assertEqual(list_repo_files(), [".licenseignore", "a.c"])


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()

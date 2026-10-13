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

    def test_binary_files_are_skipped(self):
        """A file holding a NUL byte is binary and is not scanned."""
        git_init_and_add({"a.c": "x\n"})
        Path("image.bin").write_bytes(b"\x89PNG\0\0")
        subprocess.run(["git", "add", "image.bin"], check=True)
        self.assertEqual(list_repo_files(), ["a.c"])

    def test_binary_check_stops_at_8000_bytes(self):
        """A zero byte among the first 8000 bytes makes a file binary; a later one does not."""
        git_init_and_add({"a.c": "x\n"})
        Path("nul_at_7999").write_bytes(b"x" * 7999 + b"\0")
        Path("nul_at_8000").write_bytes(b"x" * 8000 + b"\0")
        subprocess.run(["git", "add", "nul_at_7999", "nul_at_8000"], check=True)
        self.assertEqual(list_repo_files(), ["a.c", "nul_at_8000"])

    def test_submodules_are_skipped(self):
        """A submodule is a directory on disk, so it is skipped rather than opened."""
        git_init_and_add({"a.c": "x\n"})
        gitlink = f"160000,{'1' * 40},vendor/sub"
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", gitlink], check=True)
        Path("vendor/sub").mkdir(parents=True)
        self.assertEqual(list_repo_files(), ["a.c"])

    def test_symlinks_are_skipped(self):
        """A symlink is skipped; the file it points to is scanned on its own."""
        git_init_and_add({"a.c": "x\n"})
        Path("link.c").symlink_to("a.c")
        subprocess.run(["git", "add", "link.c"], check=True)
        self.assertEqual(list_repo_files(), ["a.c"])


if __name__ == "__main__":
    unittest.main()

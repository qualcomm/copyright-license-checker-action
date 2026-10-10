# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
"""
Module to list the repository files a whole-repository scan checks.

Where Patch reports the files one pull request touches, this reports what the
repository holds, so files committed before the action was enabled are covered
too. Both drop EXCLUDED_EXTENSIONS and .licenseignore matches.
"""

import os
import subprocess

from scanner.file_types import EXCLUDED_EXTENSIONS
from scanner.ignore_config import IgnoreConfig

# Binary files, such as images or compiled programs, are skipped. A file counts
# as binary if any of its first 8000 bytes is zero, the same test Git uses.
BINARY_SNIFF_BYTES = 8000


def list_tracked_files() -> list:
    """
    List every git-tracked path in the current directory.

    Returns:
        list: Repository-relative paths, exactly as stored in the index.
    """
    # -z also turns off core.quotePath, which would otherwise wrap any path
    # holding a non-ASCII byte in quotes and octal escapes, so it would never
    # match a real file. surrogateescape keeps non-UTF-8 names openable.
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=True,
        errors="surrogateescape",
    )
    return [path for path in result.stdout.split("\0") if path]


def is_text_file(path: str) -> bool:
    """
    Report whether a path is a regular, non-binary file.

    A symlink is skipped because the file it points at is listed in its own
    right, and a submodule is a directory, so neither is read.

    Args:
        path (str): A repository-relative path.

    Returns:
        bool: True if the path is a regular text file.
    """
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    with open(path, "rb") as f:
        return b"\0" not in f.read(BINARY_SNIFF_BYTES)


def list_repo_files() -> list:
    """
    List the tracked files to check for license problems.

    Every tracked text file is listed, not just the SOURCE_EXTENSIONS ones,
    because an incompatible license matters wherever it appears. That mirrors
    LicenseChecker, which only limits its "a new file needs a license" check to
    source extensions and checks every other changed file for an incompatible
    one. Requiring a license header therefore stays the checker's decision.

    Returns:
        list: Repository-relative paths, in git's index order.
    """
    ignore_config = IgnoreConfig()
    return [
        path
        for path in list_tracked_files()
        if not path.endswith(EXCLUDED_EXTENSIONS)
        and not ignore_config.is_excluded(path)
        and is_text_file(path)
    ]

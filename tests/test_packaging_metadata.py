# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

"""Tests that keep packaging metadata and compatibility requirements aligned."""

import tomllib
import unittest
from pathlib import Path

from copyright_license_checker import __version__


class TestPackagingMetadata(unittest.TestCase):
    """Verify the package declaration remains a single dependency source."""

    def setUp(self) -> None:
        """Load packaging inputs from the repository root."""
        self.repository_root = Path(__file__).resolve().parent.parent

    def test_requirements_file_mirrors_project_dependencies(self) -> None:
        """Keep legacy installer requirements synchronized with project metadata."""
        pyproject = tomllib.loads(
            (self.repository_root / "pyproject.toml").read_text(encoding="utf-8")
        )
        requirements = [
            line
            for line in (self.repository_root / "requirements.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line and not line.startswith("#")
        ]

        self.assertEqual(requirements, pyproject["project"]["dependencies"])

    def test_project_metadata_uses_the_package_version(self) -> None:
        """Use the package version as the single version source."""
        pyproject = tomllib.loads(
            (self.repository_root / "pyproject.toml").read_text(encoding="utf-8")
        )

        self.assertEqual(pyproject["project"]["dynamic"], ["version"])
        self.assertEqual(
            pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "copyright_license_checker.__version__",
        )
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

"""Validate the distributions built by the ``package`` tox environment."""

import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path


class TestPackageArtifacts(unittest.TestCase):
    """Confirm source and wheel distributions contain the checker package."""

    def setUp(self) -> None:
        """Locate the distributions produced at the repository root."""
        self.repository_root = Path(__file__).resolve().parent.parent
        self.distribution_directory = self.repository_root / "dist"

    def _wheel_path(self) -> Path:
        """Return the one wheel built for this package."""
        wheels = list(self.distribution_directory.glob("*.whl"))
        self.assertEqual(len(wheels), 1)
        return wheels[0]

    def _source_path(self) -> Path:
        """Return the one source distribution built for this package."""
        source_distributions = list(self.distribution_directory.glob("*.tar.gz"))
        self.assertEqual(len(source_distributions), 1)
        return source_distributions[0]

    def test_wheel_contains_package_and_metadata(self) -> None:
        """The wheel contains the runnable modules and declared dependencies."""
        with zipfile.ZipFile(self._wheel_path()) as wheel:
            names = set(wheel.namelist())
            metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
            metadata = wheel.read(metadata_name).decode("utf-8")

        self.assertTrue(
            {
                "copyright_license_checker/__init__.py",
                "copyright_license_checker/__main__.py",
                "copyright_license_checker/cli.py",
                "copyright_license_checker/license_scancode.py",
            }.issubset(names)
        )
        self.assertIn("Requires-Python: >=3.12", metadata)
        self.assertIn("Requires-Dist: scancode-toolkit==32.5.0", metadata)
        self.assertIn("Requires-Dist: lxml==6.1.0", metadata)

    def test_source_distribution_contains_build_inputs(self) -> None:
        """The source distribution has the files needed to rebuild the package."""
        with tarfile.open(self._source_path()) as source:
            names = set(source.getnames())

        self.assertTrue(any(name.endswith("/pyproject.toml") for name in names))
        self.assertTrue(any(name.endswith("/README.md") for name in names))
        self.assertTrue(any(name.endswith("/LICENSE") for name in names))
        self.assertTrue(
            any(name.endswith("/src/copyright_license_checker/cli.py") for name in names)
        )

    def test_installed_console_command_runs_outside_source_checkout(self) -> None:
        """Install the wheel with ScanCode and run its generated console command."""
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--only-binary=lxml",
                str(self._wheel_path()),
            ],
            check=True,
        )
        self.assertIsNotNone(shutil.which("scancode"))

        with tempfile.TemporaryDirectory() as temporary_directory:
            result = subprocess.run(
                ["copyright-license-checker", "--version"],
                cwd=temporary_directory,
                capture_output=True,
                check=True,
                text=True,
            )

        self.assertRegex(result.stdout, r"^copyright-license-checker \d+\.\d+\.\d+\n$")


if __name__ == "__main__":
    unittest.main()

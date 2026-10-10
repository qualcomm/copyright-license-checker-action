# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
"""
Tests for scanner.repo_checker: checking whole files for license problems.

scancode stays mocked, as in the rest of the suite. The shared mock patches
subprocess.run itself, so it also covers the call in scanner.repo_checker.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch as mock_patch

from scanner.repo_checker import RepoLicenseChecker, detect_licenses
from tests.scancode_mock import TempCwdMixin, scancode_mock_patcher

ALLOWED = ["BSD-3-Clause", "MIT", "Apache-2.0"]
SUBPROCESS_RUN = "scanner.repo_checker.subprocess.run"


class TestDetectLicenses(TempCwdMixin, unittest.TestCase):
    """detect_licenses runs one scancode over copies of the files."""

    def test_results_are_keyed_by_original_path(self):
        """scancode's results map back to the repository-relative paths."""
        Path("src").mkdir()
        Path("src/a.c").write_text("x\n", encoding="utf-8")
        with scancode_mock_patcher({"src/a.c": "MIT"}) as run:
            results = detect_licenses(["src/a.c"])
        self.assertEqual(results["src/a.c"]["detected_license_expression_spdx"], "MIT")
        self.assertEqual(run.call_count, 1)
        # --strip-root has scancode report each path relative to the directory it was given.
        self.assertIn("--strip-root", run.call_args.args[0])

    def test_scancode_is_given_exactly_the_listed_files(self):
        """Each listed file reaches scancode at its own path, so same-named files stay apart."""
        for path in ("a/main.c", "b/main.c", "unlisted.c"):
            Path(path).parent.mkdir(exist_ok=True)
            Path(path).write_text("x\n", encoding="utf-8")
        handed_over = []

        def fake_run(cmd, **_kwargs):
            input_dir = Path(cmd[-1])
            handed_over.extend(f.relative_to(input_dir).as_posix() for f in input_dir.rglob("*.c"))
            Path(cmd[cmd.index("--json-pp") + 1]).write_text('{"files": []}', encoding="utf-8")
            return MagicMock(returncode=0)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            detect_licenses(["a/main.c", "b/main.c"])
        self.assertEqual(sorted(handed_over), ["a/main.c", "b/main.c"])


class TestRepoLicenseChecker(TempCwdMixin, unittest.TestCase):
    """RepoLicenseChecker applies the shared license rules to whole files."""

    def run_checker(self, detections: dict, allowed: list = None) -> tuple:
        """
        Run the checker over detections' files with scancode mocked.

        Args:
            detections: Maps each path to the SPDX expression scancode reports
                for it, or None for no license.
            allowed: The allowed licenses, defaulting to ALLOWED.

        Returns:
            The checker's (flagged_files, warning_files).
        """
        for path in detections:
            Path(path).write_text("x\n", encoding="utf-8")
        with scancode_mock_patcher(detections):
            return RepoLicenseChecker(list(detections), allowed or ALLOWED).run()

    def test_allowed_license_passes(self):
        """A file carrying an allowed license raises no issue."""
        self.assertEqual(self.run_checker({"a.c": "MIT"}), ({}, {}))

    def test_incompatible_license_blocks(self):
        """A file carrying a disallowed license is a blocking issue."""
        flagged, _ = self.run_checker({"a.c": "GPL-2.0-only"})
        self.assertEqual(
            flagged["a.c"],
            {"license_issues": ["Incompatible license: GPL-2.0-only"], "copyright_issues": []},
        )

    def test_uncertain_license_warns(self):
        """An unrecognized ScanCode reference is a warning, not a blocking issue."""
        flagged, warnings = self.run_checker({"a.c": "LicenseRef-scancode-unknown"})
        self.assertEqual(flagged, {})
        self.assertEqual(
            warnings,
            {
                "a.c": {
                    "license_issues": ["Incompatible license: LicenseRef-scancode-unknown"],
                    "copyright_issues": [],
                }
            },
        )

    def test_license_in_a_later_detection_blocks(self):
        """A disallowed license found after an allowed one in the same file still blocks."""
        Path("a.c").write_text("x\n", encoding="utf-8")
        result = {
            "path": "a.c",
            "type": "file",
            "detected_license_expression_spdx": "MIT AND GPL-2.0-only",
            "license_detections": [
                {"license_expression_spdx": "MIT"},
                {"license_expression_spdx": "GPL-2.0-only"},
            ],
        }
        with mock_patch("scanner.repo_checker.detect_licenses", return_value={"a.c": result}):
            flagged, _ = RepoLicenseChecker(["a.c"], ALLOWED).run()
        self.assertEqual(
            flagged["a.c"]["license_issues"], ["Incompatible license: MIT AND GPL-2.0-only"]
        )

    def test_source_file_without_license_blocks(self):
        """A source file with no license at all is a blocking issue."""
        flagged, _ = self.run_checker({"a.c": None})
        self.assertEqual(flagged["a.c"]["license_issues"], ["No license found in source file: a.c"])

    def test_other_file_without_license_passes(self):
        """A non-source file is not required to carry a license."""
        self.assertEqual(self.run_checker({"Makefile": None}), ({}, {}))

    def test_other_file_with_incompatible_license_blocks(self):
        """A non-source file carrying a disallowed license still blocks."""
        flagged, _ = self.run_checker({"Makefile": "GPL-2.0-only"})
        self.assertIn("Makefile", flagged)

    def test_file_scancode_skipped_is_treated_as_unlicensed(self):
        """A file absent from scancode's report has no license: only a source file is flagged."""
        for path in ("a.c", ".gitignore"):
            Path(path).write_text("x\n", encoding="utf-8")
        with scancode_mock_patcher({}):
            flagged, _ = RepoLicenseChecker(["a.c", ".gitignore"], ALLOWED).run()
        self.assertEqual(list(flagged), ["a.c"])

    def test_no_files_skips_scancode(self):
        """With nothing to scan, scancode is not run at all."""
        with mock_patch(SUBPROCESS_RUN) as run:
            self.assertEqual(RepoLicenseChecker([], ALLOWED).run(), ({}, {}))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

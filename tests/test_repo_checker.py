# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
"""
Tests for scanner.repo_checker: checking whole files for license problems.

scancode stays mocked, as in the rest of the suite. The shared mock patches
subprocess.run itself, so it also covers the call in scanner.repo_checker.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch as mock_patch

from scanner.repo_checker import RepoLicenseChecker, detect_licenses
from tests.scancode_mock import TempCwdMixin, scancode_mock_patcher

ALLOWED = ["BSD-3-Clause", "MIT", "Apache-2.0"]
SUBPROCESS_RUN = "scanner.repo_checker.subprocess.run"


class TestDetectLicenses(TempCwdMixin, unittest.TestCase):
    """detect_licenses runs one scancode over staged hardlinks of the files."""

    def test_no_paths_skips_scancode(self):
        """A direct call with nothing to scan does not stage or run scancode at all."""
        with mock_patch(SUBPROCESS_RUN) as run:
            self.assertEqual(detect_licenses([]), {})
        run.assert_not_called()

    def test_no_staging_directory_raises_naming_the_location_and_reason(self):
        """If no staging directory can be created at all, the scan stops with a clear reason."""
        Path("a.c").write_text("x\n", encoding="utf-8")
        with (
            mock_patch(SUBPROCESS_RUN) as run,
            mock_patch("tempfile.mkdtemp", side_effect=OSError("Permission denied")),
        ):
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["a.c"])
        run.assert_not_called()
        self.assertIn("Permission denied", str(caught.exception))

    def test_hardlink_failure_raises_naming_the_path_and_reason(self):
        """If a file cannot be hardlinked, the scan stops instead of copying it."""
        Path("a.c").write_text("x\n", encoding="utf-8")
        with (
            mock_patch(SUBPROCESS_RUN) as run,
            mock_patch(
                "scanner.repo_checker.os.link",
                side_effect=OSError("Invalid cross-device link"),
            ),
        ):
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["a.c"])
        run.assert_not_called()
        self.assertIn("a.c", str(caught.exception))
        self.assertIn("Invalid cross-device link", str(caught.exception))

    def test_hardlink_failure_cleans_up_the_staging_directory(self):
        """A hardlink failure still removes the staging directory it created."""
        Path("a.c").write_text("x\n", encoding="utf-8")
        created = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(*args, **kwargs):
            name = real_mkdtemp(*args, **kwargs)
            created.append(name)
            return name

        with (
            mock_patch("scanner.repo_checker.os.link", side_effect=OSError("no hardlinks here")),
            mock_patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp),
        ):
            with self.assertRaises(RuntimeError):
                detect_licenses(["a.c"])
        self.assertEqual(len(created), 1)
        self.assertFalse(Path(created[0]).exists())

    def test_staging_happens_inside_the_checkout(self):
        """The staging directory is created inside the checkout, not next to it."""
        Path("a.c").write_text("x\n", encoding="utf-8")
        checkout_root = Path.cwd()

        def fake_run(cmd, **_kwargs):
            input_dir = Path(cmd[-1]).resolve()
            self.assertEqual(input_dir.parts[: len(checkout_root.parts)], checkout_root.parts)
            Path(cmd[cmd.index("--json-pp") + 1]).write_text('{"files": []}', encoding="utf-8")
            return MagicMock(returncode=0)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            detect_licenses(["a.c"])

    def test_staging_is_cleaned_up_on_success(self):
        """The staging directory is removed once the scan completes normally."""
        Path("a.c").write_text("x\n", encoding="utf-8")
        checkout_root = Path.cwd()

        def fake_run(cmd, **_kwargs):
            Path(cmd[cmd.index("--json-pp") + 1]).write_text('{"files": []}', encoding="utf-8")
            return MagicMock(returncode=0)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            detect_licenses(["a.c"])
        self.assertEqual(list(checkout_root.iterdir()), [checkout_root / "a.c"])

    def test_files_are_hardlinked_not_copied(self):
        """A staged file is a hardlink to the original, not a byte-for-byte copy."""
        Path("a.c").write_text("x\n", encoding="utf-8")
        same_file = []

        def fake_run(cmd, **_kwargs):
            # The staging directory still exists at this point; it is cleaned up only
            # after detect_licenses() returns, so check the hardlink here, not after.
            staged = Path(cmd[-1]) / "a.c"
            same_file.append(staged.samefile("a.c"))
            Path(cmd[cmd.index("--json-pp") + 1]).write_text('{"files": []}', encoding="utf-8")
            return MagicMock(returncode=0)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            detect_licenses(["a.c"])
        self.assertEqual(same_file, [True])

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

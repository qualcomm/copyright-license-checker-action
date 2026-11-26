# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
"""
Tests for scanner.repo_checker: checking whole files for license problems.

scancode stays mocked, as in the rest of the suite. The shared mock patches
subprocess.run itself, so it also covers the call in scanner.repo_checker.
"""

import json
import subprocess
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

    def test_missing_report_raises_with_scancode_output(self):
        """If scancode writes no report, the error carries its exit code and stderr."""
        Path("a.c").write_text("x\n", encoding="utf-8")
        failed = MagicMock(returncode=2, stderr="Error: bad option")
        with mock_patch(SUBPROCESS_RUN, return_value=failed):
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["a.c"])
        self.assertIn("exit code 2", str(caught.exception))
        self.assertIn("Error: bad option", str(caught.exception))

    def test_unattributed_run_level_error_raises_even_with_a_valid_report(self):
        """A worker that never finished is recorded against the run, not a file; still fails."""
        for path in ("a.c", "b.c"):
            Path(path).write_text("x\n", encoding="utf-8")

        def fake_run(cmd, **_kwargs):
            # b.c's worker never responds, so scancode records that against
            # the run rather than against a file: b.c is simply missing from
            # "files" below, as if it had never been given to scancode at all.
            # a.c scans cleanly in the same run, to show that one file's loss
            # is reason enough to fail, whatever else succeeded alongside it.
            report = {
                "headers": [{"errors": ["Timeout waiting for resource. Path unknown."]}],
                "files": [
                    {
                        "path": "a.c",
                        "type": "file",
                        "detected_license_expression_spdx": "MIT",
                    }
                ],
            }
            Path(cmd[cmd.index("--json-pp") + 1]).write_text(json.dumps(report), encoding="utf-8")
            return MagicMock(returncode=1)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["a.c", "b.c"])
        self.assertIn("Timeout waiting for resource", str(caught.exception))

    def test_attributed_file_error_does_not_abort_the_run(self):
        """A file that fails on its own does not stop the rest of the run from being reported."""
        for path in ("a.c", "b.c"):
            Path(path).write_text("x\n", encoding="utf-8")

        def fake_run(cmd, **_kwargs):
            # a.c times out on its own: scancode records this both on a.c's
            # own scan_errors (what _classify() reads) and as a
            # "Path: input/a.c" pointer in headers[0]["errors"] (scancode's
            # own cli.py adds this pointer for every file that already has
            # scan_errors, even without --verbose). b.c scans cleanly
            # alongside it.
            report = {
                "headers": [{"errors": ["Path: input/a.c"]}],
                "files": [
                    {"path": "a.c", "type": "file", "scan_errors": ["ERROR: timeout"]},
                    {
                        "path": "b.c",
                        "type": "file",
                        "detected_license_expression_spdx": "MIT",
                    },
                ],
            }
            Path(cmd[cmd.index("--json-pp") + 1]).write_text(json.dumps(report), encoding="utf-8")
            return MagicMock(returncode=1)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            results = detect_licenses(["a.c", "b.c"])
        self.assertEqual(results["a.c"]["scan_errors"], ["ERROR: timeout"])
        self.assertEqual(results["b.c"]["detected_license_expression_spdx"], "MIT")

    def test_attributed_error_for_a_nested_path_does_not_abort_the_run(self):
        """The pointer's path can have more than one segment and still match correctly."""
        Path("src").mkdir()
        Path("src/a.c").write_text("x\n", encoding="utf-8")

        def fake_run(cmd, **_kwargs):
            report = {
                "headers": [{"errors": ["Path: input/src/a.c"]}],
                "files": [{"path": "src/a.c", "type": "file", "scan_errors": ["ERROR: timeout"]}],
            }
            Path(cmd[cmd.index("--json-pp") + 1]).write_text(json.dumps(report), encoding="utf-8")
            return MagicMock(returncode=1)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            results = detect_licenses(["src/a.c"])
        self.assertEqual(results["src/a.c"]["scan_errors"], ["ERROR: timeout"])

    def test_wrong_root_pointer_is_not_mistaken_for_a_known_file(self):
        """A pointer scancode would never actually write must not be matched by accident."""
        Path("a.c").write_text("x\n", encoding="utf-8")

        def fake_run(cmd, **_kwargs):
            # scancode always writes "Path: input/<path>", since "input" is the
            # directory detect_licenses() stages files under; a pointer under
            # any other directory name is not one scancode would ever produce
            # for this run, so it must still be treated as unattributed.
            report = {
                "headers": [{"errors": ["Path: other/a.c"]}],
                "files": [{"path": "a.c", "type": "file", "scan_errors": ["ERROR: timeout"]}],
            }
            Path(cmd[cmd.index("--json-pp") + 1]).write_text(json.dumps(report), encoding="utf-8")
            return MagicMock(returncode=1)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["a.c"])
        self.assertIn("Path: other/a.c", str(caught.exception))

    def test_backslash_in_a_filename_is_matched_to_the_original_path(self):
        """A literal backslash (valid on Linux) must not make ScanCode's result unreachable."""
        Path("a\\b.mk").write_text("x\n", encoding="utf-8")

        def fake_run(cmd, **_kwargs):
            # ScanCode reports every backslash in a path as a forward slash,
            # even though the staged file is not actually nested.
            entry = {"path": "a/b.mk", "type": "file", "detected_license_expression_spdx": "MIT"}
            Path(cmd[cmd.index("--json-pp") + 1]).write_text(
                json.dumps({"files": [entry]}), encoding="utf-8"
            )
            return MagicMock(returncode=0)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            results = detect_licenses(["a\\b.mk"])
        self.assertEqual(results["a\\b.mk"]["detected_license_expression_spdx"], "MIT")

    def test_unexpected_reported_path_raises(self):
        """A path ScanCode reports that matches none of the staged files fails the scan."""
        Path("a.c").write_text("x\n", encoding="utf-8")

        def fake_run(cmd, **_kwargs):
            entry = {"path": "other.c", "type": "file", "detected_license_expression_spdx": "MIT"}
            Path(cmd[cmd.index("--json-pp") + 1]).write_text(
                json.dumps({"files": [entry]}), encoding="utf-8"
            )
            return MagicMock(returncode=0)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["a.c"])
        self.assertIn("other.c", str(caught.exception))

    def test_no_run_level_error_is_read_normally(self):
        """A report with an empty or absent headers/errors still reads its files."""
        Path("a.c").write_text("x\n", encoding="utf-8")
        with scancode_mock_patcher({"a.c": "MIT"}):
            results = detect_licenses(["a.c"])
        self.assertEqual(results["a.c"]["detected_license_expression_spdx"], "MIT")


class TestStagedPathCollisions(TempCwdMixin, unittest.TestCase):
    """detect_licenses refuses to guess when two paths would collide in ScanCode's report."""

    def test_colliding_backslash_and_forward_slash_paths_raise(self):
        """Two distinct paths that read identically to ScanCode must not merge their results."""
        Path("a").mkdir()
        Path("a/b.mk").write_text("x\n", encoding="utf-8")
        Path("a\\b.mk").write_text("y\n", encoding="utf-8")

        with mock_patch(SUBPROCESS_RUN) as run:
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["a/b.mk", "a\\b.mk"])
        run.assert_not_called()
        self.assertIn("a/b.mk", str(caught.exception))
        self.assertIn("a\\b.mk", str(caught.exception))

    def test_colliding_dot_segment_paths_raise(self):
        """ScanCode also collapses "." and ".." segments, so those must collide too."""
        Path("a").mkdir()
        Path("a/b.mk").write_text("x\n", encoding="utf-8")
        Path("a\\.\\b.mk").write_text("y\n", encoding="utf-8")

        with mock_patch(SUBPROCESS_RUN) as run:
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["a/b.mk", "a\\.\\b.mk"])
        run.assert_not_called()
        self.assertIn("a/b.mk", str(caught.exception))
        self.assertIn("a\\.\\b.mk", str(caught.exception))

    def test_leading_backslash_colliding_with_the_staging_root_raises(self):
        """A leading backslash can merge with the staging root's own separator."""
        Path("b.mk").write_text("x\n", encoding="utf-8")
        Path("\\b.mk").write_text("y\n", encoding="utf-8")

        with mock_patch(SUBPROCESS_RUN) as run:
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["b.mk", "\\b.mk"])
        run.assert_not_called()
        self.assertIn("b.mk", str(caught.exception))
        self.assertIn("\\b.mk", str(caught.exception))

    def test_file_colliding_with_a_normalized_directory_name_raises(self):
        """A file whose reported path is an ancestor of another's must not go unnoticed."""
        Path("a").mkdir()
        Path("a/b.mk").write_text("x\n", encoding="utf-8")
        Path("a\\b.mk").mkdir()
        Path("a\\b.mk/ok.mk").write_text("y\n", encoding="utf-8")

        with mock_patch(SUBPROCESS_RUN) as run:
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["a/b.mk", "a\\b.mk/ok.mk"])
        run.assert_not_called()
        self.assertIn("a/b.mk", str(caught.exception))

    def test_path_escaping_the_staging_root_raises(self):
        """A leading ".." can collapse a path out of the staging root entirely."""
        Path("..\\b.mk").write_text("x\n", encoding="utf-8")

        with mock_patch(SUBPROCESS_RUN) as run:
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["..\\b.mk"])
        run.assert_not_called()
        self.assertIn("..\\b.mk", str(caught.exception))

    def test_parent_directory_colliding_with_a_file_raises_even_if_the_leaf_does_not(self):
        """A file's own real parent directory must be checked, not just its collapsed leaf path."""
        Path("a").mkdir()
        Path("a/b.mk").write_text("x\n", encoding="utf-8")
        Path("a\\b.mk").mkdir()
        Path("a\\b.mk/..\\ok.mk").write_text("y\n", encoding="utf-8")

        with mock_patch(SUBPROCESS_RUN) as run:
            with self.assertRaises(RuntimeError) as caught:
                detect_licenses(["a/b.mk", "a\\b.mk/..\\ok.mk"])
        run.assert_not_called()
        self.assertIn("a/b.mk", str(caught.exception))
        self.assertIn("a\\b.mk", str(caught.exception))


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

    def test_file_scancode_failed_on_blocks_with_reason(self):
        """A file scancode failed on is reported as such, though scancode exits non-zero."""
        Path("a.c").write_text("x\n", encoding="utf-8")

        def fake_run(cmd, check=False, **_kwargs):
            # scancode exits 1 when it times out on a file, yet still writes its
            # report; subprocess.run raises on that exit only if check=True.
            # It also adds a "Path: input/a.c" pointer to headers[0]["errors"]
            # for the same file, which must not itself abort the run.
            entry = {"path": "a.c", "type": "file", "scan_errors": ["ERROR: timeout\n"]}
            report = {"headers": [{"errors": ["Path: input/a.c"]}], "files": [entry]}
            Path(cmd[cmd.index("--json-pp") + 1]).write_text(json.dumps(report), encoding="utf-8")
            if check:
                raise subprocess.CalledProcessError(1, cmd)
            return MagicMock(returncode=1)

        with mock_patch(SUBPROCESS_RUN, side_effect=fake_run):
            flagged, _ = RepoLicenseChecker(["a.c"], ALLOWED).run()
        self.assertEqual(flagged["a.c"]["license_issues"], ["License scan failed: ERROR: timeout"])

    def test_no_files_skips_scancode(self):
        """With nothing to scan, scancode is not run at all."""
        with mock_patch(SUBPROCESS_RUN) as run:
            self.assertEqual(RepoLicenseChecker([], ALLOWED).run(), ({}, {}))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

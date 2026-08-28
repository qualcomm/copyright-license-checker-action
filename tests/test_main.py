"""
Tests for the helper functions in main.py.

Covers repository license resolution (LICENSE file scan, config fallback and
default) and the warning-vs-error classification of uncertain licenses.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch as mock_patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402  pylint: disable=wrong-import-position


class LicenseFileTestCase(unittest.TestCase):
    """Base case that runs each test inside a scratch working directory."""

    def setUp(self):
        """Change into a temporary directory so LICENSE lookups are isolated."""
        # pylint: disable=consider-using-with
        # A `with` block can't span setUp/tearDown; addCleanup is the correct
        # unittest idiom for scoping a TemporaryDirectory to the test lifetime.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        original_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, original_cwd)


class TestDetectLicenseFromFile(LicenseFileTestCase):
    """detect_license_from_file shells out to scancode and parses its report."""

    def install_scancode_mock(self, expression):
        """
        Patch subprocess.run to emit a scancode report for a single file.

        Args:
            expression: SPDX expression to report, or None for no detection.
        """

        def fake_run(cmd, **_kwargs):
            output_file = cmd[cmd.index("--json-pp") + 1]
            detections = [] if expression is None else [{"license_expression_spdx": expression}]
            report = {
                "files": [{"path": "LICENSE", "type": "file", "license_detections": detections}]
            }
            Path(output_file).write_text(__import__("json").dumps(report), encoding="utf-8")
            return MagicMock(returncode=0)

        patcher = mock_patch("main.subprocess.run", side_effect=fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_file_returns_none(self):
        """A path that does not exist yields None without invoking scancode."""
        self.assertIsNone(main.detect_license_from_file("does/not/exist"))

    def test_detected_expression_is_returned(self):
        """The first detection's SPDX expression is returned."""
        Path("LICENSE").write_text("MIT license text", encoding="utf-8")
        self.install_scancode_mock("MIT")
        self.assertEqual(main.detect_license_from_file("LICENSE"), "MIT")

    def test_no_detection_returns_none(self):
        """A report with no detections yields None."""
        Path("LICENSE").write_text("unrecognizable", encoding="utf-8")
        self.install_scancode_mock(None)
        self.assertIsNone(main.detect_license_from_file("LICENSE"))

    def test_scancode_failure_returns_none(self):
        """A subprocess error is swallowed and reported as None."""
        Path("LICENSE").write_text("MIT license text", encoding="utf-8")
        patcher = mock_patch("main.subprocess.run", side_effect=OSError("scancode missing"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertIsNone(main.detect_license_from_file("LICENSE"))


class TestGetLicense(LicenseFileTestCase):
    """get_license resolves the repository's own license."""

    def test_bsd_detection_is_coerced_to_clear(self):
        """Any detected BSD variant is normalized to BSD-3-Clause-Clear."""
        Path("LICENSE").write_text("BSD text", encoding="utf-8")
        with mock_patch("main.detect_license_from_file", return_value="BSD-2-Clause"):
            self.assertEqual(main.get_license("org/repo"), "BSD-3-Clause-Clear")

    def test_non_bsd_detection_is_returned_as_is(self):
        """A non-BSD detection is returned unchanged."""
        Path("LICENSE").write_text("GPL text", encoding="utf-8")
        with mock_patch("main.detect_license_from_file", return_value="GPL-2.0-only"):
            self.assertEqual(main.get_license("org/repo"), "GPL-2.0-only")

    def test_config_fallback_by_suffix_match(self):
        """With no LICENSE file, a config project name suffix match wins."""
        self.assertEqual(main.get_license("someorg/meta-qcom-kernel"), "GPL-2.0")

    def test_config_fallback_by_exact_match(self):
        """An exact config project name also matches."""
        self.assertEqual(main.get_license("targoy-qti/qli_test_repo"), "GPL-2.0")

    def test_default_when_nothing_matches(self):
        """With no LICENSE file and no config entry, the default is returned."""
        self.assertEqual(main.get_license("unknown/repository"), "BSD-3-Clause-Clear")

    def test_alternate_license_filenames_are_found(self):
        """COPYING is among the recognized license filenames."""
        Path("COPYING").write_text("MIT text", encoding="utf-8")
        with mock_patch("main.detect_license_from_file", return_value="MIT") as detect:
            self.assertEqual(main.get_license("org/repo"), "MIT")
            self.assertTrue(detect.called)


class TestIsUncertainLicenseIssue(unittest.TestCase):
    """Classification of license issues as uncertain (warning) or real (error)."""

    def test_unknown_license_is_uncertain(self):
        """A lone scancode 'unknown' reference is a warning."""
        self.assertTrue(
            main.is_uncertain_license_issue(
                "Incompatible license added: LicenseRef-scancode-unknown-license-reference"
            )
        )

    def test_gpl_is_not_uncertain(self):
        """A known copyleft license is an error, not a warning."""
        self.assertFalse(
            main.is_uncertain_license_issue("Incompatible license added: GPL-2.0-only")
        )

    def test_mixed_unknown_and_gpl_is_not_uncertain(self):
        """Any recognized incompatible license in the expression forces an error."""
        issue = (
            "Incompatible license added: GPL-2.0-only AND "
            "LicenseRef-scancode-unknown-license-reference"
        )
        self.assertFalse(main.is_uncertain_license_issue(issue))

    def test_all_uncertain_components_is_uncertain(self):
        """An expression made only of uncertain references is a warning."""
        issue = (
            "Incompatible license added: LicenseRef-scancode-unknown-license-reference AND "
            "LicenseRef-scancode-warranty-disclaimer"
        )
        self.assertTrue(main.is_uncertain_license_issue(issue))

    def test_solitary_proprietary_license_is_not_uncertain(self):
        """
        A lone proprietary-license detection is a blocking error today. Proprietary
        mode will make this mode-aware; this test pins the current behavior.
        """
        self.assertFalse(
            main.is_uncertain_license_issue(
                "Incompatible license added: LicenseRef-scancode-proprietary-license"
            )
        )

    def test_proprietary_mixed_with_unknown_is_uncertain(self):
        """Mixed with other uncertain references, proprietary becomes a warning."""
        issue = (
            "Incompatible license added: LicenseRef-scancode-proprietary-license AND "
            "LicenseRef-scancode-unknown-license-reference"
        )
        self.assertTrue(main.is_uncertain_license_issue(issue))

    def test_license_change_issue_examines_added_license(self):
        """For a change issue, only the added license decides the outcome."""
        self.assertTrue(
            main.is_uncertain_license_issue(
                "License deleted: MIT and license added: LicenseRef-scancode-unknown"
            )
        )
        self.assertFalse(
            main.is_uncertain_license_issue("License deleted: MIT and license added: GPL-2.0-only")
        )

    def test_permissive_licenseref_is_not_uncertain(self):
        """A LicenseRef that appears in the permissive list is not uncertain."""
        self.assertFalse(
            main.is_uncertain_license_issue(
                "Incompatible license added: LicenseRef-scancode-unicode"
            )
        )

    def test_other_issue_types_match_on_substring(self):
        """Issues that are neither add nor change fall back to a substring check."""
        self.assertTrue(
            main.is_uncertain_license_issue("License deleted: LicenseRef-scancode-unknown")
        )
        self.assertFalse(
            main.is_uncertain_license_issue("No license added for source file: src/foo.c")
        )


class TestParseArgs(unittest.TestCase):
    """Tests for the command-line interface used by the action."""

    def test_positional_args_are_required(self):
        """The existing patch-file and repository arguments are preserved."""
        args = main.parse_args(["pr.patch", "org/repo"])
        self.assertEqual(args.patch_file, "pr.patch")
        self.assertEqual(args.repo_name, "org/repo")

    def test_missing_positional_exits(self):
        """Omitting a required positional argument fails through argparse."""
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main.parse_args(["pr.patch"])
        self.assertEqual(caught.exception.code, 2)


class TestBeautifyOutput(unittest.TestCase):
    """
    beautify_output is a pure rendering concern: it prints the report and
    returns. Exit-code semantics live in main() (see TestMainEntryPoint).
    """

    def render(self, flagged: dict, warnings: dict) -> str:
        """
        Call beautify_output, capturing its stdout.

        Args:
            flagged: Blocking-issue dictionary.
            warnings: Warning-issue dictionary.

        Returns:
            The captured stdout.
        """
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            main.beautify_output(flagged, warnings, "BSD-3-Clause-Clear", "PREFIX")
        return buffer.getvalue()

    def test_no_issues_reports_success(self):
        """A clean run reports success."""
        output = self.render({}, {})
        self.assertIn("No license or copyright issues detected", output)

    def test_blocking_issues_are_rendered(self):
        """Blocking issues are rendered under the blocking-errors heading."""
        flagged = {
            "src/a.c": {
                "license_issues": ["Incompatible license added: GPL-2.0-only"],
                "copyright_issues": [],
            },
            "src/b.c": {
                "license_issues": ["Incompatible license added: GPL-3.0-only"],
                "copyright_issues": [],
            },
        }
        output = self.render(flagged, {})
        self.assertIn("B L O C K I N G   E R R O R S", output)
        self.assertIn("src/a.c", output)

    def test_warnings_only_reports_warnings(self):
        """Warnings are reported without the blocking-errors heading."""
        warnings = {
            "src/c.c": {
                "license_issues": ["Incompatible license added: LicenseRef-scancode-unknown"],
                "copyright_issues": [],
            }
        }
        output = self.render({}, warnings)
        self.assertIn("W A R N I N G S", output)
        self.assertNotIn("B L O C K I N G", output)

    def test_copyright_issues_are_rendered(self):
        """Copyright issues appear under their own heading."""
        flagged = {
            "src/d.c": {
                "license_issues": [],
                "copyright_issues": ["Copyright deletions detected: ['Copyright (c) 2019 X']"],
            }
        }
        output = self.render(flagged, {})
        self.assertIn("COPYRIGHT ISSUES", output)

    def test_compliance_doc_is_referenced(self):
        """Every report links to COMPLIANCE.md."""
        flagged = {"src/e.c": {"license_issues": ["x"], "copyright_issues": []}}
        output = self.render(flagged, {})
        self.assertIn("COMPLIANCE.md", output)


class TestMainEntryPoint(LicenseFileTestCase):
    """
    main() wires the pieces together: resolve the repo license, pick the allowed
    list, run both checkers and route issues into blocking vs. warning buckets.
    """

    def run_main(self, argv: list, license_issues: dict, copyright_issues: dict):
        """
        Run main() with both checkers stubbed out.

        Args:
            argv: Replacement sys.argv.
            license_issues: Return value for LicenseChecker.run().
            copyright_issues: Return value for CopyrightChecker.run().

        Returns:
            Tuple of (captured stdout, exit code).
        """
        buffer = io.StringIO()
        license_checker = MagicMock()
        license_checker.run.return_value = license_issues
        copyright_checker = MagicMock()
        copyright_checker.run.return_value = copyright_issues

        with (
            mock_patch.object(sys, "argv", argv),
            mock_patch("main.Patch"),
            mock_patch("main.LicenseChecker", return_value=license_checker),
            mock_patch("main.CopyrightChecker", return_value=copyright_checker),
        ):
            with contextlib.redirect_stdout(buffer):
                with self.assertRaises(SystemExit) as caught:
                    main.main()
        return buffer.getvalue(), caught.exception.code

    def test_clean_run_exits_zero(self):
        """With no issues from either checker, main() exits 0."""
        _, code = self.run_main(["main.py", "pr.patch", "org/repo"], {}, {})
        self.assertEqual(code, 0)

    def test_blocking_license_issue_fails(self):
        """A real license issue produces a non-zero exit."""
        _, code = self.run_main(
            ["main.py", "pr.patch", "org/repo"],
            {"src/a.c": ["Incompatible license added: GPL-2.0-only"]},
            {},
        )
        self.assertEqual(code, 1)

    def test_uncertain_license_issue_is_a_warning(self):
        """An uncertain license issue is routed to warnings and exits 0."""
        output, code = self.run_main(
            ["main.py", "pr.patch", "org/repo"],
            {
                "src/a.c": [
                    "Incompatible license added: LicenseRef-scancode-unknown-license-reference"
                ]
            },
            {},
        )
        self.assertEqual(code, 0)
        self.assertIn("W A R N I N G S", output)

    def test_copyright_issue_blocks(self):
        """A copyright deletion is always a blocking issue."""
        _, code = self.run_main(
            ["main.py", "pr.patch", "org/repo"],
            {},
            {"src/a.c": ["Copyright deletions detected: ['Copyright (c) 2019 X']"]},
        )
        self.assertEqual(code, 1)

    def test_license_and_copyright_issues_merge_per_file(self):
        """Issues of both kinds on one file are merged into a single entry."""
        output, code = self.run_main(
            ["main.py", "pr.patch", "org/repo"],
            {"src/a.c": ["Incompatible license added: GPL-2.0-only"]},
            {"src/a.c": ["Copyright deletions detected: ['Copyright (c) 2019 X']"]},
        )
        self.assertEqual(code, 1)
        self.assertIn("LICENSE ISSUES", output)
        self.assertIn("COPYRIGHT ISSUES", output)

    def test_many_flagged_files_still_exit_one(self):
        """
        The exit code is 1 for any number of flagged files, never the count.

        A POSIX exit status is 8 bits, so handing the OS a file count masked
        every exact multiple of 256 back to 0 -- a PR breaking 256 files
        reported a passing build. The wrap happens at the OS boundary, which an
        in-process test cannot observe (SystemExit still carries the full int),
        so this pins the contract that removes the hazard: the value handed to
        sys.exit is always 1, whatever the count. Checks 255/256/257 to cover
        the boundary either side of the first wrap, and 512 for the second.
        """
        for file_count in (255, 256, 257, 512):
            with self.subTest(file_count=file_count):
                license_issues = {
                    f"src/file_{idx}.c": ["Incompatible license added: GPL-2.0-only"]
                    for idx in range(file_count)
                }
                output, code = self.run_main(
                    ["main.py", "pr.patch", "org/repo"], license_issues, {}
                )
                self.assertEqual(code, 1)
                self.assertEqual(output.count("F I L E:"), file_count)

    def test_permissive_repo_gets_permissive_allowed_list(self):
        """A permissive repo license selects the permissive allowed list."""
        with (
            mock_patch("main.get_license", return_value="MIT"),
            mock_patch("main.Patch"),
            mock_patch("main.CopyrightChecker") as copyright_cls,
            mock_patch("main.LicenseChecker") as license_cls,
        ):
            license_cls.return_value.run.return_value = {}
            copyright_cls.return_value.run.return_value = {}
            with mock_patch.object(sys, "argv", ["main.py", "pr.patch", "org/repo"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        main.main()
            self.assertEqual(license_cls.call_args[0][2], main.PERMISSIVE_LICENSES)

    def test_copyleft_repo_gets_copyleft_allowed_list(self):
        """A copyleft repo license selects the copyleft allowed list."""
        with (
            mock_patch("main.get_license", return_value="GPL-2.0-only"),
            mock_patch("main.Patch"),
            mock_patch("main.CopyrightChecker") as copyright_cls,
            mock_patch("main.LicenseChecker") as license_cls,
        ):
            license_cls.return_value.run.return_value = {}
            copyright_cls.return_value.run.return_value = {}
            with mock_patch.object(sys, "argv", ["main.py", "pr.patch", "org/repo"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        main.main()
            self.assertEqual(license_cls.call_args[0][2], main.COPYLEFT_LICENSES)

    def test_compound_expression_is_parsed_into_components(self):
        """An unrecognized compound expression is split into its components."""
        with (
            mock_patch("main.get_license", return_value="GPL-2.0-only AND MIT"),
            mock_patch("main.Patch"),
            mock_patch("main.CopyrightChecker") as copyright_cls,
            mock_patch("main.LicenseChecker") as license_cls,
        ):
            license_cls.return_value.run.return_value = {}
            copyright_cls.return_value.run.return_value = {}
            with mock_patch.object(sys, "argv", ["main.py", "pr.patch", "org/repo"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        main.main()
            self.assertEqual(license_cls.call_args[0][2], ["GPL-2.0-only", "MIT"])


class TestLicenseListsAreDisjoint(unittest.TestCase):
    """Sanity checks on the module-level license constants."""

    def test_permissive_and_copyleft_do_not_overlap(self):
        """No identifier appears in both the permissive and copyleft lists."""
        self.assertEqual(set(main.PERMISSIVE_LICENSES) & set(main.COPYLEFT_LICENSES), set())

    def test_default_license_is_permissive(self):
        """The fallback default license is in the permissive list."""
        self.assertIn("BSD-3-Clause-Clear", main.PERMISSIVE_LICENSES)


if __name__ == "__main__":
    unittest.main()

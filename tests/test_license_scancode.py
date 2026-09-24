"""
Tests for scanner.license_scancode.LicenseChecker.

scancode is invoked as a CLI subprocess and never imported, so these tests mock
subprocess.run and the JSON file it writes. That keeps the suite fast and avoids
depending on the multi-hundred-megabyte scancode-toolkit package.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

from scanner.copyright_checker import DEFAULT_INTERNAL_ENTITIES
from scanner.license_scancode import LicenseChecker
from scanner.licenses import PROPRIETARY_LICENSE
from tests.scancode_mock import scancode_mock_patcher

PERMISSIVE = [
    "BSD-3-Clause",
    "BSD-3-Clause-Clear",
    "MIT",
    "Apache-2.0",
    "ISC",
    "LicenseRef-scancode-unicode",
]


def make_patch_obj(changes: list) -> MagicMock:
    """
    Build a stub Patch exposing only the .changes attribute.

    Args:
        changes: List of change dictionaries.

    Returns:
        A stub object with a .changes attribute.
    """
    stub = MagicMock()
    stub.changes = changes
    return stub


def make_change(
    content: str,
    change_type: str = "MODIFIED",
    path_name: str = "src/foo.c",
    file_type: str = "source",
) -> dict:
    """
    Build a single change dictionary in the shape Patch produces.

    Args:
        content: Diff content for the file.
        change_type: One of ADDED/MODIFIED/DELETED/RENAMED/RENAMED_MODIFIED.
        path_name: File path.
        file_type: Either 'source' or 'binary'.

    Returns:
        A change dictionary.
    """
    return {
        "path_name": path_name,
        "file_type": file_type,
        "change_type": change_type,
        "content": content,
    }


class ScancodeMockMixin:
    """Provides a subprocess.run replacement that writes a fake scancode report."""

    def install_scancode_mock(self, detections: dict):
        """
        Patch subprocess.run so it writes a scancode-shaped JSON report.

        Args:
            detections: Maps scanned filename (e.g. '0_added.txt') to either an
                SPDX expression string, or None for 'no license detected'.
        """

        patcher = scancode_mock_patcher(detections)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestIsSourceFile(unittest.TestCase):
    """Source-file extension detection."""

    def setUp(self):
        """Create a checker with an empty patch."""
        self.checker = LicenseChecker(make_patch_obj([]), PERMISSIVE)

    def test_known_source_extensions(self):
        """Recognized code extensions are source files."""
        for name in (
            "a.c",
            "a.cpp",
            "a.h",
            "a.hpp",
            "a.java",
            "a.py",
            "a.js",
            "a.ts",
            "a.rb",
            "a.go",
            "a.swift",
            "a.kt",
            "a.kts",
            "a.sh",
        ):
            self.assertTrue(self.checker.is_source_file(name), name)

    def test_non_source_extensions(self):
        """Other extensions are not source files."""
        for name in ("a.txt", "a.cfg", "a.png", "Makefile"):
            self.assertFalse(self.checker.is_source_file(name), name)


class TestLicenseCheckerModePlumbing(unittest.TestCase):
    """
    Constructor defaults for mode/proprietary_entities.
    """

    def test_mode_defaults_to_opensource(self):
        """With no mode argument, the checker defaults to opensource."""
        checker = LicenseChecker(make_patch_obj([]), PERMISSIVE)
        self.assertEqual(checker.mode, "opensource")

    def test_proprietary_entities_defaults_to_module_default(self):
        """With no proprietary_entities argument, the module default is used."""
        checker = LicenseChecker(make_patch_obj([]), PERMISSIVE)
        self.assertEqual(checker.proprietary_entities, DEFAULT_INTERNAL_ENTITIES)

    def test_mode_and_entities_are_stored_when_provided(self):
        """Explicit mode/proprietary_entities arguments are stored as given."""
        checker = LicenseChecker(
            make_patch_obj([]),
            PERMISSIVE,
            mode="proprietary",
            proprietary_entities=["Acme Robotics"],
        )
        self.assertEqual(checker.mode, "proprietary")
        self.assertEqual(checker.proprietary_entities, ["Acme Robotics"])


class TestDetectLicensesBatch(ScancodeMockMixin, unittest.TestCase):
    """Batch scanning splits added and deleted lines into separate scans."""

    def test_added_and_deleted_are_scanned_separately(self):
        """Added and deleted line groups get independent results."""
        self.install_scancode_mock({"0_added.txt": "MIT", "0_deleted.txt": "BSD-3-Clause"})
        checker = LicenseChecker(make_patch_obj([]), PERMISSIVE)
        results = checker.detect_licenses_batch(
            [make_change("+MIT license text\n-BSD license text\n")]
        )
        self.assertEqual(results[(0, "added")], "MIT")
        self.assertEqual(results[(0, "deleted")], "BSD-3-Clause")

    def test_empty_content_is_skipped(self):
        """A change with no content produces no scan results."""
        self.install_scancode_mock({})
        checker = LicenseChecker(make_patch_obj([]), PERMISSIVE)
        self.assertEqual(checker.detect_licenses_batch([make_change(None)]), {})

    def test_no_detection_omits_entry(self):
        """A scanned file with no license detections yields a falsy result."""
        self.install_scancode_mock({"0_added.txt": None})
        checker = LicenseChecker(make_patch_obj([]), PERMISSIVE)
        results = checker.detect_licenses_batch([make_change("+just some code\n")])
        self.assertFalse(results.get((0, "added")))

    def test_multiple_changes_share_a_single_subprocess_call(self):
        """
        All changes are batched into one scancode invocation, not one
        subprocess.run per change.
        """

        def fake_run(cmd, **_kwargs):
            output_file = cmd[cmd.index("--json-pp") + 1]
            Path(output_file).write_text(json.dumps({"files": []}), encoding="utf-8")
            return MagicMock(returncode=0)

        checker = LicenseChecker(make_patch_obj([]), PERMISSIVE)
        with mock_patch(
            "scanner.license_scancode.subprocess.run", side_effect=fake_run
        ) as run_mock:
            checker.detect_licenses_batch(
                [
                    make_change("+MIT text\n"),
                    make_change("+Apache text\n"),
                    make_change("-BSD text\n"),
                ]
            )
        self.assertEqual(run_mock.call_count, 1)


class TestRunLicenseRules(ScancodeMockMixin, unittest.TestCase):
    """End-to-end rule evaluation in run()."""

    def run_checker(self, changes: list, detections: dict, allowed: list = None) -> dict:
        """
        Install the scancode mock and run the checker.

        Args:
            changes: Change dictionaries to evaluate.
            detections: Filename -> SPDX expression (or None) mapping.
            allowed: Allowed license list; defaults to the permissive set.

        Returns:
            The blocking-files dictionary.
        """
        self.install_scancode_mock(detections)
        checker = LicenseChecker(make_patch_obj(changes), allowed or PERMISSIVE)
        flagged, _warnings = checker.run()
        return flagged

    def test_incompatible_license_added_is_flagged(self):
        """Adding a copyleft license to a permissive repo is flagged."""
        flagged = self.run_checker([make_change("+GPL text\n")], {"0_added.txt": "GPL-2.0-only"})
        self.assertIn("Incompatible license added: GPL-2.0-only", flagged["src/foo.c"][0])

    def test_permissive_license_added_is_not_flagged(self):
        """Adding a permissive license to a permissive repo is allowed."""
        flagged = self.run_checker([make_change("+MIT text\n")], {"0_added.txt": "MIT"})
        self.assertEqual(flagged, {})

    def test_license_deleted_without_replacement_is_flagged(self):
        """Removing a license with nothing added is flagged."""
        flagged = self.run_checker([make_change("-MIT text\n")], {"0_deleted.txt": "MIT"})
        self.assertIn("License deleted: MIT", flagged["src/foo.c"][0])

    def test_license_changed_to_copyleft_is_flagged(self):
        """Swapping a permissive license for a copyleft one is flagged."""
        flagged = self.run_checker(
            [make_change("+GPL text\n-MIT text\n")],
            {"0_added.txt": "GPL-2.0-only", "0_deleted.txt": "MIT"},
        )
        self.assertIn(
            "License deleted: MIT and license added: GPL-2.0-only", flagged["src/foo.c"][0]
        )

    def test_renamed_modified_license_change_is_flagged(self):
        """A renamed-and-modified file receives normal license checks."""
        flagged = self.run_checker(
            [make_change("+GPL text\n-MIT text\n", change_type="RENAMED_MODIFIED")],
            {"0_added.txt": "GPL-2.0-only", "0_deleted.txt": "MIT"},
        )
        self.assertIn(
            "License deleted: MIT and license added: GPL-2.0-only", flagged["src/foo.c"][0]
        )

    def test_license_changed_to_permissive_is_allowed(self):
        """Swapping one permissive license for another is allowed."""
        flagged = self.run_checker(
            [make_change("+Apache text\n-MIT text\n")],
            {"0_added.txt": "Apache-2.0", "0_deleted.txt": "MIT"},
        )
        self.assertEqual(flagged, {})

    def test_new_source_file_without_license_is_flagged(self):
        """An ADDED source file with no detected license is flagged."""
        flagged = self.run_checker(
            [make_change("+int main(void) { return 0; }\n", change_type="ADDED")],
            {"0_added.txt": None},
        )
        self.assertIn("No license added for source file", flagged["src/foo.c"][0])

    def test_new_non_source_file_without_license_is_not_flagged(self):
        """An ADDED non-source file with no license is not flagged."""
        flagged = self.run_checker(
            [make_change("+some data\n", change_type="ADDED", path_name="data/blob.txt")],
            {"0_added.txt": None},
        )
        self.assertEqual(flagged, {})

    def test_binary_changes_are_skipped(self):
        """Binary changes are excluded before scanning."""
        flagged = self.run_checker([make_change("+data\n", file_type="binary")], {})
        self.assertEqual(flagged, {})

    def test_no_source_files_returns_empty(self):
        """With no source changes, run() short-circuits."""
        checker = LicenseChecker(make_patch_obj([]), PERMISSIVE)
        self.assertEqual(checker.run(), ({}, {}))


class TestRunChangeTypeCoverageGaps(ScancodeMockMixin, unittest.TestCase):
    """
    Whole-file deletions and pure renames are not license-checked. Renamed
    files carrying content changes receive the same checks as modifications.
    """

    def test_deleted_change_type_is_not_license_checked(self):
        """Deleting a file removes its license without being flagged."""
        self.install_scancode_mock({"0_deleted.txt": "MIT"})
        checker = LicenseChecker(
            make_patch_obj([make_change("-MIT text\n", change_type="DELETED")]),
            PERMISSIVE,
        )
        self.assertEqual(checker.run(), ({}, {}))

    def test_renamed_change_type_is_not_license_checked(self):
        """Pure RENAMED changes are not license-checked."""
        self.install_scancode_mock({"0_deleted.txt": "MIT"})
        checker = LicenseChecker(
            make_patch_obj([make_change("-MIT text\n", change_type="RENAMED")]),
            PERMISSIVE,
        )
        self.assertEqual(checker.run(), ({}, {}))


class TestLicenseComparisonFix(ScancodeMockMixin, unittest.TestCase):
    """
    Regression test for a fixed string/list type confusion at
    license_scancode.py:229. detect_licenses_batch returns license expressions
    as strings, but run() used to compare them with set(added) != set(deleted)
    -- comparing sets of *characters*, not licenses, so anagram pairs like
    'MIT'/'TIM' compared equal. Now compared as plain strings.
    """

    def test_anagram_licenses_are_treated_as_a_real_change(self):
        """'MIT' and 'TIM' are not the same license and must be flagged as such."""
        self.install_scancode_mock({"0_added.txt": "TIM", "0_deleted.txt": "MIT"})
        checker = LicenseChecker(
            make_patch_obj([make_change("+TIM text\n-MIT text\n")]),
            PERMISSIVE,
        )
        flagged, _warnings = checker.run()
        self.assertIn("License deleted: MIT and license added: TIM", flagged["src/foo.c"][0])


class TestRunSeverityRouting(ScancodeMockMixin, unittest.TestCase):
    """LicenseChecker assigns warning versus blocking severity."""

    def run_checker(self, changes: list, detections: dict) -> tuple:
        """Install the ScanCode mock and return the checker result buckets."""
        self.install_scancode_mock(detections)
        return LicenseChecker(make_patch_obj(changes), PERMISSIVE).run()

    def test_unknown_addition_is_a_warning(self):
        """An unrecognized ScanCode reference does not block the action."""
        flagged, warnings = self.run_checker(
            [make_change("+unknown license\n")],
            {"0_added.txt": "LicenseRef-scancode-unknown-license-reference"},
        )
        self.assertEqual(flagged, {})
        self.assertIn("Incompatible license added", warnings["src/foo.c"][0])

    def test_mixed_gpl_and_unknown_addition_is_blocking(self):
        """A known incompatible component keeps a mixed expression blocking."""
        flagged, warnings = self.run_checker(
            [make_change("+license\n")],
            {"0_added.txt": "GPL-2.0-only AND LicenseRef-scancode-unknown-license-reference"},
        )
        self.assertIn("src/foo.c", flagged)
        self.assertEqual(warnings, {})

    def test_solitary_proprietary_marker_is_blocking(self):
        """The existing proprietary marker remains a blocking issue."""
        flagged, warnings = self.run_checker(
            [make_change("+license\n")],
            {"0_added.txt": "LicenseRef-scancode-proprietary-license"},
        )
        self.assertIn("src/foo.c", flagged)
        self.assertEqual(warnings, {})

    def test_unknown_deletion_is_a_warning(self):
        """Deletion-only ScanCode references preserve the legacy warning behavior."""
        flagged, warnings = self.run_checker(
            [make_change("-unknown license\n")],
            {"0_deleted.txt": "LicenseRef-scancode-unknown-license-reference"},
        )
        self.assertEqual(flagged, {})
        self.assertIn("License deleted", warnings["src/foo.c"][0])


class TestRunProprietaryMode(ScancodeMockMixin, unittest.TestCase):
    """Proprietary-mode rule modifiers."""

    def run_checker(
        self, changes: list, detections: dict, allowed: list = None, entities: list = None
    ) -> tuple:
        """Install the ScanCode mock and run the checker in proprietary mode."""
        self.install_scancode_mock(detections)
        checker = LicenseChecker(
            make_patch_obj(changes),
            allowed or PERMISSIVE,
            mode="proprietary",
            proprietary_entities=entities,
        )
        return checker.run()

    def test_permissive_addition_is_a_notice_warning(self):
        """A permissive OSS addition warns instead of blocking."""
        flagged, warnings = self.run_checker(
            [make_change("+MIT text\n", change_type="ADDED")], {"0_added.txt": "MIT"}
        )
        self.assertEqual(flagged, {})
        self.assertIn("Permissive open-source license added: MIT", warnings["src/foo.c"][0])
        self.assertIn("NOTICE", warnings["src/foo.c"][0])

    def test_unchanged_permissive_license_does_not_warn(self):
        """An unchanged license does not warn."""
        flagged, warnings = self.run_checker(
            [make_change("+MIT text\n-MIT text\n")],
            {"0_added.txt": "MIT", "0_deleted.txt": "MIT"},
        )
        self.assertEqual(flagged, {})
        self.assertEqual(warnings, {})

    def test_removing_proprietary_marking_blocks_without_notice_warning(self):
        """Proprietary removal blocks and suppresses the permissive-addition warning."""
        flagged, warnings = self.run_checker(
            [make_change("+MIT text\n-proprietary text\n")],
            {"0_added.txt": "MIT", "0_deleted.txt": PROPRIETARY_LICENSE},
        )
        self.assertIn("Proprietary license statement removed", flagged["src/foo.c"][0])
        self.assertEqual(warnings, {})

    def test_removing_proprietary_marker_from_compound_expression_blocks(self):
        """Removing the marker from one component of a compound expression still blocks."""
        flagged, _warnings = self.run_checker(
            [make_change("+MIT text\n-mixed text\n")],
            {"0_added.txt": "MIT", "0_deleted.txt": f"{PROPRIETARY_LICENSE} AND GPL-2.0-only"},
        )
        self.assertIn("Proprietary license statement removed", flagged["src/foo.c"][0])

    def test_permissive_added_while_proprietary_retained_warns(self):
        """A retained proprietary marker is ignored for permissiveness checks."""
        flagged, warnings = self.run_checker(
            [make_change("+MIT text\n-proprietary text\n")],
            {"0_added.txt": f"MIT AND {PROPRIETARY_LICENSE}", "0_deleted.txt": PROPRIETARY_LICENSE},
        )
        self.assertEqual(flagged, {})
        self.assertIn("Permissive open-source license added", warnings["src/foo.c"][0])

    def test_copyleft_added_while_proprietary_retained_still_blocks(self):
        """Excluding the retained marker does not excuse a copyleft addition."""
        flagged, warnings = self.run_checker(
            [make_change("+GPL text\n-proprietary text\n")],
            {
                "0_added.txt": f"GPL-2.0-only AND {PROPRIETARY_LICENSE}",
                "0_deleted.txt": PROPRIETARY_LICENSE,
            },
        )
        self.assertIn("src/foo.c", flagged)
        self.assertEqual(warnings, {})

    def test_solitary_proprietary_license_is_silent(self):
        """A solitary proprietary-license detection raises no issue in proprietary mode."""
        flagged, warnings = self.run_checker(
            [make_change("+proprietary header\n", change_type="ADDED")],
            {"0_added.txt": PROPRIETARY_LICENSE},
        )
        self.assertEqual(flagged, {})
        self.assertEqual(warnings, {})

    def test_permissive_swapped_for_proprietary_blocks_with_distinct_message(self):
        """Deleting a real license and adding a proprietary marker is still relicensing."""
        flagged, warnings = self.run_checker(
            [make_change("+proprietary text\n-MIT text\n")],
            {"0_added.txt": PROPRIETARY_LICENSE, "0_deleted.txt": "MIT"},
        )
        self.assertIn("attribution terms are not extinguished", flagged["src/foo.c"][0])
        self.assertEqual(warnings, {})

    def test_marker_stripped_from_compound_deleted_expression_blocks(self):
        """Keeping only the proprietary marker still reports the deleted real license."""
        flagged, _warnings = self.run_checker(
            [make_change("+proprietary text\n-mixed text\n")],
            {"0_added.txt": PROPRIETARY_LICENSE, "0_deleted.txt": f"MIT AND {PROPRIETARY_LICENSE}"},
        )
        self.assertIn("attribution terms are not extinguished", flagged["src/foo.c"][0])

    def test_proprietary_marking_on_both_sides_is_silent(self):
        """Reformatting a proprietary marker is not a license change."""
        flagged, warnings = self.run_checker(
            [make_change("+proprietary text\n-proprietary text\n")],
            {"0_added.txt": PROPRIETARY_LICENSE, "0_deleted.txt": PROPRIETARY_LICENSE},
        )
        self.assertEqual(flagged, {})
        self.assertEqual(warnings, {})

    def test_new_file_with_internal_copyright_and_no_license_is_not_blocked(self):
        """A recognized internal copyright satisfies the proprietary new-file rule."""
        content = "+Copyright (c) 2024 Qualcomm Technologies, Inc.\n+int main(void) {}\n"
        flagged, warnings = self.run_checker(
            [make_change(content, change_type="ADDED")], {"0_added.txt": None}
        )
        self.assertEqual(flagged, {})
        self.assertEqual(warnings, {})

    def test_new_file_with_custom_entity_and_no_license_is_not_blocked(self):
        """A configured custom entity is honored."""
        content = "+Copyright (c) 2024 Acme Robotics\n+int main(void) {}\n"
        flagged, _warnings = self.run_checker(
            [make_change(content, change_type="ADDED")],
            {"0_added.txt": None},
            entities=["Acme Robotics"],
        )
        self.assertEqual(flagged, {})

    def test_new_file_with_no_copyright_and_no_license_blocks_distinctly(self):
        """A file with neither license nor internal copyright still blocks."""
        flagged, _warnings = self.run_checker(
            [make_change("+int main(void) {}\n", change_type="ADDED")], {"0_added.txt": None}
        )
        message = flagged["src/foo.c"][0]
        self.assertIn("scan team/legal", message)
        self.assertNotEqual(message, "No license added for source file: src/foo.c")


if __name__ == "__main__":
    unittest.main()

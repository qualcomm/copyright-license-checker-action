"""
Tests for scanner.license_scancode.LicenseChecker.

scancode is invoked as a CLI subprocess and never imported, so these tests mock
subprocess.run and the JSON file it writes. That keeps the suite fast and avoids
depending on the multi-hundred-megabyte scancode-toolkit package.
"""

import unittest
from unittest.mock import MagicMock

from scanner.license_scancode import LicenseChecker
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
        change_type: One of ADDED/MODIFIED/DELETED/RENAMED.
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
        self.checker = LicenseChecker(make_patch_obj([]), "org/repo", PERMISSIVE)

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


class TestDetectLicensesBatch(ScancodeMockMixin, unittest.TestCase):
    """Batch scanning splits added and deleted lines into separate scans."""

    def test_added_and_deleted_are_scanned_separately(self):
        """Added and deleted line groups get independent results."""
        self.install_scancode_mock({"0_added.txt": "MIT", "0_deleted.txt": "BSD-3-Clause"})
        checker = LicenseChecker(make_patch_obj([]), "org/repo", PERMISSIVE)
        results = checker.detect_licenses_batch(
            [make_change("+MIT license text\n-BSD license text\n")]
        )
        self.assertEqual(results[(0, "added")], "MIT")
        self.assertEqual(results[(0, "deleted")], "BSD-3-Clause")

    def test_empty_content_is_skipped(self):
        """A change with no content produces no scan results."""
        self.install_scancode_mock({})
        checker = LicenseChecker(make_patch_obj([]), "org/repo", PERMISSIVE)
        self.assertEqual(checker.detect_licenses_batch([make_change(None)]), {})

    def test_no_detection_omits_entry(self):
        """A scanned file with no license detections yields a falsy result."""
        self.install_scancode_mock({"0_added.txt": None})
        checker = LicenseChecker(make_patch_obj([]), "org/repo", PERMISSIVE)
        results = checker.detect_licenses_batch([make_change("+just some code\n")])
        self.assertFalse(results.get((0, "added")))


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
            The flagged-files dictionary.
        """
        self.install_scancode_mock(detections)
        checker = LicenseChecker(make_patch_obj(changes), "org/repo", allowed or PERMISSIVE)
        return checker.run()

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
        checker = LicenseChecker(make_patch_obj([]), "org/repo", PERMISSIVE)
        self.assertEqual(checker.run(), {})


class TestRunChangeTypeCoverageGaps(ScancodeMockMixin, unittest.TestCase):
    """
    Documents pre-existing gaps: license rules apply only to MODIFIED and ADDED
    changes. DELETED and RENAMED changes are never license-checked. These assert
    current behavior, not desired behavior.
    """

    def test_deleted_change_type_is_not_license_checked(self):
        """Deleting a file removes its license without being flagged."""
        self.install_scancode_mock({"0_deleted.txt": "MIT"})
        checker = LicenseChecker(
            make_patch_obj([make_change("-MIT text\n", change_type="DELETED")]),
            "org/repo",
            PERMISSIVE,
        )
        self.assertEqual(checker.run(), {})

    def test_renamed_change_type_is_not_license_checked(self):
        """RENAMED changes are not license-checked."""
        self.install_scancode_mock({"0_deleted.txt": "MIT"})
        checker = LicenseChecker(
            make_patch_obj([make_change("-MIT text\n", change_type="RENAMED")]),
            "org/repo",
            PERMISSIVE,
        )
        self.assertEqual(checker.run(), {})


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
            "org/repo",
            PERMISSIVE,
        )
        flagged = checker.run()
        self.assertIn("License deleted: MIT and license added: TIM", flagged["src/foo.c"][0])


if __name__ == "__main__":
    unittest.main()

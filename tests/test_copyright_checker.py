"""
Tests for scanner.copyright_checker.CopyrightChecker.

These pin down current behavior — including the sanctioned QUIC -> QTI rebrand
exception and the fact that only MODIFIED changes are checked — before the
proprietary-mode work adds an independent entity-matching helper.
"""

import unittest
from unittest.mock import MagicMock

from scanner.copyright_checker import CopyrightChecker


def make_patch(changes: list) -> MagicMock:
    """
    Build a stub Patch exposing only the .changes attribute the checker reads.

    Args:
        changes: List of change dictionaries.

    Returns:
        A stub object with a .changes attribute.
    """
    patch = MagicMock()
    patch.changes = changes
    return patch


def make_change(content: str, change_type: str = "MODIFIED", path_name: str = "src/foo.c", file_type: str = "source") -> dict:
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


class TestNormalizeString(unittest.TestCase):
    """normalize_string strips everything except alphabetic characters."""

    def setUp(self):
        """Create a checker with an empty patch."""
        self.checker = CopyrightChecker(make_patch([]))

    def test_strips_digits_and_punctuation(self):
        """Years, punctuation and spaces are removed."""
        self.assertEqual(
            self.checker.normalize_string("Copyright (c) 2024 Acme, Inc."),
            "CopyrightcAcmeInc",
        )

    def test_years_do_not_affect_equality(self):
        """Two statements differing only by year normalize identically."""
        self.assertEqual(
            self.checker.normalize_string("Copyright (c) 2019 Acme"),
            self.checker.normalize_string("Copyright (c) 2024 Acme"),
        )


class TestDetectCopyrightChanges(unittest.TestCase):
    """detect_copyright_changes splits added vs. deleted copyright lines."""

    def setUp(self):
        """Create a checker with an empty patch."""
        self.checker = CopyrightChecker(make_patch([]))

    def test_detects_added_and_deleted(self):
        """Added and deleted copyright lines are returned separately."""
        content = "+Copyright (c) 2024 New Author\n-Copyright (c) 2019 Old Author\n"
        added, deleted = self.checker.detect_copyright_changes(content)
        self.assertEqual(len(added), 1)
        self.assertEqual(len(deleted), 1)
        self.assertIn("New Author", added[0][0])
        self.assertIn("Old Author", deleted[0][0])

    def test_ignores_lines_without_copyright_keyword(self):
        """Only lines containing 'Copyright' are considered."""
        added, deleted = self.checker.detect_copyright_changes("+int x = 1;\n-int y = 2;\n")
        self.assertEqual(added, [])
        self.assertEqual(deleted, [])

    def test_non_string_content_returns_empty(self):
        """None content (e.g. a rename) yields empty lists rather than raising."""
        self.assertEqual(self.checker.detect_copyright_changes(None), ([], []))


class TestCopyrightDeletions(unittest.TestCase):
    """run() flags copyright statements removed without replacement."""

    def test_deleted_copyright_is_flagged(self):
        """A deleted copyright with no matching addition is flagged."""
        content = "-Copyright (c) 2019 Some Other Author. All rights reserved.\n"
        checker = CopyrightChecker(make_patch([make_change(content)]))
        flagged = checker.run()
        self.assertIn("src/foo.c", flagged)
        self.assertIn("Copyright deletions detected", flagged["src/foo.c"][0])

    def test_added_copyright_only_is_not_flagged(self):
        """Adding a copyright without deleting one is never flagged."""
        content = "+Copyright (c) 2024 Qualcomm Technologies, Inc. and/or its subsidiaries.\n"
        checker = CopyrightChecker(make_patch([make_change(content)]))
        self.assertEqual(checker.run(), {})

    def test_reworded_year_only_change_is_not_flagged(self):
        """A year-only change normalizes to the same string, so it is not flagged."""
        content = "-Copyright (c) 2019 Acme Corp\n+Copyright (c) 2024 Acme Corp\n"
        checker = CopyrightChecker(make_patch([make_change(content)]))
        self.assertEqual(checker.run(), {})

    def test_binary_files_are_skipped(self):
        """Only file_type == 'source' changes are examined."""
        content = "-Copyright (c) 2019 Some Other Author.\n"
        checker = CopyrightChecker(make_patch([make_change(content, file_type="binary")]))
        self.assertEqual(checker.run(), {})


class TestAllowedTransitions(unittest.TestCase):
    """The one sanctioned QUIC -> QTI rebrand exception."""

    def test_quic_to_qti_transition_is_allowed(self):
        """Deleting the QUIC statement is excused when the QTI one is added."""
        content = (
            "-Copyright (c) 2022 Qualcomm Innovation Center, Inc. All rights reserved.\n"
            "+Copyright (c) 2024 Qualcomm Technologies, Inc. and/or its subsidiaries.\n"
        )
        checker = CopyrightChecker(make_patch([make_change(content)]))
        self.assertEqual(checker.run(), {})

    def test_quic_deletion_without_qti_addition_is_flagged(self):
        """Deleting the QUIC statement with no QTI replacement is still flagged."""
        content = "-Copyright (c) 2022 Qualcomm Innovation Center, Inc. All rights reserved.\n"
        checker = CopyrightChecker(make_patch([make_change(content)]))
        self.assertIn("src/foo.c", checker.run())

    def test_qti_addition_does_not_excuse_unrelated_deletion(self):
        """
        The exception is specific to the QUIC statement: adding the QTI line does
        not license the removal of some third party's copyright.
        """
        content = (
            "-Copyright (c) 2019 Unrelated Third Party, Ltd.\n"
            "+Copyright (c) 2024 Qualcomm Technologies, Inc. and/or its subsidiaries.\n"
        )
        checker = CopyrightChecker(make_patch([make_change(content)]))
        self.assertIn("src/foo.c", checker.run())

    def test_short_qti_form_does_not_excuse_quic_deletion(self):
        """
        The transition requires the full 'and/or its subsidiaries' wording. The
        short 'Qualcomm Technologies, Inc.' form must NOT satisfy it — this guards
        the boundary that proprietary mode's entity helper must not cross.
        """
        content = (
            "-Copyright (c) 2022 Qualcomm Innovation Center, Inc. All rights reserved.\n"
            "+Copyright (c) 2024 Qualcomm Technologies, Inc.\n"
        )
        checker = CopyrightChecker(make_patch([make_change(content)]))
        self.assertIn("src/foo.c", checker.run())


class TestChangeTypeCoverageGaps(unittest.TestCase):
    """
    Documents pre-existing coverage gaps: only MODIFIED changes are checked for
    copyright deletions. ADDED, DELETED and RENAMED changes are not. These tests
    assert current behavior rather than desired behavior.
    """

    def test_added_change_type_is_not_copyright_checked(self):
        """Copyright deletions inside an ADDED change are not flagged."""
        content = "-Copyright (c) 2019 Some Other Author.\n"
        checker = CopyrightChecker(make_patch([make_change(content, change_type="ADDED")]))
        self.assertEqual(checker.run(), {})

    def test_deleted_change_type_is_not_copyright_checked(self):
        """Deleting a whole file removes its copyright without being flagged."""
        content = "-Copyright (c) 2019 Some Other Author.\n"
        checker = CopyrightChecker(make_patch([make_change(content, change_type="DELETED")]))
        self.assertEqual(checker.run(), {})

    def test_renamed_change_type_is_not_copyright_checked(self):
        """RENAMED changes are not copyright checked."""
        content = "-Copyright (c) 2019 Some Other Author.\n"
        checker = CopyrightChecker(make_patch([make_change(content, change_type="RENAMED")]))
        self.assertEqual(checker.run(), {})


if __name__ == "__main__":
    unittest.main()

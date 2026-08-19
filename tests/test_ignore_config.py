"""
Tests for scanner.ignore_config.IgnoreConfig.

Covers .licenseignore loading, comment/blank handling and gitwildmatch matching.
"""

import tempfile
import unittest
from pathlib import Path

from scanner.ignore_config import IgnoreConfig


class IgnoreConfigTestCase(unittest.TestCase):
    """Base case providing a scratch directory for ignore files."""

    def setUp(self):
        """Create a temporary directory for ignore files."""
        # pylint: disable=consider-using-with
        # A `with` block can't span setUp/tearDown; addCleanup is the correct
        # unittest idiom for scoping a TemporaryDirectory to the test lifetime.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write_ignore(self, content: str) -> str:
        """
        Write an ignore file and return its path.

        Args:
            content: Ignore-file text.

        Returns:
            Path to the written file.
        """
        path = Path(self.tmp.name, ".licenseignore")
        path.write_text(content, encoding="utf-8")
        return str(path)


class TestIgnoreConfigLoading(IgnoreConfigTestCase):
    """Pattern loading and parsing."""

    def test_missing_file_yields_no_patterns(self):
        """An absent ignore file leaves the config inert."""
        config = IgnoreConfig(str(Path(self.tmp.name, "nope")))
        self.assertEqual(config.patterns, [])
        self.assertIsNone(config.spec)

    def test_comments_and_blanks_are_skipped(self):
        """Comment and blank lines are not treated as patterns."""
        config = IgnoreConfig(
            self.write_ignore("# a comment\n\nvendor/**\n\n  # indented comment\n")
        )
        self.assertEqual(config.patterns, ["vendor/**"])

    def test_patterns_are_stripped(self):
        """Surrounding whitespace is removed from each pattern."""
        config = IgnoreConfig(self.write_ignore("  vendor/**  \n"))
        self.assertEqual(config.patterns, ["vendor/**"])


class TestIgnoreConfigMatching(IgnoreConfigTestCase):
    """Path matching behavior."""

    def test_missing_file_excludes_nothing(self):
        """With no ignore file, no path is excluded."""
        config = IgnoreConfig(str(Path(self.tmp.name, "nope")))
        self.assertFalse(config.is_excluded("vendor/thing.c"))

    def test_directory_glob_matches_nested_paths(self):
        """A '**' pattern matches nested paths."""
        config = IgnoreConfig(self.write_ignore("vendor/**\n"))
        self.assertTrue(config.is_excluded("vendor/lib/thing.c"))

    def test_non_matching_path_is_kept(self):
        """Paths outside the patterns are not excluded."""
        config = IgnoreConfig(self.write_ignore("vendor/**\n"))
        self.assertFalse(config.is_excluded("src/thing.c"))

    def test_extension_glob_matches(self):
        """A '*.ext' pattern matches by extension at any depth."""
        config = IgnoreConfig(self.write_ignore("*.generated.js\n"))
        self.assertTrue(config.is_excluded("web/app.generated.js"))

    def test_multiple_patterns_are_all_applied(self):
        """Every listed pattern participates in matching."""
        config = IgnoreConfig(self.write_ignore("vendor/**\nthird_party/**\n"))
        self.assertTrue(config.is_excluded("vendor/a.c"))
        self.assertTrue(config.is_excluded("third_party/b.c"))
        self.assertFalse(config.is_excluded("src/c.c"))


if __name__ == "__main__":
    unittest.main()

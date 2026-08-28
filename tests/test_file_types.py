"""
Tie extension lists documented in README.md/COMPLIANCE.md to scanner.file_types.
"""

import re
import unittest
from pathlib import Path

from scanner.file_types import EXCLUDED_EXTENSIONS, SOURCE_EXTENSIONS

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


class TestReadmeMatchesFileTypes(unittest.TestCase):
    """README.md's documented extension lists match scanner.file_types."""

    def setUp(self):
        """Load README.md once per test."""
        self.readme = _read("README.md")

    def test_source_extensions_match(self):
        """The quoted source-extension list matches SOURCE_EXTENSIONS."""
        block = re.search(
            r"### Source File Identification.*?```text\n(.*?)\n```",
            self.readme,
            re.DOTALL,
        )
        self.assertIsNotNone(block, "Could not find the source-extensions code block")
        documented = tuple(re.findall(r"'(\.[A-Za-z0-9]+)'", block.group(1)))
        self.assertEqual(documented, SOURCE_EXTENSIONS)

    def test_excluded_extensions_match(self):
        """The 'Excluded File Types' bullet list matches EXCLUDED_EXTENSIONS."""
        block = re.search(r"\*\*Excluded File Types\*\*:.*?(?=\n###|\Z)", self.readme, re.DOTALL)
        self.assertIsNotNone(block, "Could not find the 'Excluded File Types' section")
        documented = set(re.findall(r"`(\.[A-Za-z0-9]+)`", block.group(0)))
        self.assertEqual(documented, set(EXCLUDED_EXTENSIONS))


class TestComplianceDocMatchesFileTypes(unittest.TestCase):
    """COMPLIANCE.md's documented source-extension list matches scanner.file_types."""

    def test_source_extensions_match(self):
        """The comma-separated source-extension list matches SOURCE_EXTENSIONS."""
        compliance = _read("COMPLIANCE.md")
        block = re.search(
            r"\*\*Supported source file extensions:\*\*\n```\n(.*?)\n```",
            compliance,
            re.DOTALL,
        )
        self.assertIsNotNone(block, "Could not find the supported-extensions code block")
        documented = tuple(ext.strip() for ext in block.group(1).split(","))
        self.assertEqual(documented, SOURCE_EXTENSIONS)


if __name__ == "__main__":
    unittest.main()

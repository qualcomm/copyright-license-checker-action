# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""
File-type extension lists shared across the scanner.

Single source of truth for the two lists documented in README.md and
COMPLIANCE.md: Patch's exclusions and LicenseChecker's source-file inclusions.
"""

# Files skipped entirely by Patch, before any license/copyright check runs.
EXCLUDED_EXTENSIONS = (".patch", ".bb", ".md", ".json", ".yml")

# Extensions LicenseChecker treats as source code requiring a license.
SOURCE_EXTENSIONS = (
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".java",
    ".py",
    ".js",
    ".ts",
    ".rb",
    ".go",
    ".swift",
    ".kt",
    ".kts",
    ".sh",
)

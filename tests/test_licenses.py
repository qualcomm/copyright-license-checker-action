"""Tests for shared license lists and SPDX-expression helpers."""

import unittest

from scanner.licenses import (
    COPYLEFT_LICENSES,
    PERMISSIVE_LICENSES,
    PROPRIETARY_LICENSE,
    is_copyleft,
    is_license_allowed,
    is_uncertain_expression,
    split_license_components,
)


class TestSplitLicenseComponents(unittest.TestCase):
    """SPDX expressions are split into individual identifiers."""

    def test_single_license(self):
        self.assertEqual(split_license_components("MIT"), ["MIT"])

    def test_and_or_expression(self):
        self.assertEqual(
            split_license_components("(MIT OR GPL-2.0-only) AND Apache-2.0"),
            ["MIT", "GPL-2.0-only", "Apache-2.0"],
        )

    def test_empty_expression(self):
        self.assertEqual(split_license_components(""), [])


class TestIsLicenseAllowed(unittest.TestCase):
    """The shared evaluator preserves the existing checker behavior."""

    def test_single_allowed_license(self):
        self.assertTrue(is_license_allowed("MIT", PERMISSIVE_LICENSES))

    def test_single_disallowed_license(self):
        self.assertFalse(is_license_allowed("GPL-2.0-only", PERMISSIVE_LICENSES))

    def test_whitespace_is_stripped(self):
        self.assertTrue(is_license_allowed("  MIT  ", PERMISSIVE_LICENSES))

    def test_and_requires_all_components(self):
        self.assertTrue(is_license_allowed("MIT AND Apache-2.0", PERMISSIVE_LICENSES))
        self.assertFalse(is_license_allowed("MIT AND GPL-2.0-only", PERMISSIVE_LICENSES))

    def test_or_requires_one_component(self):
        self.assertTrue(is_license_allowed("(MIT OR GPL-2.0-only)", PERMISSIVE_LICENSES))
        self.assertFalse(is_license_allowed("(GPL-2.0-only OR GPL-3.0-only)", PERMISSIVE_LICENSES))

    def test_leading_or_group_does_not_exempt_trailing_and_component(self):
        self.assertFalse(
            is_license_allowed("(MIT OR GPL-2.0-only) AND GPL-3.0-only", PERMISSIVE_LICENSES)
        )
        self.assertTrue(
            is_license_allowed("(MIT OR GPL-2.0-only) AND Apache-2.0", PERMISSIVE_LICENSES)
        )

    def test_unknown_license_is_not_allowed(self):
        self.assertFalse(is_license_allowed("LicenseRef-scancode-unknown", PERMISSIVE_LICENSES))


class TestGplOrLaterCompatibility(unittest.TestCase):
    """GPL `-or-later` compatibility remains unchanged."""

    def test_or_later_accepts_only_variant(self):
        self.assertTrue(is_license_allowed("GPL-2.0-only", COPYLEFT_LICENSES))

    def test_or_later_accepts_bare_base_license(self):
        self.assertTrue(is_license_allowed("GPL-2.0", ["GPL-2.0-or-later"]))

    def test_permissive_project_rejects_gpl(self):
        self.assertFalse(is_license_allowed("GPL-2.0-only", PERMISSIVE_LICENSES))


class TestUncertainExpressions(unittest.TestCase):
    """Uncertain ScanCode references retain their current classification."""

    def test_empty_expression_is_not_uncertain(self):
        self.assertFalse(is_uncertain_expression(""))

    def test_known_license_is_not_uncertain(self):
        self.assertFalse(is_uncertain_expression("MIT"))

    def test_unknown_reference_is_uncertain(self):
        self.assertTrue(is_uncertain_expression("LicenseRef-scancode-unknown-license-reference"))

    def test_mixed_known_and_unknown_is_not_uncertain(self):
        self.assertFalse(
            is_uncertain_expression(
                "GPL-2.0-only AND LicenseRef-scancode-unknown-license-reference"
            )
        )

    def test_all_unknown_components_are_uncertain(self):
        self.assertTrue(
            is_uncertain_expression(
                "LicenseRef-scancode-unknown-license-reference AND "
                "LicenseRef-scancode-warranty-disclaimer"
            )
        )

    def test_solitary_proprietary_marker_is_not_uncertain(self):
        self.assertFalse(is_uncertain_expression(PROPRIETARY_LICENSE))


class TestLicenseLists(unittest.TestCase):
    """Sanity checks for the canonical license lists."""

    def test_permissive_and_copyleft_do_not_overlap(self):
        self.assertEqual(set(PERMISSIVE_LICENSES) & set(COPYLEFT_LICENSES), set())

    def test_default_license_is_permissive(self):
        self.assertIn("BSD-3-Clause-Clear", PERMISSIVE_LICENSES)

    def test_copyleft_membership(self):
        self.assertTrue(is_copyleft("GPL-2.0-only"))
        self.assertFalse(is_copyleft("MIT"))

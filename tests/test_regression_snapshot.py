"""
End-to-end regression harness for the COMPLIANCE.md scenarios.

Snapshots main()'s stdout and exit code byte-for-byte -- exercising the real
Patch, LicenseChecker and CopyrightChecker, with only the scancode subprocess
mocked -- for one fixture patch per documented scenario. The unit tests
alongside this file cover the pieces in isolation; this covers what a
reviewer actually sees in the PR check, so a refactor that quietly reworders
a report, drops a section, or flips a blocking error into a warning fails
here even when every unit test still passes.

Scope is mode: opensource, the behavior this branch implements. The same
eight snapshots hold byte-for-byte on the proprietary-mode branch, which is
the point of pinning them here first: they are the baseline that shows
adding a second mode leaves the default path untouched.
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402  pylint: disable=wrong-import-position
from tests.scancode_mock import (  # noqa: E402  pylint: disable=wrong-import-position
    TempCwdMixin,
    scancode_mock_patcher,
)
from tests.static_data import patches  # noqa: E402  pylint: disable=wrong-import-position

PROPRIETARY_LICENSE = "LicenseRef-scancode-proprietary-license"


class RegressionSnapshotTestCase(TempCwdMixin, unittest.TestCase):
    """
    Runs main() end-to-end (real Patch/LicenseChecker/CopyrightChecker, only
    scancode mocked) in a scratch directory with no LICENSE file, so each
    scenario exercises get_license()'s real default-fallback path against
    repo_name "org/repo" (which matches no scanner/config.py entry).
    """

    def run_main(self, patch_content: str, detections: dict) -> tuple:
        """
        Write the patch to disk and run main() end-to-end.

        Args:
            patch_content: Raw patch text.
            detections: Scancode filename -> SPDX expression (or None) mapping.

        Returns:
            Tuple of (captured stdout, exit code).
        """
        patch_path = Path(self.tmp.name, "pr.patch")
        patch_path.write_text(patch_content, encoding="utf-8")

        argv = ["main.py", str(patch_path), "org/repo"]

        buffer = io.StringIO()
        with scancode_mock_patcher(detections):
            with mock_patch.object(sys, "argv", argv):
                with contextlib.redirect_stdout(buffer):
                    with self.assertRaises(SystemExit) as caught:
                        main.main()
        return buffer.getvalue(), caught.exception.code


EXPECTED_OS1_COPYLEFT_ADDED_BLOCKS = "< file license/copyright check > License file not found or detection failed, checking config...\n< file license/copyright check > Using default license: BSD-3-Clause-Clear\n< file license/copyright check > ┌───────────────────────────────────────────┐\n< file license/copyright check > │           **Flagged Files Report**         │\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ 📖 For more information, see: COMPLIANCE.md\n< file license/copyright check > │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │ 🚨  B L O C K I N G   E R R O R S\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │\n< file license/copyright check > │ ┌─ 📄 F I L E: src/module.c\n< file license/copyright check > │ │\n< file license/copyright check > │ ├─ 🚨 LICENSE ISSUES:\n< file license/copyright check > │ │  • Incompatible license added: GPL-2.0-only\n< file license/copyright check > │ └─────────────────────────────────────────\n< file license/copyright check > └───────────────────────────────────────────┘\n"  # noqa: E501
EXPECTED_OS1_COPYLEFT_ADDED_BLOCKS_CODE = 1

EXPECTED_OS2_LICENSE_DELETED_BLOCKS = "< file license/copyright check > License file not found or detection failed, checking config...\n< file license/copyright check > Using default license: BSD-3-Clause-Clear\n< file license/copyright check > ┌───────────────────────────────────────────┐\n< file license/copyright check > │           **Flagged Files Report**         │\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ 📖 For more information, see: COMPLIANCE.md\n< file license/copyright check > │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │ 🚨  B L O C K I N G   E R R O R S\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │\n< file license/copyright check > │ ┌─ 📄 F I L E: src/utils.py\n< file license/copyright check > │ │\n< file license/copyright check > │ ├─ 🚨 LICENSE ISSUES:\n< file license/copyright check > │ │  • License deleted: MIT\n< file license/copyright check > │ └─────────────────────────────────────────\n< file license/copyright check > └───────────────────────────────────────────┘\n"  # noqa: E501
EXPECTED_OS2_LICENSE_DELETED_BLOCKS_CODE = 1

EXPECTED_OS3_LICENSE_CHANGED_BLOCKS = "< file license/copyright check > License file not found or detection failed, checking config...\n< file license/copyright check > Using default license: BSD-3-Clause-Clear\n< file license/copyright check > ┌───────────────────────────────────────────┐\n< file license/copyright check > │           **Flagged Files Report**         │\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ 📖 For more information, see: COMPLIANCE.md\n< file license/copyright check > │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │ 🚨  B L O C K I N G   E R R O R S\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │\n< file license/copyright check > │ ┌─ 📄 F I L E: src/core.cpp\n< file license/copyright check > │ │\n< file license/copyright check > │ ├─ 🚨 LICENSE ISSUES:\n< file license/copyright check > │ │  • License deleted: MIT and license added: GPL-2.0-only\n< file license/copyright check > │ └─────────────────────────────────────────\n< file license/copyright check > └───────────────────────────────────────────┘\n"  # noqa: E501
EXPECTED_OS3_LICENSE_CHANGED_BLOCKS_CODE = 1

EXPECTED_OS4_NEW_FILE_NO_LICENSE_BLOCKS = "< file license/copyright check > License file not found or detection failed, checking config...\n< file license/copyright check > Using default license: BSD-3-Clause-Clear\n< file license/copyright check > ┌───────────────────────────────────────────┐\n< file license/copyright check > │           **Flagged Files Report**         │\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ 📖 For more information, see: COMPLIANCE.md\n< file license/copyright check > │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │ 🚨  B L O C K I N G   E R R O R S\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │\n< file license/copyright check > │ ┌─ 📄 F I L E: src/new_feature.py\n< file license/copyright check > │ │\n< file license/copyright check > │ ├─ 🚨 LICENSE ISSUES:\n< file license/copyright check > │ │  • No license added for source file: src/new_feature.py\n< file license/copyright check > │ └─────────────────────────────────────────\n< file license/copyright check > └───────────────────────────────────────────┘\n"  # noqa: E501
EXPECTED_OS4_NEW_FILE_NO_LICENSE_BLOCKS_CODE = 1

EXPECTED_OS5_COPYRIGHT_DELETION_BLOCKS = "< file license/copyright check > License file not found or detection failed, checking config...\n< file license/copyright check > Using default license: BSD-3-Clause-Clear\n< file license/copyright check > ┌───────────────────────────────────────────┐\n< file license/copyright check > │           **Flagged Files Report**         │\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ 📖 For more information, see: COMPLIANCE.md\n< file license/copyright check > │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │ 🚨  B L O C K I N G   E R R O R S\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │\n< file license/copyright check > │ ┌─ 📄 F I L E: src/bar.c\n< file license/copyright check > │ │\n< file license/copyright check > │ ├─ 🚨 COPYRIGHT ISSUES:\n< file license/copyright check > │ │  • Copyright deletions detected: [' * Copyright (c) 2019 Some Other Author. All rights reserved.']\n< file license/copyright check > │ └─────────────────────────────────────────\n< file license/copyright check > └───────────────────────────────────────────┘\n"  # noqa: E501
EXPECTED_OS5_COPYRIGHT_DELETION_BLOCKS_CODE = 1

EXPECTED_OS6_UNCERTAIN_LICENSE_WARNS = "< file license/copyright check > License file not found or detection failed, checking config...\n< file license/copyright check > Using default license: BSD-3-Clause-Clear\n< file license/copyright check > ┌───────────────────────────────────────────┐\n< file license/copyright check > │           **Flagged Files Report**         │\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ 📖 For more information, see: COMPLIANCE.md\n< file license/copyright check > │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │ ⚠️   W A R N I N G S  (Non-blocking)\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │\n< file license/copyright check > │ ┌─ 📄 F I L E: src/module.c\n< file license/copyright check > │ │\n< file license/copyright check > │ ├─ ⚠️  LICENSE WARNINGS:\n< file license/copyright check > │ │  • Incompatible license added: LicenseRef-scancode-unknown-license-reference\n< file license/copyright check > │ └─────────────────────────────────────────\n< file license/copyright check > └───────────────────────────────────────────┘\n"  # noqa: E501
EXPECTED_OS6_UNCERTAIN_LICENSE_WARNS_CODE = 0

EXPECTED_OS6B_MIXED_UNCERTAIN_AND_GPL_BLOCKS = "< file license/copyright check > License file not found or detection failed, checking config...\n< file license/copyright check > Using default license: BSD-3-Clause-Clear\n< file license/copyright check > ┌───────────────────────────────────────────┐\n< file license/copyright check > │           **Flagged Files Report**         │\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ 📖 For more information, see: COMPLIANCE.md\n< file license/copyright check > │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │ 🚨  B L O C K I N G   E R R O R S\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │\n< file license/copyright check > │ ┌─ 📄 F I L E: src/module.c\n< file license/copyright check > │ │\n< file license/copyright check > │ ├─ 🚨 LICENSE ISSUES:\n< file license/copyright check > │ │  • Incompatible license added: GPL-2.0-only AND LicenseRef-scancode-unknown-license-reference\n< file license/copyright check > │ └─────────────────────────────────────────\n< file license/copyright check > └───────────────────────────────────────────┘\n"  # noqa: E501
EXPECTED_OS6B_MIXED_UNCERTAIN_AND_GPL_BLOCKS_CODE = 1

EXPECTED_OS7_SOLE_PROPRIETARY_BLOCKS_OPENSOURCE = "< file license/copyright check > License file not found or detection failed, checking config...\n< file license/copyright check > Using default license: BSD-3-Clause-Clear\n< file license/copyright check > ┌───────────────────────────────────────────┐\n< file license/copyright check > │           **Flagged Files Report**         │\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ 📖 For more information, see: COMPLIANCE.md\n< file license/copyright check > │    https://github.com/qualcomm/copyright-license-checker-action/blob/main/COMPLIANCE.md\n< file license/copyright check > ├───────────────────────────────────────────┤\n< file license/copyright check > │\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │ 🚨  B L O C K I N G   E R R O R S\n< file license/copyright check > │ ═══════════════════════════════════════════\n< file license/copyright check > │\n< file license/copyright check > │ ┌─ 📄 F I L E: src/module.c\n< file license/copyright check > │ │\n< file license/copyright check > │ ├─ 🚨 LICENSE ISSUES:\n< file license/copyright check > │ │  • Incompatible license added: LicenseRef-scancode-proprietary-license\n< file license/copyright check > │ └─────────────────────────────────────────\n< file license/copyright check > └───────────────────────────────────────────┘\n"  # noqa: E501
EXPECTED_OS7_SOLE_PROPRIETARY_BLOCKS_OPENSOURCE_CODE = 1


class TestOpensourceModeScenarios(RegressionSnapshotTestCase):
    """COMPLIANCE.md scenarios 1-7."""

    def test_os1_incompatible_license_added_blocks(self):
        """Scenario 1: adding a copyleft license to a permissive repo blocks."""
        output, code = self.run_main(patches.ADDITION_ONLY, {"0_added.txt": "GPL-2.0-only"})
        self.assertEqual(code, EXPECTED_OS1_COPYLEFT_ADDED_BLOCKS_CODE)
        self.assertEqual(output, EXPECTED_OS1_COPYLEFT_ADDED_BLOCKS)

    def test_os2_license_deleted_without_replacement_blocks(self):
        """Scenario 2: removing a license with nothing added back blocks."""
        output, code = self.run_main(patches.DELETION_ONLY, {"0_deleted.txt": "MIT"})
        self.assertEqual(code, EXPECTED_OS2_LICENSE_DELETED_BLOCKS_CODE)
        self.assertEqual(output, EXPECTED_OS2_LICENSE_DELETED_BLOCKS)

    def test_os3_license_change_to_incompatible_blocks(self):
        """Scenario 3: swapping a permissive license for a copyleft one blocks."""
        output, code = self.run_main(
            patches.ADDITION_AND_DELETION,
            {"0_added.txt": "GPL-2.0-only", "0_deleted.txt": "MIT"},
        )
        self.assertEqual(code, EXPECTED_OS3_LICENSE_CHANGED_BLOCKS_CODE)
        self.assertEqual(output, EXPECTED_OS3_LICENSE_CHANGED_BLOCKS)

    def test_os4_missing_license_on_new_source_file_blocks(self):
        """Scenario 4: a new source file with no detected license blocks."""
        output, code = self.run_main(patches.NEW_FILE_NO_LICENSE, {"0_added.txt": None})
        self.assertEqual(code, EXPECTED_OS4_NEW_FILE_NO_LICENSE_BLOCKS_CODE)
        self.assertEqual(output, EXPECTED_OS4_NEW_FILE_NO_LICENSE_BLOCKS)

    def test_os5_copyright_deletion_blocks(self):
        """Scenario 5: a copyright statement deleted without replacement blocks."""
        output, code = self.run_main(
            patches.MODIFIED_WITH_DELETED_COPYRIGHT, {"0_deleted.txt": None}
        )
        self.assertEqual(code, EXPECTED_OS5_COPYRIGHT_DELETION_BLOCKS_CODE)
        self.assertEqual(output, EXPECTED_OS5_COPYRIGHT_DELETION_BLOCKS)

    def test_os6_uncertain_license_is_a_warning(self):
        """Uncertain/unknown license detection: a lone unknown reference warns."""
        output, code = self.run_main(
            patches.ADDITION_ONLY,
            {"0_added.txt": "LicenseRef-scancode-unknown-license-reference"},
        )
        self.assertEqual(code, EXPECTED_OS6_UNCERTAIN_LICENSE_WARNS_CODE)
        self.assertEqual(output, EXPECTED_OS6_UNCERTAIN_LICENSE_WARNS)

    def test_os6b_mixed_uncertain_and_known_incompatible_blocks(self):
        """Mixed with a known incompatible license, the expression still blocks."""
        output, code = self.run_main(
            patches.ADDITION_ONLY,
            {"0_added.txt": "GPL-2.0-only AND LicenseRef-scancode-unknown-license-reference"},
        )
        self.assertEqual(code, EXPECTED_OS6B_MIXED_UNCERTAIN_AND_GPL_BLOCKS_CODE)
        self.assertEqual(output, EXPECTED_OS6B_MIXED_UNCERTAIN_AND_GPL_BLOCKS)

    def test_os7_sole_proprietary_license_blocks(self):
        """Special case: a solitary proprietary-license detection blocks."""
        output, code = self.run_main(patches.ADDITION_ONLY, {"0_added.txt": PROPRIETARY_LICENSE})
        self.assertEqual(code, EXPECTED_OS7_SOLE_PROPRIETARY_BLOCKS_OPENSOURCE_CODE)
        self.assertEqual(output, EXPECTED_OS7_SOLE_PROPRIETARY_BLOCKS_OPENSOURCE)


if __name__ == "__main__":
    unittest.main()

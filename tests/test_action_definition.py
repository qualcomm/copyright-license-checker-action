# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

"""Tests for the Action's packaged checker invocation."""

import unittest
from pathlib import Path


class TestActionDefinition(unittest.TestCase):
    """Ensure the Action invokes the installed checker with its workspace root."""

    def setUp(self) -> None:
        """Load the composite Action definition."""
        self.action_definition = (Path(__file__).resolve().parent.parent / "action.yml").read_text(
            encoding="utf-8"
        )

    def test_installs_local_project_and_invokes_console_command(self) -> None:
        """Install the Action project and pass the checked-out workspace explicitly."""
        self.assertIn(
            'uv --no-config pip install --python "$PYTHON_PATH" --no-deps', self.action_definition
        )
        self.assertIn("copyright-license-checker \\", self.action_definition)
        self.assertIn('--repo-root "$GITHUB_WORKSPACE"', self.action_definition)
        self.assertNotIn('python "$ACTION_PATH/main.py"', self.action_definition)

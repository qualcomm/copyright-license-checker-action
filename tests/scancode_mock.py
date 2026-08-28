"""Shared ScanCode subprocess mocks and temporary-directory test support."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch as mock_patch


def scancode_mock_patcher(detections: dict):
    """Return a patcher that writes a ScanCode-shaped JSON report."""

    def fake_run(cmd, **_kwargs):
        output_file = cmd[cmd.index("--json-pp") + 1]
        files = []
        for filename, expression in detections.items():
            entry = {"path": filename, "type": "file", "license_detections": []}
            if expression is not None:
                entry["license_detections"] = [{"license_expression_spdx": expression}]
            files.append(entry)
        files.append({"path": ".", "type": "directory", "license_detections": []})
        Path(output_file).write_text(json.dumps({"files": files}), encoding="utf-8")
        return MagicMock(returncode=0)

    return mock_patch("scanner.license_scancode.subprocess.run", side_effect=fake_run)


class TempCwdMixin:
    """Run each test inside an isolated temporary working directory."""

    def setUp(self):
        """Change into a temporary directory for the test lifetime."""
        # pylint: disable=consider-using-with
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        original_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, original_cwd)

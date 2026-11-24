# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
"""Check whole repository files for license issues."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from scanner.file_types import SOURCE_EXTENSIONS
from scanner.licenses import is_license_allowed, is_uncertain_expression


def detect_licenses(paths: list) -> dict:
    """Scan repository-relative files once and return results keyed by path."""
    if not paths:
        return {}

    # Stage inside the checkout to support hardlinks. Nested mounts may still
    # prevent linking; the temporary directory is untracked and cleaned up.
    stage_root = os.path.abspath(".")
    try:
        staging_dir = tempfile.TemporaryDirectory(dir=stage_root)
    except OSError as error:
        raise RuntimeError(
            f"could not create a staging directory under {stage_root!r}: {error}"
        ) from error

    with staging_dir as tmpdir:
        # Preserve repository-relative paths so ScanCode sees exactly the selected files.
        # Hardlink only; never copy files that cannot be linked.
        input_dir = os.path.join(tmpdir, "input")
        for path in paths:
            target = Path(input_dir, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path, target)
            except OSError as error:
                raise RuntimeError(
                    f"could not hardlink {path!r} into the staging directory: {error}"
                ) from error

        output_file = os.path.join(tmpdir, "scancode_results.json")
        subprocess.run(
            [
                "scancode",
                "--license",
                "--strip-root",
                "--quiet",
                "--json-pp",
                output_file,
                input_dir,
            ],
            check=True,
        )
        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)

    return {entry["path"]: entry for entry in data.get("files", []) if entry["type"] == "file"}


class RepoLicenseChecker:
    """Check whole repository files for license issues."""

    def __init__(self, paths: list, allowed_licenses: list) -> None:
        """Store repository-relative paths and allowed licenses."""
        self.paths = paths
        self.allowed_licenses = allowed_licenses

    def run(self) -> tuple:
        """Return blocking and warning files in the standard report format."""
        results = detect_licenses(self.paths) if self.paths else {}

        flagged_files = {}
        warning_files = {}
        for path in self.paths:
            # ScanCode may omit files such as .gitignore; treat them as unlicensed.
            issues, warnings = self._classify(path, results.get(path, {}))
            if issues:
                flagged_files[path] = {"license_issues": issues, "copyright_issues": []}
            if warnings:
                warning_files[path] = {"license_issues": warnings, "copyright_issues": []}
        return flagged_files, warning_files

    def _classify(self, path: str, result: dict) -> tuple:
        """Return blocking issues and warnings for one file."""
        # The whole-file expression includes later license detections.
        expression = result.get("detected_license_expression_spdx")
        if not expression:
            # Only source files have to carry a license, as in the patch scan.
            missing = path.endswith(SOURCE_EXTENSIONS)
            return ([f"No license found in source file: {path}"] if missing else []), []
        if is_license_allowed(expression, self.allowed_licenses):
            return [], []

        message = [f"Incompatible license: {expression}"]
        return ([], message) if is_uncertain_expression(expression) else (message, [])

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
"""
Module to check whole repository files for license issues.

Where the patch scan judges the lines a patch adds and deletes, this judges
whole files as they stand. Both apply the same rules from scanner.licenses.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from scanner.file_types import SOURCE_EXTENSIONS
from scanner.licenses import is_license_allowed, is_uncertain_expression


def detect_licenses(paths: list) -> dict:
    """
    Detect the licenses of whole files in a single scancode run.

    Args:
        paths (list): Repository-relative paths of the files to scan.

    Returns:
        dict: Path -> scancode's result for that file, for every file reported.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # detect_licenses() copies the given files into a temporary directory, keeping their
        # repository-relative paths, and runs ScanCode once on it. The files come from
        # list_repo_files(), which already applies our rules (tracked files only, no
        # excluded extensions, no .licenseignore matches, no binaries). Scanning a copy
        # means ScanCode sees exactly that selection, so list_repo_files() stays the single
        # source of truth for what gets checked.
        input_dir = os.path.join(tmpdir, "input")
        for path in paths:
            target = Path(input_dir, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

        output_file = os.path.join(tmpdir, "scancode_results.json")
        # No check=True: scancode exits non-zero when a single file fails (for
        # example, times out) but still reports the rest, recording the failure
        # in that file's scan_errors.
        result = subprocess.run(
            [
                "scancode",
                "--license",
                "--strip-root",
                "--quiet",
                "--json-pp",
                output_file,
                input_dir,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"scancode failed (exit code {result.returncode}): {result.stderr}"
            ) from error

    return {entry["path"]: entry for entry in data.get("files", []) if entry["type"] == "file"}


class RepoLicenseChecker:
    """
    Class to check whole repository files for license issues.
    """

    def __init__(self, paths: list, allowed_licenses: list) -> None:
        """
        Initialize the RepoLicenseChecker object.

        Args:
            paths (list): Repository-relative paths of the files to check.
            allowed_licenses (list): The licenses allowed in this repository.
        """
        self.paths = paths
        self.allowed_licenses = allowed_licenses

    def run(self) -> tuple:
        """
        Run the license checker.

        Returns:
            tuple: (flagged_files, warning_files), each mapping a path to
                {"license_issues": [...], "copyright_issues": []}, the shape
                main.beautify_output and main.emit_workflow_annotations take.
        """
        results = detect_licenses(self.paths) if self.paths else {}

        flagged_files = {}
        warning_files = {}
        for path in self.paths:
            # ScanCode leaves some files out of its results (for example .gitignore), so there is
            # nothing to look up for them. We treat that as "no license". Only source files are
            # required to have a license, so only they are flagged; a .gitignore passes.
            issues, warnings = self._classify(path, results.get(path, {}))
            if issues:
                flagged_files[path] = {"license_issues": issues, "copyright_issues": []}
            if warnings:
                warning_files[path] = {"license_issues": warnings, "copyright_issues": []}
        return flagged_files, warning_files

    def _classify(self, path: str, result: dict) -> tuple:
        """
        Classify one file's scancode result into blocking issues and warnings.

        Args:
            path (str): The file's repository-relative path.
            result (dict): scancode's result for the file ({} if none).

        Returns:
            tuple: (issues, warnings), each a list of messages.
        """
        # A file scancode failed on (for example, timed out) has an unknown
        # license, not a missing one, so report the failure itself.
        if result.get("scan_errors"):
            reason = " ".join(" ".join(result["scan_errors"]).split())
            return [f"License scan failed: {reason}"], []

        # Use the expression for the whole file: the first detection alone
        # would miss a second license further down.
        expression = result.get("detected_license_expression_spdx")
        if not expression:
            # Only source files have to carry a license, as in the patch scan.
            missing = path.endswith(SOURCE_EXTENSIONS)
            return ([f"No license found in source file: {path}"] if missing else []), []
        if is_license_allowed(expression, self.allowed_licenses):
            return [], []

        message = [f"Incompatible license: {expression}"]
        return ([], message) if is_uncertain_expression(expression) else (message, [])

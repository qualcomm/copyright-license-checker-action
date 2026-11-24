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

# If ScanCode fails to scan a file, for example because it times out, the
# run-level error identifies it as "Path: input/<path>".
STAGED_INPUT_DIRNAME = "input"


def _unattributed_errors(run_errors: list, files: list) -> list:
    """
    Return header errors not already attributed to a reported file.

    ScanCode also repeats each file's scan error as "Path: input/<path>".
    Match that pointer exactly so unexpected errors still fail the scan.
    """
    attributed_pointers = {
        f"Path: {STAGED_INPUT_DIRNAME}/{entry['path']}"
        for entry in files
        if entry.get("scan_errors")
    }
    return [error for error in run_errors if error not in attributed_pointers]


def detect_licenses(paths: list) -> dict:
    """
    Scan repository-relative files once and return results keyed by path.

    Raise RuntimeError on staging failures or unattributed ScanCode errors.
    """
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
        input_dir = os.path.join(tmpdir, STAGED_INPUT_DIRNAME)
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
        # A nonzero exit can still include results and per-file scan_errors.
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

        files = _parsed_report(data)

    return {entry["path"]: entry for entry in files}


def _parsed_report(data: dict) -> list:
    """Return file entries, failing on errors without a file-level result."""
    files = [entry for entry in data.get("files", []) if entry["type"] == "file"]

    # A worker failure can leave a file out of the report entirely. Fail the scan
    # rather than treating that missing result as a file with no detected license.
    header = (data.get("headers") or [{}])[0]
    run_errors = _unattributed_errors(header.get("errors") or [], files)
    if run_errors:
        reason = " ".join(" ".join(run_errors).split())
        raise RuntimeError(f"scancode reported a run-level error: {reason}")

    return files


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
        # A scan failure means unknown, not missing, license.
        if result.get("scan_errors"):
            reason = " ".join(" ".join(result["scan_errors"]).split())
            return [f"License scan failed: {reason}"], []

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

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

"""Characterize the legacy command-line interface in a separate process.

These tests deliberately invoke ``main.py`` rather than importing it so later
packaging work preserves the interface used by Action consumers and scripts.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

NEW_SOURCE_FILE_PATCH = """\
diff --git a/src/example.py b/src/example.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/src/example.py
@@ -0,0 +1 @@
+print(\"example\")
"""


class TestLegacyCliSubprocess(unittest.TestCase):
    """Pin the observable behavior of the root ``main.py`` command."""

    def setUp(self) -> None:
        """Create an isolated caller directory and a fake ScanCode executable."""
        # The directory must persist for the entire unittest case, including
        # subprocess execution, so addCleanup is the appropriate lifetime.
        # pylint: disable=consider-using-with
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.workspace = Path(self.temporary_directory.name)
        self.source_root = Path(__file__).resolve().parent.parent
        self.scancode_bin = self.workspace / "bin"
        self.scancode_bin.mkdir()
        self._write_scancode_stub()

    def _write_scancode_stub(self) -> None:
        """Write a deterministic ScanCode replacement used only by this test."""
        scancode = self.scancode_bin / "scancode"
        scancode.write_text(
            textwrap.dedent("""\
                #!{python}
                import json
                import os
                import sys
                from pathlib import Path

                exit_code = int(os.environ.get("SCANCODE_EXIT_CODE", "0"))
                if exit_code:
                    sys.exit(exit_code)

                output_file = Path(sys.argv[sys.argv.index("--json-pp") + 1])
                target = Path(sys.argv[-1])
                detections = json.loads(os.environ.get("SCANCODE_DETECTIONS", "{{}}"))
                files = target.rglob("*") if target.is_dir() else [target]
                report_files = []
                for file_path in files:
                    if not file_path.is_file():
                        continue
                    expression = detections.get(file_path.name)
                    entry = {{"path": file_path.name, "type": "file", "license_detections": []}}
                    if expression is not None:
                        entry["license_detections"] = [{{"license_expression_spdx": expression}}]
                    report_files.append(entry)
                output_file.write_text(json.dumps({{"files": report_files}}), encoding="utf-8")
                """).format(python=sys.executable),
            encoding="utf-8",
        )
        scancode.chmod(scancode.stat().st_mode | stat.S_IXUSR)

    def _run_main(
        self,
        *arguments: str,
        cwd: Path,
        detections: dict[str, str | None] | None = None,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run the legacy entry point with a controlled environment."""
        environment = os.environ.copy()
        environment["PATH"] = f"{self.scancode_bin}{os.pathsep}{environment.get('PATH', '')}"
        environment["SCANCODE_DETECTIONS"] = json.dumps(detections or {})
        if extra_environment:
            environment.update(extra_environment)
        return subprocess.run(
            [sys.executable, str(self.source_root / "main.py"), *arguments],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def _write_patch(self, directory: Path, filename: str = "change.patch") -> Path:
        """Write the standard new-source-file patch and return its path."""
        patch_file = directory / filename
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        patch_file.write_text(NEW_SOURCE_FILE_PATCH, encoding="utf-8")
        return patch_file

    def test_help_is_available_without_scancode(self) -> None:
        """The legacy script exposes argparse help without scanning anything."""
        caller = self.workspace / "caller"
        caller.mkdir()

        result = self._run_main("--help", cwd=caller)

        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)
        self.assertIn("patch_file", result.stdout)
        self.assertIn("repo_name", result.stdout)

    def test_invalid_arguments_exit_with_argparse_status(self) -> None:
        """Missing positionals and an invalid mode retain argparse's exit code."""
        caller = self.workspace / "caller"
        caller.mkdir()

        missing = self._run_main("change.patch", cwd=caller)
        invalid_mode = self._run_main("change.patch", "org/repo", "--mode", "unknown", cwd=caller)

        self.assertEqual(missing.returncode, 2)
        self.assertIn("the following arguments are required: repo_name", missing.stderr)
        self.assertEqual(invalid_mode.returncode, 2)
        self.assertIn("invalid choice", invalid_mode.stderr)

    def test_relative_patch_uses_callers_directory_and_ignore_uses_current_directory(self) -> None:
        """Patch lookup and repository configuration are intentionally independent."""
        caller = self.workspace / "caller"
        repository = self.workspace / "repository"
        caller.mkdir()
        repository.mkdir()
        self._write_patch(caller, "patches/change.patch")
        (repository / ".licenseignore").write_text("src/example.py\n", encoding="utf-8")

        result = self._run_main("../caller/patches/change.patch", "org/repo", cwd=repository)

        self.assertEqual(result.returncode, 0)
        self.assertIn("No license or copyright issues detected", result.stdout)

    def test_blocking_finding_preserves_report_and_exit_code(self) -> None:
        """An incompatible license renders the established blocking report."""
        caller = self.workspace / "caller"
        caller.mkdir()
        self._write_patch(caller)

        result = self._run_main(
            "change.patch",
            "org/repo",
            cwd=caller,
            detections={"0_added.txt": "GPL-2.0-only"},
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Flagged Files Report", result.stdout)
        self.assertIn("Incompatible license added: GPL-2.0-only", result.stdout)

    def test_github_actions_environment_emits_escaped_annotation(self) -> None:
        """Action-style invocation adds an annotation without changing the report."""
        caller = self.workspace / "caller"
        caller.mkdir()
        self._write_patch(caller)

        result = self._run_main(
            "change.patch",
            "org/repo",
            cwd=caller,
            detections={"0_added.txt": "GPL-2.0-only"},
            extra_environment={"GITHUB_ACTIONS": "true"},
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Flagged Files Report", result.stdout)
        self.assertIn(
            "::error file=src/example.py::Incompatible license added: GPL-2.0-only", result.stdout
        )

    def test_missing_and_unreadable_patch_paths_propagate_legacy_failures(self) -> None:
        """Missing files and directories expose the current operational behavior."""
        caller = self.workspace / "caller"
        caller.mkdir()
        directory_patch = caller / "not-a-file.patch"
        directory_patch.mkdir()

        missing = self._run_main("missing.patch", "org/repo", cwd=caller)
        unreadable = self._run_main("not-a-file.patch", "org/repo", cwd=caller)

        self.assertEqual(missing.returncode, 1)
        self.assertIn("FileNotFoundError", missing.stderr)
        self.assertEqual(unreadable.returncode, 1)
        self.assertIn("IsADirectoryError", unreadable.stderr)

    def test_scancode_failure_propagates_legacy_operational_failure(self) -> None:
        """A failed ScanCode subprocess remains a nonzero legacy failure."""
        caller = self.workspace / "caller"
        caller.mkdir()
        self._write_patch(caller)

        result = self._run_main(
            "change.patch",
            "org/repo",
            cwd=caller,
            extra_environment={"SCANCODE_EXIT_CODE": "9"},
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("CalledProcessError", result.stderr)

    def test_missing_scancode_propagates_legacy_operational_failure(self) -> None:
        """A missing ScanCode executable remains a nonzero legacy failure."""
        caller = self.workspace / "caller"
        caller.mkdir()
        self._write_patch(caller)

        result = self._run_main(
            "change.patch",
            "org/repo",
            cwd=caller,
            extra_environment={"PATH": ""},
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("FileNotFoundError", result.stderr)


if __name__ == "__main__":
    unittest.main()

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import json

import scanner.license_resolver as lr

# The scancode-failure tests exercise _detect_license_from_file directly (it is the
# unit under test, not an implementation detail reached through resolve_license), and
# the _FakeProc subprocess stand-in below trips too-few-public-methods. Both are
# idiomatic in a test module.
# pylint: disable=protected-access,too-few-public-methods


def _with_license_file(tmp_path, monkeypatch, detected, content="dummy license text"):
    """chdir into a temp repo with a LICENSE file (default: non-license text), and
    stub scancode detection to return `detected`."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "LICENSE").write_text(content)
    monkeypatch.setattr(lr, "_detect_license_from_file", lambda path: detected)


# --- resolution outcomes ------------------------------------------------------

def test_real_license_passthrough(tmp_path, monkeypatch):
    _with_license_file(tmp_path, monkeypatch, "MIT")
    assert lr.resolve_license("owner/unconfigured-repo-xyz") == "MIT"


def test_scancode_bsd_detection_passthrough(tmp_path, monkeypatch):
    # A real scancode BSD detection is reported as its actual SPDX id -- NOT
    # normalized to a canonical BSD (the old DEFAULT_LICENSE squash is gone).
    for detected in ("BSD-3-Clause", "BSD-2-Clause"):
        _with_license_file(tmp_path, monkeypatch, detected)
        res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
        assert res.license == detected and res.source == "license_file", detected


def test_proprietary_detection_aborts(tmp_path, monkeypatch):
    # A proprietary catch-all is distrusted; with no config match -> abort (no
    # fabricated default), recording the file for the "undetected" message. This
    # also covers a genuine BSD-3 LICENSE that scancode 32.2.1 mis-tags as proprietary
    # (this repo + qualcomm/commit-emails-check-action): with the text heuristic removed
    # it aborts here until the scancode upgrade detects that text correctly as BSD-3.
    _with_license_file(tmp_path, monkeypatch, "LicenseRef-scancode-proprietary-license")
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert res.license is None and res.source == "none"
    assert res.license_file == "LICENSE"             # present-but-undetected
    assert lr.resolve_license("owner/unconfigured-repo-xyz") is None


def test_nonempty_undetected_nonbsd_aborts(tmp_path, monkeypatch):
    # No scancode detection + non-BSD text + no config -> abort (no default).
    _with_license_file(tmp_path, monkeypatch, None,
                       content="some real but unrecognized license text")
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert res.license is None and res.source == "none" and res.license_file == "LICENSE"


def test_alternate_license_filenames_are_recognized(tmp_path, monkeypatch):
    # British / lowercase / COPYING.md filenames are recognized as license files.
    monkeypatch.setattr(lr, "_detect_license_from_file", lambda path: "MIT")
    for name in ("LICENCE", "license", "licence.md", "COPYING.md"):
        repo_dir = tmp_path / f"repo_{name}"
        repo_dir.mkdir()
        (repo_dir / name).write_text("dummy")
        monkeypatch.chdir(repo_dir)
        assert lr.resolve_license("owner/unconfigured-repo-xyz") == "MIT", name


def test_no_license_file_no_config_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert res.license is None and res.source == "none"
    assert res.license_file is None and res.empty_license_files == ()
    assert lr.resolve_license("owner/unconfigured-repo-xyz") is None


def test_no_license_file_but_config_match_returns_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert lr.resolve_license("qualcomm-linux/meta-qcom-kernel") == "GPL-2.0"


def test_config_wins_when_file_present_but_undetected(tmp_path, monkeypatch):
    # A present-but-undetected NON-BSD file does not block a config baseline.
    _with_license_file(tmp_path, monkeypatch, None)
    res = lr.resolve_license_details("qualcomm-linux/meta-qcom-kernel")
    assert res.license == "GPL-2.0" and res.source == "config"


# --- resolve_license_details: source metadata ---------------------------------

def test_details_source_license_file(tmp_path, monkeypatch):
    _with_license_file(tmp_path, monkeypatch, "MIT")
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert (res.license, res.source, res.license_file, res.num_license_files) == \
        ("MIT", "license_file", "LICENSE", 1)


def test_details_config_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = lr.resolve_license_details("qualcomm-linux/meta-qcom-kernel")
    assert (res.license, res.source, res.config_project) == \
        ("GPL-2.0", "config", "meta-qcom-kernel")


def test_details_counts_multiple_root_license_files(tmp_path, monkeypatch):
    # Two root-level license files -> count is 2, first by priority (LICENSE) used.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "LICENSE").write_text("dummy")
    (tmp_path / "COPYING").write_text("dummy")
    monkeypatch.setattr(lr, "_detect_license_from_file", lambda path: "MIT")
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert res.num_license_files == 2
    assert res.license_file == "LICENSE" and res.source == "license_file"


def test_describe_resolution_texts():
    lf = lr.LicenseResolution("MIT", "license_file", "LICENSE", 1, None)
    assert lr.describe_resolution(lf) == "(based on license file LICENSE)"
    lf2 = lr.LicenseResolution("MIT", "license_file", "LICENSE", 3, None)
    assert "3 license files present" in lr.describe_resolution(lf2)
    cfg = lr.LicenseResolution("GPL-2.0", "config", None, 0, "meta-qcom-kernel")
    assert lr.describe_resolution(cfg) == "(from scanner/config.py entry for meta-qcom-kernel)"
    no_file = lr.LicenseResolution(None, "none", None, 0, None)
    assert lr.describe_resolution(no_file) == ""
    empty = lr.LicenseResolution(None, "none", None, 0, None, ("LICENSE",))
    assert "empty" in lr.describe_resolution(empty)
    undetected = lr.LicenseResolution(None, "none", "LICENSE", 1, None)
    assert lr.describe_resolution(undetected) == \
        "(license file present but license not conclusively detected)"


# --- empty root license file is treated as absent (no fabricated default) -----

def test_empty_license_file_is_none_with_no_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "LICENSE").write_text("\n")
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert res.source == "none" and res.license is None
    assert res.empty_license_files == ("LICENSE",) and res.license_file is None
    assert res.num_license_files == 0


def test_empty_license_file_still_honors_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "LICENSE").write_text("   ")
    res = lr.resolve_license_details("qualcomm-linux/meta-qcom-kernel")
    assert res.source == "config" and res.license == "GPL-2.0"


# --- a failing scancode must be diagnosable, not silently "undetected" --------

class _FakeProc:
    """Stand-in for a finished subprocess.run() result."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_scancode(returncode=0, stderr="", results=None):
    """
    Build a subprocess.run replacement for _detect_license_from_file.

    Writes `results` (a scancode JSON payload) to the --json-pp path when given;
    when it is None the run leaves no output file, mimicking a scancode that
    aborted before writing anything.
    """
    def _run(cmd, **_kwargs):
        if results is not None:
            out_path = cmd[cmd.index("--json-pp") + 1]
            with open(out_path, "w", encoding="utf-8") as handle:
                json.dump(results, handle)
        return _FakeProc(returncode, stderr=stderr)

    return _run


def _one_file_results(expression):
    """A minimal scancode payload: one file with a single confident match."""
    return {"files": [{
        "type": "file",
        "license_detections": [{
            "matches": [{"spdx_license_expression": expression, "matched_length": 50}],
        }],
    }]}


def test_scancode_failure_reports_exit_code_and_stderr(tmp_path, monkeypatch, capsys):
    # A scancode that dies before writing results (e.g. the click 8.5.0 regression
    # that made every invocation exit 2 with a UsageError) must print WHY. Without
    # this the only clue in CI is "returned non-zero exit status 2", which reads as
    # "your LICENSE is unrecognizable" rather than "the scanner is broken".
    (tmp_path / "LICENSE").write_text("dummy license text")
    monkeypatch.setattr(lr.subprocess, "run", _fake_scancode(
        returncode=2, stderr="Error: The option --strip-root cannot be used together"))
    assert lr._detect_license_from_file(str(tmp_path / "LICENSE")) is None
    out = capsys.readouterr().out
    assert "license detection failed" in out
    assert "scancode exit code: 2" in out
    assert "--strip-root cannot be used together" in out


def test_scancode_results_used_despite_nonzero_exit(tmp_path, monkeypatch, capsys):
    # scancode exits non-zero for per-file scan warnings while still writing valid
    # results: use them, but say what it complained about.
    (tmp_path / "LICENSE").write_text("dummy license text")
    monkeypatch.setattr(lr.subprocess, "run", _fake_scancode(
        returncode=1, stderr="some warning", results=_one_file_results("MIT")))
    assert lr._detect_license_from_file(str(tmp_path / "LICENSE")) == "MIT"
    out = capsys.readouterr().out
    assert "exited 1" in out and "some warning" in out


def test_scancode_success_is_quiet(tmp_path, monkeypatch, capsys):
    (tmp_path / "LICENSE").write_text("dummy license text")
    monkeypatch.setattr(lr.subprocess, "run", _fake_scancode(
        results=_one_file_results("MIT")))
    assert lr._detect_license_from_file(str(tmp_path / "LICENSE")) == "MIT"
    assert "scancode exit code" not in capsys.readouterr().out

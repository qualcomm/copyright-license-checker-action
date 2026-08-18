import os
import subprocess
from datetime import datetime

import pytest

import compare_tools as ct


# --- normalize_path / _ext_of ------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("./foo.py", "foo.py"),
    ("/src/foo/bar.py", "foo/bar.py"),
    ("a\\b\\c.py", "a/b/c.py"),
    ("  spaced.py  ", "spaced.py"),
    ("plain.py", "plain.py"),
    (None, ""),
    ("", ""),
])
def test_normalize_path(raw, expected):
    assert ct.normalize_path(raw) == expected


@pytest.mark.parametrize("path, ext", [
    ("a/b.py", ".py"),
    ("Makefile", ""),
    ("x.tar.gz", ".gz"),
    ("Android.bp", ".bp"),
])
def test_ext_of(path, ext):
    assert ct._ext_of(path) == ext


# --- derive_repo_name (git remote get-url) -----------------------------------

def _stub_origin(monkeypatch, url=None, fail=False):
    def run(cmd, **kwargs):
        if fail:
            raise subprocess.CalledProcessError(128, cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=(url or "") + "\n", stderr="")
    monkeypatch.setattr(ct.subprocess, "run", run)


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/qualcomm/test_sshedge.git", "qualcomm/test_sshedge"),
    ("https://github.com/qualcomm/test_sshedge", "qualcomm/test_sshedge"),
    ("git@github.com:qualcomm/test_sshedge.git", "qualcomm/test_sshedge"),
    ("https://github.qualcomm.com/org/repo.git", "org/repo"),
    ("https://x-token:pat@github.com/qualcomm/repo.git", "qualcomm/repo"),
])
def test_derive_repo_name_ok(monkeypatch, url, expected):
    _stub_origin(monkeypatch, url=url)
    assert ct.derive_repo_name("/any/path") == expected


def test_derive_repo_name_no_remote(monkeypatch):
    _stub_origin(monkeypatch, fail=True)
    assert ct.derive_repo_name("/any/path") is None


def test_derive_repo_name_empty_url(monkeypatch):
    _stub_origin(monkeypatch, url="")
    assert ct.derive_repo_name("/any/path") is None


# --- _parse_scanner_message --------------------------------------------------

@pytest.mark.parametrize("message, kind, severity, category", [
    ("No license header found", "NOLIC", "error", "license"),
    ("Incompatible license: GPL-2.0-only", "INCOMPAT", "error", "license"),
    ("Uncertain license, review manually: LicenseRef-scancode-unknown",
     "UNCERT", "warning", "license"),
    ("No copyright statement found", "NOCR", "error", "copyright"),
    ("Copyright holder does not match the expected Qualcomm/Linux Foundation pattern, "
     "review manually", "CRHOLDER", "warning", "copyright"),
])
def test_parse_scanner_message(message, kind, severity, category):
    assert ct._parse_scanner_message(message) == (kind, severity, category)


def test_parse_scanner_message_unknown_is_none():
    assert ct._parse_scanner_message("some unrelated line") is None


# --- normalize_scanner -------------------------------------------------------

def test_normalize_scanner_folds_findings():
    flagged = {
        "a.py": {
            "license_issues": ["Incompatible license: GPL-2.0-only"],
            "copyright_issues": ["No copyright statement found"],
        }
    }
    warning = {
        "b.py": {
            "license_issues": ["Uncertain license, review manually: "
                               "LicenseRef-scancode-unknown-license-reference"],
            "copyright_issues": [],
        }
    }
    view = ct.normalize_scanner(
        "BSD-3-Clause-Clear", flagged, warning,
        {"a.py", "b.py", "c.py"}, {"vendor/x.py"})

    assert view.license == "BSD-3-Clause-Clear"
    assert {f.kind for f in view.per_file["a.py"]} == {"INCOMPAT", "NOCR"}
    assert {f.kind for f in view.per_file["b.py"]} == {"UNCERT"}
    assert "c.py" in view.scanned_paths            # clean file still in scope
    assert "vendor/x.py" in view.ignored_paths
    assert view.flagged_count == 1
    assert view.warning_count == 1


def test_normalize_scanner_normalizes_paths():
    flagged = {"./a.py": {"license_issues": ["No license header found"],
                          "copyright_issues": []}}
    view = ct.normalize_scanner("MIT", flagged, {}, {"./a.py"})
    assert "a.py" in view.per_file
    assert "a.py" in view.scanned_paths


# --- normalize_repolinter ----------------------------------------------------

def _lint_result():
    return {
        "errored": False,
        "passed": True,
        "results": [
            {"ruleInfo": {"name": "source-license-headers-exist", "level": "error",
                          "ruleType": "file-starts-with"},
             "status": "NOT_PASSED_ERROR",
             "lintResult": {"targets": [
                 {"path": "a.py", "passed": False, "message": "no header"},
                 {"path": "ok.py", "passed": True},
             ]}},
            {"ruleInfo": {"name": "license-file-exists", "level": "error",
                          "ruleType": "file-existence"},
             "status": "PASSED",
             "lintResult": {"message": "found"}},
            # IGNORED rule with NO lintResult -- must not crash (the guarded case).
            {"ruleInfo": {"name": "some-off-rule", "level": "off"},
             "status": "IGNORED"},
        ],
    }


def test_normalize_repolinter_header_and_repo_level():
    view = ct.normalize_repolinter(_lint_result())
    assert not view.errored
    assert view.evaluated_paths == {"a.py", "ok.py"}
    assert any(not f.passed for f in view.per_file["a.py"])
    assert any(f.passed for f in view.per_file["ok.py"])
    repo_rules = {r.rule_name for r in view.repo_level}
    assert "license-file-exists" in repo_rules
    assert "some-off-rule" in repo_rules   # IGNORED, no lintResult, still recorded


def test_normalize_repolinter_errored():
    view = ct.normalize_repolinter({"errored": True, "errMsg": "boom", "results": []})
    assert view.errored
    assert view.err_msg == "boom"


# --- build_comparison + _compute_tags ----------------------------------------

def test_build_comparison_categories_and_compat_tag():
    sv = ct.ScannerView(license="BSD-3-Clause-Clear")
    sv.per_file = {"a.py": [ct.ScannerFinding(
        "INCOMPAT", "error", "license", "Incompatible license: GPL-2.0-only")]}
    sv.scanned_paths = {"a.py"}

    rv = ct.RepolinterView()
    rv.per_file = {"b.py": [ct.RepolinterFinding(
        "source-license-headers-exist", "SLH", "error", False, "no header")]}

    records = {r.path: r for r in ct.build_comparison(sv, rv, include_untracked=False)}
    assert records["a.py"].category == "ONLY_FULL_SCAN"
    assert records["b.py"].category == "ONLY_REPOLINTER"
    # license compatibility has no repolinter analog -> full_scan-only tag.
    assert "compatibility (full_scan-only)" in records["a.py"].tags


def test_build_comparison_both_when_overlap():
    sv = ct.ScannerView(license="BSD-3-Clause-Clear")
    sv.per_file = {"x.py": [ct.ScannerFinding(
        "NOLIC", "error", "license", "No license header found")]}
    sv.scanned_paths = {"x.py"}
    rv = ct.RepolinterView()
    rv.per_file = {"x.py": [ct.RepolinterFinding(
        "source-license-headers-exist", "SLH", "error", False, None)]}
    rv.evaluated_paths = {"x.py"}

    rec = ct.build_comparison(sv, rv, include_untracked=False)[0]
    assert rec.category == "BOTH"
    assert "missing-header agreement" in rec.tags


def test_compute_tags_missing_header_agreement():
    rec = ct.ComparisonRecord(
        path="x.py", ext=".py", category="BOTH",
        scanner_findings=[ct.ScannerFinding(
            "NOLIC", "error", "license", "No license header found")],
        repolinter_findings=[ct.RepolinterFinding(
            "source-license-headers-exist", "SLH", "error", False, None)])
    tags = ct._compute_tags(rec, {"x.py"}, include_untracked=False,
                            ignored_scope=set(), rl_evaluated={"x.py"})
    assert "missing-header agreement" in tags


# --- resolve_output_path -----------------------------------------------------

def test_resolve_output_path_auto(monkeypatch, tmp_path):
    monkeypatch.setattr(ct, "REPORTS_DIR", str(tmp_path))
    path = ct.resolve_output_path(None, "qualcomm/test_sshedge",
                                  datetime(2026, 1, 2, 3, 4, 5))
    assert path == os.path.join(str(tmp_path), "test_sshedge_20260102-030405.html")


def test_resolve_output_path_explicit_creates_parent(tmp_path):
    out = str(tmp_path / "nested" / "report.html")
    path = ct.resolve_output_path(out, "q/r", datetime(2026, 1, 1, 0, 0, 0))
    assert path == out
    assert os.path.isdir(os.path.dirname(out))

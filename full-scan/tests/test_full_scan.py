# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import pytest
from click.testing import CliRunner

import full_scan
from scanner.licenses import PERMISSIVE_LICENSES, COPYLEFT_LICENSES
from scanner.license_resolver import LicenseResolution


def _res(license_id, source="license_file", license_file="LICENSE",
         num_files=1, config_project=None):
    return LicenseResolution(license_id, source, license_file, num_files, config_project)


def _issues(license_issues=None, copyright_issues=None):
    return {
        "license_issues": license_issues or [],
        "copyright_issues": copyright_issues or [],
    }


def test_beautify_flagged_failon_exits_one(capsys):
    # Blocking finding + fail_on_findings=True -> exit 1, blocking header.
    flagged = {"a.c": _issues(license_issues=["Incompatible license: GPL-2.0"])}
    with pytest.raises(SystemExit) as exc:
        full_scan.beautify_scan_output(
            flagged, {}, "BSD-3-Clause-Clear", True, full_scan.LOG_PREFIX)
    assert exc.value.code == 1
    assert "B L O C K I N G   E R R O R S" in capsys.readouterr().out


def test_beautify_report_only_exits_zero(capsys):
    # Same blocking finding, but fail_on_findings=False -> report-only, exit 0.
    flagged = {"a.c": _issues(license_issues=["Incompatible license: GPL-2.0"])}
    with pytest.raises(SystemExit) as exc:
        full_scan.beautify_scan_output(
            flagged, {}, "BSD-3-Clause-Clear", False, full_scan.LOG_PREFIX)
    assert exc.value.code == 0
    assert "F I N D I N G S  (report-only)" in capsys.readouterr().out


def test_beautify_shows_license_reason(capsys):
    # The resolved-license line carries the plain-text "why" reason.
    flagged = {"a.c": _issues(license_issues=["Incompatible license: GPL-2.0"])}
    with pytest.raises(SystemExit):
        full_scan.beautify_scan_output(
            flagged, {}, "GPL-2.0-only", False, full_scan.LOG_PREFIX,
            "(based on license file LICENSE)")
    out = capsys.readouterr().out
    assert "Repository license: GPL-2.0-only (based on license file LICENSE)" in out


def test_beautify_warnings_only_exits_zero():
    # Warnings but no blocking files -> exit 0 even with fail_on_findings=True
    # (warnings alone never fail CI).
    warning = {"a.c": _issues(
        license_issues=["Uncertain license, review manually: LicenseRef-scancode-unknown"])}
    with pytest.raises(SystemExit) as exc:
        full_scan.beautify_scan_output(
            {}, warning, "BSD-3-Clause-Clear", True, full_scan.LOG_PREFIX)
    assert exc.value.code == 0


@pytest.mark.parametrize("resolved, expected_allowed", [
    ("BSD-3-Clause-Clear", PERMISSIVE_LICENSES),   # permissive -> full permissive set
    ("GPL-2.0-only", COPYLEFT_LICENSES),           # copyleft -> full copyleft set
    ("Foo-1.0", ["Foo-1.0"]),                      # unknown -> just the license itself
    # Compound all-permissive (scancode reports this for some Qualcomm LICENSEs)
    # must select the full permissive set, not a singleton that flags every file.
    ("BSD-3-Clause-Clear AND BSD-3-Clause", PERMISSIVE_LICENSES),
    # A partially-permissive compound satisfies neither list -> falls to the
    # singleton, guarding against over-broadening the bucket.
    ("MIT AND Foo-1.0", ["MIT AND Foo-1.0"]),
])
def test_allowed_license_selection(monkeypatch, tmp_path, resolved, expected_allowed):
    # The resolved repo license selects which allow-list every file is judged
    # against. Pin that mapping without touching git/scancode by stubbing the
    # collaborators and capturing the list handed to FullScanner.
    captured = {}

    class _FakeScanner:
        def __init__(self, repo_scan, allowed):
            captured["allowed"] = allowed

        def run(self):
            return {}, {}

    monkeypatch.setattr(full_scan, "resolve_license_details",
                        lambda repo_name: _res(resolved))
    monkeypatch.setattr(full_scan, "RepoScan", lambda **kwargs: object())
    monkeypatch.setattr(full_scan, "FullScanner", _FakeScanner)
    monkeypatch.setattr(full_scan.os, "chdir", lambda path: None)

    result = CliRunner().invoke(
        full_scan.main, ["owner/repo", "false", "--repo-path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert captured["allowed"] == expected_allowed


def _boom_if_scanned(*args, **kwargs):
    raise AssertionError("scan must be skipped when there is no license baseline")


def test_missing_root_license_blocks_when_failon(monkeypatch, tmp_path):
    # resolve_license -> None (no root license file, no config entry) with
    # fail_on_findings=true: abort with the status and a non-zero exit, and prove
    # the scan is skipped (RepoScan/FullScanner would raise if reached).
    monkeypatch.setattr(full_scan, "resolve_license_details",
                        lambda repo_name: _res(None, source="none",
                                               license_file=None, num_files=0))
    monkeypatch.setattr(full_scan, "RepoScan", _boom_if_scanned)
    monkeypatch.setattr(full_scan, "FullScanner", _boom_if_scanned)
    monkeypatch.setattr(full_scan.os, "chdir", lambda path: None)

    result = CliRunner().invoke(
        full_scan.main, ["owner/repo", "true", "--repo-path", str(tmp_path)])

    assert result.exit_code == 1, result.output
    assert "No Root-Level Licence Found" in result.output


def test_report_missing_root_license_notes_empty_file(capsys):
    # When the "no baseline" was caused by an empty (not absent) license file, the
    # abort message says so, rather than the misleading "has no root-level file".
    res = LicenseResolution(None, "none", None, 0, None, ("LICENSE",))
    with pytest.raises(SystemExit) as exc:
        full_scan.report_missing_root_license("owner/repo", False,
                                              full_scan.LOG_PREFIX, res)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "No Root-Level Licence Found" in out
    assert "exists but" in out and "empty" in out


def test_report_missing_root_license_notes_undetected_file(capsys):
    # A present-but-undetected non-empty file -> distinct status, not "no file".
    res = LicenseResolution(None, "none", "LICENSE", 1, None)
    with pytest.raises(SystemExit) as exc:
        full_scan.report_missing_root_license("owner/repo", True,
                                              full_scan.LOG_PREFIX, res)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "License Not Conclusively Detected" in out
    assert "could not be" in out and "No Root-Level Licence Found" not in out


def test_missing_root_license_reports_only(monkeypatch, tmp_path):
    # Same abort, report-only: exit 0, status still printed, scan still skipped.
    monkeypatch.setattr(full_scan, "resolve_license_details",
                        lambda repo_name: _res(None, source="none",
                                               license_file=None, num_files=0))
    monkeypatch.setattr(full_scan, "RepoScan", _boom_if_scanned)
    monkeypatch.setattr(full_scan, "FullScanner", _boom_if_scanned)
    monkeypatch.setattr(full_scan.os, "chdir", lambda path: None)

    result = CliRunner().invoke(
        full_scan.main, ["owner/repo", "false", "--repo-path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "No Root-Level Licence Found" in result.output

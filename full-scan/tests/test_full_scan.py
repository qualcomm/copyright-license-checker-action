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

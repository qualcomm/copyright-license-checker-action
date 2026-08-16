import pytest
from click.testing import CliRunner

import full_scan
from scanner.licenses import PERMISSIVE_LICENSES, COPYLEFT_LICENSES


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

    monkeypatch.setattr(full_scan, "resolve_license", lambda repo_name: resolved)
    monkeypatch.setattr(full_scan, "RepoScan", lambda **kwargs: object())
    monkeypatch.setattr(full_scan, "FullScanner", _FakeScanner)
    monkeypatch.setattr(full_scan.os, "chdir", lambda path: None)

    result = CliRunner().invoke(
        full_scan.main, ["owner/repo", "false", "--repo-path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert captured["allowed"] == expected_allowed

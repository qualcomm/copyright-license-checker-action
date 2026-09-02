import scanner.license_resolver as lr


def _with_license_file(tmp_path, monkeypatch, detected):
    """chdir into a temp repo that has a LICENSE file, and stub scancode
    detection to return `detected`."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "LICENSE").write_text("dummy license text")
    monkeypatch.setattr(lr, "_detect_license_from_file", lambda path: detected)


def test_proprietary_catch_all_falls_through(tmp_path, monkeypatch):
    # scancode mis-detects a real LICENSE as the proprietary catch-all; the
    # resolver must distrust it and fall through to config/default.
    _with_license_file(tmp_path, monkeypatch, "LicenseRef-scancode-proprietary-license")
    assert lr.resolve_license("owner/unconfigured-repo-xyz") == "BSD-3-Clause-Clear"


def test_bsd_variant_normalizes(tmp_path, monkeypatch):
    _with_license_file(tmp_path, monkeypatch, "BSD-2-Clause")
    assert lr.resolve_license("owner/unconfigured-repo-xyz") == "BSD-3-Clause-Clear"


def test_real_license_passthrough(tmp_path, monkeypatch):
    _with_license_file(tmp_path, monkeypatch, "MIT")
    assert lr.resolve_license("owner/unconfigured-repo-xyz") == "MIT"


def test_no_license_file_defaults(tmp_path, monkeypatch):
    # No LICENSE file -> config lookup misses -> default license.
    monkeypatch.chdir(tmp_path)
    assert lr.resolve_license("owner/unconfigured-repo-xyz") == "BSD-3-Clause-Clear"

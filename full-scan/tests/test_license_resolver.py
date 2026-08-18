# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

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


def test_alternate_license_filenames_are_recognized(tmp_path, monkeypatch):
    # A repo whose only license file uses the British spelling / lowercase form
    # must be recognized as HAVING a root license (not abort). Detection is
    # stubbed; the point is that the file is found and resolution does not abort.
    monkeypatch.setattr(lr, "_detect_license_from_file", lambda path: "MIT")
    for name in ("LICENCE", "license", "licence.md", "COPYING.md"):
        repo_dir = tmp_path / f"repo_{name}"
        repo_dir.mkdir()
        (repo_dir / name).write_text("dummy")
        monkeypatch.chdir(repo_dir)
        assert lr.resolve_license("owner/unconfigured-repo-xyz") == "MIT", name


def test_no_license_file_no_config_returns_none(tmp_path, monkeypatch):
    # No root-level license file AND no config entry -> no baseline. The resolver
    # must NOT fabricate a default; it returns None so the caller can abort.
    monkeypatch.chdir(tmp_path)
    assert lr.resolve_license("owner/unconfigured-repo-xyz") is None


def test_no_license_file_but_config_match_returns_config(tmp_path, monkeypatch):
    # No LICENSE file, but the repo is onboarded in config.py -> the explicit,
    # human-declared license is honored (a config entry is not a "default").
    monkeypatch.chdir(tmp_path)
    assert lr.resolve_license("qualcomm-linux/meta-qcom-kernel") == "GPL-2.0"


def test_license_file_present_unresolved_keeps_default(tmp_path, monkeypatch):
    # A license file EXISTS but scancode detects nothing -> a baseline can still
    # be assumed (the file is present), so the default is kept, NOT None.
    _with_license_file(tmp_path, monkeypatch, None)
    assert lr.resolve_license("owner/unconfigured-repo-xyz") == "BSD-3-Clause-Clear"

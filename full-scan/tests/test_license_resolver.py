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


# --- resolve_license_details: source metadata for the "why" reason -----------

def test_details_source_license_file(tmp_path, monkeypatch):
    _with_license_file(tmp_path, monkeypatch, "MIT")
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert (res.license, res.source, res.license_file, res.num_license_files) == \
        ("MIT", "license_file", "LICENSE", 1)


def test_details_bsd_normalize_still_license_file(tmp_path, monkeypatch):
    _with_license_file(tmp_path, monkeypatch, "BSD-2-Clause")
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert res.license == "BSD-3-Clause-Clear" and res.source == "license_file"


def test_details_proprietary_falls_through_to_default(tmp_path, monkeypatch):
    # File present but only the unreliable proprietary catch-all detected, no config
    # -> default, attributed to source "default" (a file is still present/counted).
    _with_license_file(tmp_path, monkeypatch, "LicenseRef-scancode-proprietary-license")
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert (res.license, res.source, res.num_license_files) == \
        ("BSD-3-Clause-Clear", "default", 1)


def test_details_config_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                      # no license file
    res = lr.resolve_license_details("qualcomm-linux/meta-qcom-kernel")
    assert (res.license, res.source, res.config_project) == \
        ("GPL-2.0", "config", "meta-qcom-kernel")


def test_details_none_when_no_baseline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert (res.license, res.source, res.num_license_files) == (None, "none", 0)


def test_details_counts_multiple_root_license_files(tmp_path, monkeypatch):
    # Two root-level license files -> count is 2, and the first by priority
    # (LICENSE beats COPYING) is the one used.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "LICENSE").write_text("dummy")
    (tmp_path / "COPYING").write_text("dummy")
    monkeypatch.setattr(lr, "_detect_license_from_file", lambda path: "MIT")
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert res.num_license_files == 2
    assert res.license_file == "LICENSE"
    assert res.source == "license_file"


def test_describe_resolution_texts():
    lf = lr.LicenseResolution("MIT", "license_file", "LICENSE", 1, None)
    assert lr.describe_resolution(lf) == "(based on license file LICENSE)"
    lf2 = lr.LicenseResolution("MIT", "license_file", "LICENSE", 3, None)
    assert "3 license files present" in lr.describe_resolution(lf2)
    cfg = lr.LicenseResolution("GPL-2.0", "config", None, 0, "meta-qcom-kernel")
    assert lr.describe_resolution(cfg) == "(from scanner/config.py entry for meta-qcom-kernel)"
    none = lr.LicenseResolution(None, "none", None, 0, None)
    assert lr.describe_resolution(none) == ""

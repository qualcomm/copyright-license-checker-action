# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import scanner.license_resolver as lr


# A genuine BSD-3-Clause LICENSE with the year-less Qualcomm header that scancode
# 32.2.1 mis-tags as proprietary -- the exact case the text heuristic must recover.
_BSD3_TEXT = """BSD 3-Clause License

Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this
   list of conditions and the following disclaimer in the documentation and/or
   other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors may
   be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS".
"""

# BSD-2-Clause: same redistribution clause but NO clause 3 -> must NOT match.
_BSD2_TEXT = """BSD 2-Clause License

Copyright (c) 2020 Example

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice.
2. Redistributions in binary form must reproduce the above copyright notice.

THIS SOFTWARE IS PROVIDED "AS IS".
"""


def _with_license_file(tmp_path, monkeypatch, detected, content="dummy license text"):
    """chdir into a temp repo with a LICENSE file (default: non-license text), and
    stub scancode detection to return `detected`."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "LICENSE").write_text(content)
    monkeypatch.setattr(lr, "_detect_license_from_file", lambda path: detected)


# --- _looks_like_bsd3 heuristic corpus ---------------------------------------

def test_looks_like_bsd3_corpus(tmp_path):
    def _w(text):
        p = tmp_path / "L"
        p.write_text(text)
        return lr._looks_like_bsd3(str(p))
    assert _w(_BSD3_TEXT) is True                    # genuine BSD-3 (Qualcomm header)
    assert _w(_BSD2_TEXT) is False                   # BSD-2: no clause 3
    assert _w("MIT License\n\nPermission is hereby granted, free of charge...") is False
    assert _w("My project. TODO: add a license.") is False
    assert _w("") is False


# --- resolution outcomes ------------------------------------------------------

def test_real_license_passthrough(tmp_path, monkeypatch):
    _with_license_file(tmp_path, monkeypatch, "MIT")
    assert lr.resolve_license("owner/unconfigured-repo-xyz") == "MIT"


def test_scancode_bsd_variant_normalizes(tmp_path, monkeypatch):
    # A real scancode BSD detection normalizes to the org canonical BSD.
    _with_license_file(tmp_path, monkeypatch, "BSD-2-Clause")
    res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
    assert res.license == "BSD-3-Clause-Clear" and res.source == "license_file"


def test_qcom_bsd_recovered_by_text_heuristic(tmp_path, monkeypatch):
    # scancode mis-tags the Qualcomm BSD LICENSE as proprietary (or detects nothing);
    # the text heuristic recovers it as a REAL BSD-3-Clause detection -- NOT a default.
    for detected in ("LicenseRef-scancode-proprietary-license", None):
        _with_license_file(tmp_path, monkeypatch, detected, content=_BSD3_TEXT)
        res = lr.resolve_license_details("owner/unconfigured-repo-xyz")
        assert res.license == "BSD-3-Clause-Clear", detected
        assert res.source == "license_file", detected


def test_proprietary_on_nonbsd_text_aborts(tmp_path, monkeypatch):
    # A proprietary catch-all on NON-BSD text: no heuristic match, no config ->
    # abort (no fabricated default). Records the file for the "undetected" message.
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

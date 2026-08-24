# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import os

import compare_tools_remote as ctr
import org_license_report as olr
from scanner.license_resolver import LicenseResolution


# --- build_repo_record: every resolution case --------------------------------

def test_build_record_license_file_single():
    res = LicenseResolution("BSD-3-Clause-Clear", "license_file", "LICENSE.txt", 1, None)
    rec = olr.build_repo_record("qualcomm/mink", "public", res, "main")
    assert rec["status"] == "detected"
    assert rec["license"] == "BSD-3-Clause-Clear"
    assert rec["issue"] == ""
    assert rec["based_on"] == (
        "[LICENSE.txt](https://github.com/qualcomm/mink/blob/main/LICENSE.txt)")


def test_build_record_license_file_multiple_notes_but_still_detected():
    res = LicenseResolution("MIT", "license_file", "LICENSE", 3, None)
    rec = olr.build_repo_record("q/r", "public", res, "main")
    assert rec["status"] == "detected"                 # a note, not a failure
    assert "3 license files present" in rec["issue"]
    assert "used LICENSE" in rec["issue"]


def test_build_record_config_source():
    res = LicenseResolution("GPL-2.0-only", "config", None, 0, "meta-qcom-kernel")
    rec = olr.build_repo_record("q/meta-qcom-kernel", "internal", res, "main")
    assert rec["status"] == "detected"
    assert rec["license"] == "GPL-2.0-only"
    assert rec["based_on"] == "scanner/config.py entry (meta-qcom-kernel)"
    assert rec["issue"] == ""


def test_build_record_none_undetected_file():
    res = LicenseResolution(None, "none", "LICENSE", 1, None, ())
    rec = olr.build_repo_record("q/r", "public", res, "main")
    assert rec["status"] == "issue"
    assert rec["license"] == "Unknown"
    assert rec["based_on"] == ""
    assert rec["issue"] == "License present but not conclusively detected"


def test_build_record_none_empty_file():
    res = LicenseResolution(None, "none", None, 0, None, ("LICENSE",))
    rec = olr.build_repo_record("q/r", "private", res, "main")
    assert rec["status"] == "issue"
    assert rec["issue"] == "Root-level license file present but empty"


def test_build_record_none_no_file():
    res = LicenseResolution(None, "none", None, 0, None, ())
    rec = olr.build_repo_record("q/r", "public", res, "main")
    assert rec["status"] == "issue"
    assert rec["issue"] == "No license file found"


def test_build_record_blob_link_uses_default_branch_and_host():
    res = LicenseResolution("BSD-3-Clause-Clear", "license_file", "LICENSE", 1, None)
    rec = olr.build_repo_record("owner/name", "public", res, "trunk",
                                host="github.example.com")
    assert rec["based_on"] == (
        "[LICENSE](https://github.example.com/owner/name/blob/trunk/LICENSE)")


# --- render_markdown ----------------------------------------------------------

def _detected(name):
    return {"repo_name": name, "visibility": "public", "license": "MIT",
            "based_on": "[LICENSE](url)", "issue": "", "status": "detected"}


def _issue(name, issue="No license file found"):
    return {"repo_name": name, "visibility": "internal", "license": "Unknown",
            "based_on": "", "issue": issue, "status": "issue"}


def test_render_markdown_header_rows_and_summary():
    records = [_detected("q/a"), _detected("q/b"), _issue("q/c")]
    md = olr.render_markdown("qualcomm", records, skipped=[{"repo_name": "q/f",
                                                            "reason": "fork"}])
    assert md.startswith("Org Name: qualcomm\n")
    assert olr._TABLE_HEADER in md
    assert olr._TABLE_SEP in md
    # Rows are numbered 1..N.
    assert "| 1 | q/a |" in md
    assert "| 3 | q/c |" in md
    assert "- Total repositories scanned: 3" in md
    assert "- Successfully detected: 2" in md
    assert "- Detection issues: 1" in md
    assert "- Skipped (size cap / fork / archived): 1" in md


def test_render_markdown_escapes_pipe_in_cells():
    rec = {"repo_name": "q/r", "visibility": "public", "license": "A | B",
           "based_on": "", "issue": "", "status": "detected"}
    md = olr.render_markdown("q", [rec], skipped=[])
    assert "A &#124; B" in md
    assert "| A | B |" not in md                        # the raw pipe must not survive


def test_esc_pipe():
    assert olr._esc("a|b|c") == "a&#124;b&#124;c"


# --- resolve_one_repo: I/O paths (clone/detection), monkeypatched ------------

def _base_task():
    return {"repo_name": "q/r", "clone_url": "https://github.com/q/r.git",
            "visibility": "public", "default_branch": "main", "host": "github.com",
            "token": "", "ca_bundle": "", "ref": None, "verbose": False}


def test_resolve_one_repo_clone_failure(monkeypatch):
    monkeypatch.setattr(olr, "clone_repo", lambda *a, **k: (False, "not found"))
    rec = olr.resolve_one_repo(_base_task())
    assert rec["status"] == "issue"
    assert rec["issue"] == "Clone failed: not found"
    assert rec["license"] == "Unknown"


def test_resolve_one_repo_detection_exception(monkeypatch):
    def fake_clone(url, dest, token, ca, ref=None):
        os.makedirs(dest)                               # so os.chdir(dest) succeeds
        return True, ""

    def boom(_repo):
        raise RuntimeError("scancode blew up")

    monkeypatch.setattr(olr, "clone_repo", fake_clone)
    monkeypatch.setattr(olr, "resolve_license_details", boom)
    cwd_before = os.getcwd()
    rec = olr.resolve_one_repo(_base_task())
    assert os.getcwd() == cwd_before                    # cwd restored even on error
    assert rec["status"] == "issue"
    assert "License detection error: scancode blew up" in rec["issue"]


def test_resolve_one_repo_success(monkeypatch):
    def fake_clone(url, dest, token, ca, ref=None):
        os.makedirs(dest)
        return True, ""

    res = LicenseResolution("BSD-3-Clause-Clear", "license_file", "LICENSE", 1, None)
    monkeypatch.setattr(olr, "clone_repo", fake_clone)
    monkeypatch.setattr(olr, "resolve_license_details", lambda _repo: res)
    rec = olr.resolve_one_repo(_base_task())
    assert rec["status"] == "detected"
    assert rec["license"] == "BSD-3-Clause-Clear"
    assert rec["based_on"] == (
        "[LICENSE](https://github.com/q/r/blob/main/LICENSE)")


# --- list_org_repos additive behavior (visibility / repo_type / skipped_out) --

def test_list_org_repos_captures_visibility_type_and_skips(monkeypatch):
    seen_urls = []
    page = [
        {"name": "pub", "owner": {"login": "q"}, "size": 10, "visibility": "public",
         "private": False, "default_branch": "main"},
        {"name": "intr", "owner": {"login": "q"}, "size": 10, "visibility": "internal",
         "private": True, "default_branch": "trunk"},
        {"name": "nofield", "owner": {"login": "q"}, "size": 10, "private": True},
        {"name": "forked", "owner": {"login": "q"}, "fork": True},
    ]

    def fake_get(url, tok, ca):
        seen_urls.append(url)
        return page if "page=1" in url else []

    monkeypatch.setattr(ctr, "_gh_get_json", fake_get)
    skipped = []
    repos = ctr.list_org_repos("q", "", "", include_archived=False,
                               max_repo_size_mb=0, repo_type="all",
                               skipped_out=skipped)
    assert any("type=all" in u for u in seen_urls)      # repo_type flows into the query
    assert [r["repo_name"] for r in repos] == ["q/pub", "q/intr", "q/nofield"]
    assert repos[0]["visibility"] == "public"
    assert repos[1]["visibility"] == "internal"
    assert repos[1]["default_branch"] == "trunk"
    # visibility falls back from the `private` flag when the field is absent.
    assert repos[2]["visibility"] == "private"
    assert repos[2]["default_branch"] == "HEAD"
    assert {"repo_name": "q/forked", "reason": "fork"} in skipped


def test_list_org_repos_defaults_preserve_public_behavior(monkeypatch):
    # Called the legacy way (no repo_type / skipped_out): still queries type=public.
    seen = []

    def fake_get(url, tok, ca):
        seen.append(url)
        return [{"name": "a", "owner": {"login": "q"}, "size": 1}] \
            if "page=1" in url else []

    monkeypatch.setattr(ctr, "_gh_get_json", fake_get)
    repos = ctr.list_org_repos("q", "", "", include_archived=False,
                               max_repo_size_mb=0)
    assert any("type=public" in u for u in seen)
    assert [r["repo_name"] for r in repos] == ["q/a"]


# --- structured link fields + HTML report ------------------------------------

def test_build_record_includes_structured_link_fields():
    res = LicenseResolution("BSD-3-Clause-Clear", "license_file", "LICENSE", 1, None)
    rec = olr.build_repo_record("q/r", "public", res, "main")
    assert rec["source"] == "license_file"
    assert rec["license_file"] == "LICENSE"
    assert rec["license_url"] == "https://github.com/q/r/blob/main/LICENSE"
    assert rec["config_project"] == ""

    cfg = LicenseResolution("GPL-2.0-only", "config", None, 0, "meta-qcom-kernel")
    crec = olr.build_repo_record("q/meta-qcom-kernel", "internal", cfg, "main")
    assert crec["source"] == "config"
    assert crec["config_project"] == "meta-qcom-kernel"
    assert crec["license_url"] == ""


def test_build_report_data_totals():
    records = [_detected("q/a"), _detected("q/b"), _issue("q/c")]
    data = olr.build_report_data("qualcomm", records, [{"repo_name": "q/f",
                                                        "reason": "fork"}],
                                 "github.com", "all", "2026-08-19 10:00:00")
    assert data["meta"]["org"] == "qualcomm"
    assert data["meta"]["repo_type"] == "all"
    assert data["totals"] == {"total": 3, "detected": 2, "issues": 1, "skipped": 1}
    assert data["records"] == records


def test_render_html_embeds_data_and_escapes_script():
    rec = olr.build_repo_record(
        "q/r", "public",
        LicenseResolution("MIT", "license_file", "LICENSE", 1, None), "main")
    data = olr.build_report_data("q</script>", [rec], [], "github.com", "all",
                                 "2026-08-19 10:00:00")
    html = olr.render_html(data)
    assert "@@DATA@@" not in html                      # placeholder substituted
    assert "q/r" in html                               # record data embedded
    assert "https://github.com/q/r/blob/main/LICENSE" in html
    # A '</' inside embedded JSON is escaped so it cannot close the <script> block.
    assert "q<\\/script>" in html
    assert "q</script>" not in html

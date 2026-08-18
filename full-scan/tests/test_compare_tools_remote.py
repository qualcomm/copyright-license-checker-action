import subprocess

import pytest

import compare_tools_remote as ctr


# --- clone_repo argv (incl. the new --branch/--ref threading) ----------------

def _capture_run(monkeypatch, returncode=0, stderr=""):
    """Stub subprocess.run in the remote tool; record the argv it was handed."""
    captured = {}

    def run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(ctr.subprocess, "run", run)
    return captured


def test_clone_repo_default_branch_no_ref(monkeypatch):
    captured = _capture_run(monkeypatch)
    ok, err = ctr.clone_repo("https://github.com/q/a.git", "/tmp/dest", "", "")
    assert ok and err == ""
    assert captured["cmd"] == [
        "git", "clone", "--depth=1", "--quiet",
        "https://github.com/q/a.git", "/tmp/dest"]
    assert "--branch" not in captured["cmd"]


def test_clone_repo_with_ref_adds_branch(monkeypatch):
    captured = _capture_run(monkeypatch)
    ctr.clone_repo("https://github.com/q/a.git", "/tmp/dest", "", "",
                   ref="full-matrix-fixtures")
    cmd = captured["cmd"]
    assert "--branch" in cmd
    assert cmd[cmd.index("--branch") + 1] == "full-matrix-fixtures"
    # --branch must precede the url/dest positionals.
    assert cmd.index("--branch") < cmd.index("https://github.com/q/a.git")


def test_clone_repo_token_embedded_and_scrubbed(monkeypatch):
    captured = _capture_run(
        monkeypatch, returncode=128,
        stderr="fatal: could not read from https://SECRET_TOK@github.com/q/a.git")
    ok, err = ctr.clone_repo("https://github.com/q/a.git", "/tmp/dest",
                             "SECRET_TOK", "")
    assert not ok
    # Token is embedded into the clone URL for auth...
    assert "https://SECRET_TOK@github.com/q/a.git" in captured["cmd"]
    # ...but scrubbed from any returned error text.
    assert "SECRET_TOK" not in err
    assert "***" in err


def test_clone_repo_git_missing(monkeypatch):
    def run(cmd, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(ctr.subprocess, "run", run)
    ok, err = ctr.clone_repo("https://github.com/q/a.git", "/tmp/dest", "", "")
    assert not ok
    assert "git executable not found" in err


# --- resolve_repo_list (explicit --repos path; no network) -------------------

def test_resolve_repo_list_explicit_dedup_and_bad_skip():
    repos = ctr.resolve_repo_list(
        orgs=[], repos_filter=["q/a", "q/b", "q/a", "no-slash"],
        token="", ca_bundle="", include_archived=False,
        max_repos=0, max_repo_size_mb=500)
    names = [r["repo_name"] for r in repos]
    assert names == ["q/a", "q/b"]                       # dedup + bad entry skipped
    assert repos[0]["clone_url"] == "https://github.com/q/a.git"


def test_resolve_repo_list_max_repos_cap():
    repos = ctr.resolve_repo_list(
        orgs=[], repos_filter=["q/a", "q/b", "q/c"],
        token="", ca_bundle="", include_archived=False,
        max_repos=2, max_repo_size_mb=500)
    assert [r["repo_name"] for r in repos] == ["q/a", "q/b"]


# --- list_org_repos filters (stub the GitHub API) ----------------------------

def _one_page(monkeypatch, page):
    """Stub _gh_get_json to return `page` once (list is <100 so no 2nd call)."""
    monkeypatch.setattr(ctr, "_gh_get_json", lambda url, tok, ca: page)


def test_list_org_repos_filters_fork_archived_size(monkeypatch):
    _one_page(monkeypatch, [
        {"name": "keep", "owner": {"login": "q"}, "size": 100,
         "clone_url": "https://github.com/q/keep.git"},
        {"name": "forked", "owner": {"login": "q"}, "size": 10, "fork": True},
        {"name": "arch", "owner": {"login": "q"}, "size": 10, "archived": True},
        {"name": "huge", "owner": {"login": "q"}, "size": 600 * 1024},  # 600MB > cap
    ])
    repos = ctr.list_org_repos("q", "", "", include_archived=False,
                               max_repo_size_mb=500)
    assert [r["repo_name"] for r in repos] == ["q/keep"]


def test_list_org_repos_include_archived(monkeypatch):
    _one_page(monkeypatch, [
        {"name": "arch", "owner": {"login": "q"}, "size": 10, "archived": True},
    ])
    repos = ctr.list_org_repos("q", "", "", include_archived=True,
                               max_repo_size_mb=0)          # 0 disables size cap
    assert [r["repo_name"] for r in repos] == ["q/arch"]


# --- build_aggregate_data ----------------------------------------------------

def test_build_aggregate_data_totals_and_error_ordering():
    results = [
        {"repo_name": "q/b", "error": None, "repolinter_ok": True,
         "fs_blocks_rl_clean": False,
         "summary": {"both": 2, "only_full_scan": 1, "only_repolinter": 0,
                     "incompat_count": 1, "fs_blocking": 3}},
        {"repo_name": "q/a", "error": "clone_failed", "summary": {}},
    ]
    data = ctr.build_aggregate_data({"orgs": ["q"]}, results)
    tot = data["totals"]
    assert tot["repos_total"] == 2
    assert tot["repos_ok"] == 1
    assert tot["clone_failed"] == 1
    assert tot["repos_incompat"] == 1
    assert tot["total_flagged_files"] == 3
    assert tot["agreement_pct"] == 67                       # round(2/3*100)
    # errors sort last; ok repos alphabetical.
    assert [r["repo_name"] for r in data["repos"]] == ["q/b", "q/a"]


def test_error_record_clone_vs_scan():
    clone = ctr._error_record("q/a", "clone_failed", "boom")
    assert clone["error"] == "clone_failed" and clone["clone_ok"] is False
    scan = ctr._error_record("q/a", "scan_failed", "boom")
    assert scan["clone_ok"] is True                          # cloned, then scan failed


# --- report template: links target the scanned ref, not HEAD -----------------

def test_report_links_use_ref_not_hardcoded_head():
    tpl = ctr._HTML_TEMPLATE
    assert "function refSlug" in tpl
    assert "/blob/HEAD/" not in tpl                          # no hardcoded default branch
    assert '"/blob/" + refSlug()' in tpl                     # fileUrl derives the ref


def test_render_html_escapes_script_and_fills_data():
    data = {"meta": {"ref": "full-matrix-fixtures"}, "totals": {},
            "repos": [{"repo_name": "q/</script>", "summary": {}}]}
    html = ctr.render_html(data)
    assert "@@DATA@@" not in html                            # placeholder substituted
    # A '</' inside embedded JSON is escaped so it cannot close the <script> block.
    assert "q/<\\/script>" in html
    assert "q/</script>" not in html

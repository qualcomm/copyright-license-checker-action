import os
import sys
import ssl
import json
import logging
import tempfile
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

import click

# This file lives in <action-repo>/scripts/, one level below the repo root. Put the
# repo root on sys.path so `python scripts/compare_tools_remote.py ...` resolves the
# root-level main/scanner packages that compare_tools imports transitively.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pylint: disable=wrong-import-position
import compare_tools as ct

"""
Org-wide, multi-repo version of compare_tools.py.

compare_tools.py compares repolinter against our full_scan for ONE local checkout.
This tool does the same comparison across MANY public GitHub repos at once: it
enumerates repos in one or more GitHub orgs (via the GitHub REST API), shallow-clones
each into a throwaway temp dir (git clone --depth=1), runs both tools, and aggregates
the per-file license/copyright comparison into a single self-contained interactive HTML
report (plus a JSON dump for auditing). Temp clones are removed automatically.

Like compare_tools.py this is a read-only DIAGNOSTIC, not a CI gate. It builds the
evidence, at scale, for eventually retiring repolinter's license/copyright rules in
favour of full_scan.

Usage:
    python scripts/compare_tools_remote.py [--orgs ORG ...] [--repos owner/repo ...]
                                           [--max-repos N] [--include-archived]
                                           [--include-licenseignore] [--workers N]
                                           [--ruleset-url URL] [--output FILE]
                                           [--open] [--port N] [--verbose]

    With no --orgs/--repos it enumerates the default Qualcomm public orgs. Use --repos
    owner/repo (repeatable) to scan an explicit set and skip API enumeration. The
    aggregate report is ALWAYS served on 0.0.0.0:<port> (default 8000) until Ctrl-C.

    Auth: GITHUB_TOKEN (if set) raises the API rate limit and is used for cloning.
    Corporate SSL: REQUESTS_CA_BUNDLE (if set) is passed to git as GIT_SSL_CAINFO and
    to the API calls.

Runtime dependencies: `git`, `docker`, and `scancode` must be on PATH.
"""

LOG_PREFIX = "< repolinter vs full-scan (multi-repo) >"

# Public github.com only (per design). Same org list style as the reference tool.
GITHUB_HOST = "github.com"
GITHUB_API = "https://api.github.com"
DEFAULT_ORGS = ["qualcomm", "qualcomm-linux", "qualcomm-qrb-ros", "audioreach", "quic"]

# Huge kernel mirrors -- a shallow clone + scancode run over these is impractical.
SKIP_REPOS = {"linux-kernel", "linux-kernel-topics"}


def _log(msg: str) -> None:
    """Emit a progress/status line to stderr (stdout is reserved for the summary)."""
    print(f"{LOG_PREFIX} {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# GitHub enumeration
# --------------------------------------------------------------------------- #

def _gh_get_json(url: str, token: str, ca_bundle: str):
    """
    GET a GitHub API URL and return the parsed JSON body.

    Args:
        url (str): The API URL.
        token (str): GITHUB_TOKEN or "" (unauthenticated).
        ca_bundle (str): CA bundle path for SSL, or "" for the system default.

    Returns:
        The decoded JSON (list or dict).
    """
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "copyright-license-checker-compare-tools",
    })
    if token:
        req.add_header("Authorization", f"token {token}")
    ctx = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else None
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:  # nosec B310
        return json.loads(resp.read().decode("utf-8"))


def list_org_repos(org: str, token: str, ca_bundle: str,
                   include_archived: bool) -> list:
    """
    List an org's public, non-fork repos (paginated), applying the skip filters.

    A listing failure for one org (rate limit, network, org not found) is logged and
    yields an empty list so the rest of the run continues.

    Args:
        org (str): The GitHub org login.
        token (str): GITHUB_TOKEN or "".
        ca_bundle (str): CA bundle path or "".
        include_archived (bool): Keep archived repos (excluded by default).

    Returns:
        list: [{"repo_name": "owner/name", "clone_url": "https://..."}].
    """
    repos = []
    page = 1
    while True:
        url = (f"{GITHUB_API}/orgs/{org}/repos"
               f"?type=public&per_page=100&page={page}")
        try:
            batch = _gh_get_json(url, token, ca_bundle)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                _log(f"WARNING: GitHub API 403 for org '{org}' (rate limit?). Set "
                     f"GITHUB_TOKEN to raise the limit. Skipping remaining pages.")
            elif exc.code == 404:
                _log(f"WARNING: org '{org}' not found (404); skipping.")
            else:
                _log(f"WARNING: GitHub API error {exc.code} for org '{org}': "
                     f"{exc.reason}")
            break
        except urllib.error.URLError as exc:
            _log(f"WARNING: could not reach GitHub API for org '{org}': {exc.reason}")
            break

        if not batch:
            break
        for entry in batch:
            if entry.get("fork"):
                continue
            if entry.get("archived") and not include_archived:
                continue
            name = entry.get("name")
            if not name or name in SKIP_REPOS:
                continue
            owner = (entry.get("owner") or {}).get("login", org)
            repos.append({
                "repo_name": f"{owner}/{name}",
                "clone_url": (entry.get("clone_url")
                              or f"https://{GITHUB_HOST}/{owner}/{name}.git"),
            })
        if len(batch) < 100:
            break
        page += 1
    return repos


def resolve_repo_list(orgs: list, repos_filter: list, token: str, ca_bundle: str,
                      include_archived: bool, max_repos: int) -> list:
    """
    Build the final repo work-list from --repos (explicit) or --orgs (enumerated).

    De-duplicates by repo_name and applies --max-repos, logging any truncation so the
    cap is never silent.

    Args:
        orgs (list): Orgs to enumerate (used only when repos_filter is empty).
        repos_filter (list): Explicit "owner/repo" list; skips API enumeration.
        token (str): GITHUB_TOKEN or "".
        ca_bundle (str): CA bundle path or "".
        include_archived (bool): Keep archived repos.
        max_repos (int): Hard cap on repos processed (0 = no cap).

    Returns:
        list: [{"repo_name", "clone_url"}] to scan.
    """
    if repos_filter:
        result = []
        for raw in repos_filter:
            name = raw.strip()
            if "/" not in name:
                _log(f"WARNING: --repos '{name}' is not 'owner/repo'; skipping.")
                continue
            owner, repo = name.split("/", 1)
            result.append({
                "repo_name": name,
                "clone_url": f"https://{GITHUB_HOST}/{owner}/{repo}.git",
            })
    else:
        result = []
        for org in orgs:
            found = list_org_repos(org, token, ca_bundle, include_archived)
            _log(f"org '{org}': {len(found)} repo(s) to scan")
            result.extend(found)

    seen = set()
    deduped = []
    for entry in result:
        if entry["repo_name"] in seen:
            continue
        seen.add(entry["repo_name"])
        deduped.append(entry)

    if max_repos and len(deduped) > max_repos:
        _log(f"NOTE: capping {len(deduped)} repos to --max-repos={max_repos} "
             f"({len(deduped) - max_repos} dropped).")
        deduped = deduped[:max_repos]
    return deduped


# --------------------------------------------------------------------------- #
# Clone + per-repo comparison (the ProcessPool worker)
# --------------------------------------------------------------------------- #

def clone_repo(clone_url: str, dest: str, token: str, ca_bundle: str) -> tuple:
    """
    Shallow-clone a repo into dest.

    The token, when set, is embedded in the URL for auth; it is scrubbed from any
    returned error text so it can never leak into the report/JSON.

    Args:
        clone_url (str): The https clone URL.
        dest (str): Target directory (must not already exist).
        token (str): GITHUB_TOKEN or "".
        ca_bundle (str): CA bundle path or "" (sets GIT_SSL_CAINFO).

    Returns:
        tuple: (ok: bool, err: str). err is "" on success.
    """
    url = clone_url
    if token and url.startswith("https://"):
        url = "https://" + token + "@" + url[len("https://"):]
    env = os.environ.copy()
    if ca_bundle:
        env["GIT_SSL_CAINFO"] = ca_bundle
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth=1", "--quiet", url, dest],
            capture_output=True, text=True, env=env, check=False, timeout=600,
        )
    except FileNotFoundError:
        return False, "git executable not found on PATH."
    except subprocess.TimeoutExpired:
        return False, "git clone timed out after 600s."
    err = (proc.stderr or "").strip()
    if token:
        err = err.replace(token, "***")
    return proc.returncode == 0, "" if proc.returncode == 0 else err[:800]


def _error_record(repo_name: str, kind: str, detail: str) -> dict:
    """Build a per-repo result record for a clone/scan failure."""
    return {
        "repo_name": repo_name, "error": kind, "error_detail": str(detail)[:800],
        "clone_ok": kind != "clone_failed", "repolinter_ok": False,
        "repolinter_error": None, "license": None,
        "summary": {}, "files": [], "repolinter_all": [], "fs_blocks_rl_clean": False,
    }


def process_one_repo(task: dict) -> dict:
    """
    Clone one repo, run both tools, and return a compact comparison record.

    Runs in a separate process (ProcessPoolExecutor): run_full_scan does a
    process-global os.chdir and main.get_license reads cwd, so full_scan is not
    thread-safe -- one process per in-flight repo keeps cwd isolated. A fresh clone
    has everything git-tracked, so include_untracked is unnecessary and repolinter /
    full_scan see the same file set automatically.

    Args:
        task (dict): {repo_name, clone_url, ruleset_url, token, ca_bundle, verbose}.

    Returns:
        dict: Compact per-repo record (summary + files + repolinter_all), or an error
              record. Always JSON-serializable; never raises for expected failures.
    """
    repo_name = task["repo_name"]
    with tempfile.TemporaryDirectory(prefix="cmpremote-") as tmp:
        dest = os.path.join(tmp, "repo")
        ok, clone_err = clone_repo(task["clone_url"], dest, task["token"],
                                   task["ca_bundle"])
        if not ok:
            return _error_record(repo_name, "clone_failed", clone_err)

        try:
            license_id, flagged, warning, scanned, ignored = ct.run_full_scan(
                repo_name, dest, False, task["verbose"],
                task["include_licenseignore"])
        except Exception as exc:  # pylint: disable=broad-except
            return _error_record(repo_name, "scan_failed", f"full_scan failed: {exc}")

        scanner_view = ct.normalize_scanner(license_id, flagged, warning, scanned,
                                            ignored)

        raw_rl = {}
        repolinter_ok = False
        repolinter_error = None
        try:
            raw_rl = ct.run_repolinter(dest, task["ruleset_url"])
            repolinter_ok = True
        except ct.RepolinterUnavailable as exc:
            repolinter_error = str(exc)
        rl_view = ct.normalize_repolinter(raw_rl)
        if rl_view.errored:
            repolinter_ok = False
            repolinter_error = f"repolinter reported an error: {rl_view.err_msg}"

        records = ct.build_comparison(scanner_view, rl_view, False)
        data = ct.build_report_data({}, scanner_view, rl_view, records,
                                    flagged, warning, raw_rl)
        summary = data["summary"]
        return {
            "repo_name": repo_name,
            "error": None,
            "clone_ok": True,
            "license": license_id,
            "repolinter_ok": repolinter_ok,
            "repolinter_error": repolinter_error,
            "summary": summary,
            "files": data["files"],
            "repolinter_all": data["repolinter_all"],
            # full_scan blocks but repolinter's error-level header rules are clean:
            # the key divergence motivating the eventual repolinter-rule retirement.
            "fs_blocks_rl_clean": bool(summary.get("fs_blocking", 0) > 0
                                       and summary.get("rl_error_files", 0) == 0),
        }


# --------------------------------------------------------------------------- #
# Aggregation + rendering
# --------------------------------------------------------------------------- #

def _log_progress(done: int, total: int, rec: dict) -> None:
    """Print a one-line progress update for a finished repo."""
    if rec.get("error"):
        _log(f"[{done}/{total}] {rec['repo_name']}: ERROR ({rec['error']})")
        return
    summ = rec.get("summary", {})
    flag = " *** full_scan blocks, repolinter clean ***" \
        if rec.get("fs_blocks_rl_clean") else ""
    _log(f"[{done}/{total}] {rec['repo_name']}: both={summ.get('both', 0)} "
         f"only_fs={summ.get('only_full_scan', 0)} "
         f"only_rl={summ.get('only_repolinter', 0)} "
         f"incompat={summ.get('incompat_count', 0)}{flag}")


def build_aggregate_data(meta: dict, results: list) -> dict:
    """
    Roll per-repo records into aggregate totals + a sorted repos list for the report.

    Args:
        meta (dict): Run metadata (orgs, ruleset, image, timestamp, ...).
        results (list): Per-repo records from process_one_repo.

    Returns:
        dict: {"meta", "totals", "repos"} ready for HTML/JSON serialization.
    """
    ok_repos = [r for r in results if r.get("error") is None]
    both = sum(r["summary"].get("both", 0) for r in ok_repos)
    only_fs = sum(r["summary"].get("only_full_scan", 0) for r in ok_repos)
    only_rl = sum(r["summary"].get("only_repolinter", 0) for r in ok_repos)
    union = both + only_fs + only_rl

    totals = {
        "repos_total": len(results),
        "repos_ok": len(ok_repos),
        "clone_failed": sum(1 for r in results if r.get("error") == "clone_failed"),
        "scan_failed": sum(1 for r in results if r.get("error") == "scan_failed"),
        "repolinter_unavailable": sum(1 for r in ok_repos
                                      if not r.get("repolinter_ok")),
        "repos_incompat": sum(1 for r in ok_repos
                              if r["summary"].get("incompat_count", 0) > 0),
        "repos_fs_only": sum(1 for r in ok_repos if r.get("fs_blocks_rl_clean")),
        "total_flagged_files": sum(r["summary"].get("fs_blocking", 0)
                                   for r in ok_repos),
        "agreement_pct": round(both / union * 100) if union else 100,
    }

    # Errors last, then alphabetical -- a stable, scannable default order.
    repos_sorted = sorted(
        results, key=lambda r: (r.get("error") is not None, r["repo_name"].lower()))
    return {"meta": meta, "totals": totals, "repos": repos_sorted}


def render_html(data: dict) -> str:
    """Render the self-contained aggregate HTML report (see compare_tools.render_html)."""
    data_json = json.dumps(data).replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("@@DATA@@", data_json)


def _write_text(text: str, path: str) -> None:
    """Write text to path (utf-8)."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

@click.command()
@click.option("--orgs", multiple=True,
              help="GitHub org(s) to enumerate (repeatable). Default: the Qualcomm "
                   "public orgs. Ignored when --repos is given.")
@click.option("--repos", "repos_filter", multiple=True,
              help="Explicit owner/repo to scan (repeatable); skips org enumeration.")
@click.option("--max-repos", type=int, default=0,
              help="Cap the number of repos processed (0 = no cap). scancode is slow, "
                   "so a full-org run can take a long time.")
@click.option("--include-archived", is_flag=True, default=False, show_default=True,
              help="Include archived repos (excluded by default).")
@click.option("--include-licenseignore", is_flag=True, default=False,
              show_default=True,
              help="Also scan files the repo's .licenseignore excludes. Repolinter "
                   "ignores .licenseignore, so this gives parity on repos that use "
                   "it (otherwise those files show as repolinter-only).")
@click.option("--workers", type=int, default=4, show_default=True,
              help="Parallel worker PROCESSES (full_scan is not thread-safe). Keep "
                   "modest: several parallel scancode runs are CPU/memory heavy. "
                   "Use 1 for sequential/debug.")
@click.option("--ruleset-url", default=ct.DEFAULT_RULESET_URL, show_default=True,
              help="repolinter ruleset URL.")
@click.option("--output", default=None,
              type=click.Path(dir_okay=False, resolve_path=True),
              help="Aggregate HTML path. Default: "
                   "<action-repo>/reports/multi_<YYYYMMDD-HHMMSS>.html (a .json "
                   "sibling is written alongside).")
@click.option("--open", "open_browser", is_flag=True, default=False,
              help="Open the served report in a browser when the server starts.")
@click.option("--port", default=8000, show_default=True, type=int,
              help="Port to serve the report on (always served on 0.0.0.0 until Ctrl-C).")
@click.option("--verbose", is_flag=True, default=False,
              help="Echo suppressed get_license/scan chatter to stderr.")
def main(orgs, repos_filter, max_repos, include_archived, include_licenseignore,
         workers, ruleset_url, output, open_browser, port, verbose):
    """
    Compare repolinter and full_scan across many GitHub repos; write an HTML report.

    Enumerates orgs (or an explicit --repos set), shallow-clones each repo, runs both
    tools, and aggregates the per-file comparison. Diagnostic only: exits 0 whenever a
    report was produced; exits 2 only when NO repo could be scanned.
    """
    logging.basicConfig(level=logging.WARNING)

    orgs = list(orgs) or DEFAULT_ORGS
    repos_filter = list(repos_filter)
    token = os.environ.get("GITHUB_TOKEN") or ""
    ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or ""

    _log("Enumerating repositories...")
    repo_list = resolve_repo_list(orgs, repos_filter, token, ca_bundle,
                                  include_archived, max_repos)
    if not repo_list:
        _log("ERROR: no repositories to scan. With no --repos, enumeration returned "
             "nothing -- check --orgs, network, and GITHUB_TOKEN (rate limits).")
        sys.exit(2)

    _log(f"Scanning {len(repo_list)} repo(s) with {workers} worker(s). scancode is "
         f"slow -- this can take a while.")

    tasks = [{
        "repo_name": r["repo_name"], "clone_url": r["clone_url"],
        "ruleset_url": ruleset_url, "token": token, "ca_bundle": ca_bundle,
        "verbose": verbose, "include_licenseignore": include_licenseignore,
    } for r in repo_list]

    results = []
    total = len(tasks)
    done = 0
    if workers <= 1:
        for task in tasks:
            try:
                rec = process_one_repo(task)
            except Exception as exc:  # pylint: disable=broad-except
                rec = _error_record(task["repo_name"], "scan_failed", exc)
            done += 1
            _log_progress(done, total, rec)
            results.append(rec)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_one_repo, t): t for t in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    rec = future.result()
                except Exception as exc:  # pylint: disable=broad-except
                    rec = _error_record(task["repo_name"], "scan_failed", exc)
                done += 1
                _log_progress(done, total, rec)
                results.append(rec)

    now = datetime.now()
    output = ct.resolve_output_path(output, "multi", now)
    meta = {
        "orgs": orgs if not repos_filter else ["(explicit --repos)"],
        "ruleset_url": ruleset_url,
        "repolinter_image": ct.REPOLINTER_IMAGE,
        "github_host": GITHUB_HOST,
        "workers": workers,
        "include_licenseignore": include_licenseignore,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data = build_aggregate_data(meta, results)
    _write_text(render_html(data), output)
    json_path = os.path.splitext(output)[0] + ".json"
    _write_text(json.dumps(data, indent=2), json_path)

    tot = data["totals"]
    _log(f"Done. repos ok: {tot['repos_ok']}/{tot['repos_total']} | clone-failed: "
         f"{tot['clone_failed']} | incompat repos: {tot['repos_incompat']} | "
         f"full_scan-blocks-rl-clean: {tot['repos_fs_only']} | overall agreement: "
         f"{tot['agreement_pct']}%")
    print(f"{LOG_PREFIX} Report: {output}")
    print(f"{LOG_PREFIX} JSON:   {json_path}")

    # Always serve (blocks until Ctrl-C), then exit 0 -- a report was produced.
    ct._serve_report(output, port, open_browser)  # pylint: disable=protected-access
    sys.exit(0)


# --------------------------------------------------------------------------- #
# HTML template (self-contained: inline CSS + vanilla JS, no external assets)
# --------------------------------------------------------------------------- #

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Repolinter vs full_scan &mdash; org-wide</title>
<style>
  :root {
    --bg: #0f1420; --panel: #171d2b; --panel2: #1e2536; --border: #2b3346;
    --fg: #e6ebf5; --muted: #9aa7bd; --accent: #4f8cff;
    --err: #ff5c6c; --warn: #ffb84d; --ok: #43d19e; --info: #7aa2ff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  header { padding: 20px 24px; background: var(--panel);
    border-bottom: 1px solid var(--border); }
  h1 { margin: 0 0 4px; font-size: 20px; }
  .meta { color: var(--muted); font-size: 13px; display: grid;
    grid-template-columns: max-content 1fr; gap: 2px 12px; margin-top: 10px; max-width: 900px; }
  .meta b { color: var(--fg); font-weight: 600; }
  .wrap { padding: 0 24px 60px; max-width: 1280px; margin: 0 auto; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit,minmax(160px,1fr));
    gap: 12px; margin: 18px 0; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; }
  .card .n { font-size: 26px; font-weight: 700; }
  .card .l { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .card.err .n { color: var(--err); } .card.warn .n { color: var(--warn); }
  .card.ok .n { color: var(--ok); } .card.info .n { color: var(--info); }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 14px 0; }
  input[type=text] { background: var(--panel2); color: var(--fg);
    border: 1px solid var(--border); border-radius: 7px; padding: 7px 10px; font-size: 13px;
    min-width: 240px; }
  .chip { padding: 5px 11px; border-radius: 999px; border: 1px solid var(--border);
    background: var(--panel2); color: var(--muted); cursor: pointer; font-size: 12px; }
  .chip.active { color: #fff; border-color: var(--accent); background: #22304e; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
    vertical-align: top; }
  th { color: var(--muted); font-weight: 600; cursor: pointer; user-select: none;
    white-space: nowrap; }
  th.num, td.num { text-align: right; }
  tr.row { cursor: pointer; }
  tr.row:hover { background: var(--panel2); }
  td.path { font-family: ui-monospace, "SF Mono", Menlo, monospace; word-break: break-all; }
  .detail td { background: #10151f; }
  table.sub { margin: 6px 0; }
  table.sub th { color: var(--muted); cursor: default; }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 6px; font-size: 11px;
    font-weight: 600; margin: 1px 3px 1px 0; white-space: nowrap; }
  .b-err { background: #3a1d22; color: var(--err); border: 1px solid #5b2730; }
  .b-warn { background: #3a2f1a; color: var(--warn); border: 1px solid #5b4a27; }
  .b-ok { background: #14342a; color: var(--ok); border: 1px solid #1f5343; }
  .b-info { background: #1c2540; color: var(--info); border: 1px solid #2c3a63; }
  .b-cat-BOTH { background: #3a2f1a; color: var(--warn); border: 1px solid #5b4a27; }
  .b-cat-ONLY_FULL_SCAN { background: #1c2540; color: var(--info); border: 1px solid #2c3a63; }
  .b-cat-ONLY_REPOLINTER { background: #2a2033; color: #c69bff; border: 1px solid #43315b; }
  .tag { display: inline-block; padding: 1px 7px; border-radius: 6px; font-size: 11px;
    background: var(--panel2); color: var(--muted); border: 1px solid var(--border);
    margin: 1px 3px 1px 0; }
  .tag.compat { color: var(--warn); border-color: #5b4a27; }
  .banner { background: #3a1d22; border: 1px solid var(--err); color: #ffd7db;
    padding: 8px 12px; border-radius: 8px; margin: 6px 0; white-space: pre-wrap; }
  .warnnote { background: #3a2f1a; border: 1px solid #5b4a27; color: #ffe7c2;
    padding: 6px 10px; border-radius: 8px; margin: 6px 0; }
  .legend { color: var(--muted); margin-top: 26px; }
  .legend li { margin: 6px 0; }
  .legend code { background: var(--panel2); padding: 1px 5px; border-radius: 4px; color: var(--fg); }
  .muted { color: var(--muted); } .empty { color: var(--muted); padding: 20px; text-align: center; }
  button.dl { background: var(--panel2); color: var(--fg); border: 1px solid var(--border);
    border-radius: 7px; padding: 7px 12px; cursor: pointer; margin-top: 10px; }
</style>
</head>
<body>
<header>
  <h1>Repolinter <span class="muted">vs</span> full_scan &mdash; org-wide comparison</h1>
  <div id="meta" class="meta"></div>
</header>
<div class="wrap">
  <div id="cards" class="cards"></div>
  <div class="controls">
    <input type="text" id="search" placeholder="Filter by repository...">
    <span class="chip active" data-f="ALL">All</span>
    <span class="chip" data-f="INCOMPAT">Incompatible license</span>
    <span class="chip" data-f="FSONLY">full_scan blocks, repolinter clean</span>
    <span class="chip" data-f="ERROR">Errors</span>
  </div>
  <table id="repos-table">
    <thead><tr>
      <th data-sort="repo_name">Repository</th>
      <th data-sort="license">License</th>
      <th class="num" data-sort="scanned">Files</th>
      <th class="num" data-sort="both">Both</th>
      <th class="num" data-sort="only_fs">Only full_scan</th>
      <th class="num" data-sort="only_rl">Only repolinter</th>
      <th class="num" data-sort="incompat">Incompat</th>
      <th class="num" data-sort="agreement">Agreement</th>
      <th data-sort="status">Status</th>
    </tr></thead>
    <tbody id="repos-body"></tbody>
  </table>
  <div id="repos-empty" class="empty" style="display:none">No repositories matched.</div>

  <div class="legend">
    <ul>
      <li><b>Click a repository row</b> to expand its per-file license/copyright
        comparison (File &middot; full_scan &middot; repolinter header &middot; Category
        &middot; Divergence), the same view as the single-repo tool.</li>
      <li><b>Header presence vs compatibility.</b> repolinter's header rules only check
        that a copyright line and an SPDX/BSD notice <i>exist</i>; they never evaluate
        whether the license is <i>allowed</i>. full_scan's <code>INCOMPAT</code>/<code>UNCERT</code>
        findings have no repolinter analog &mdash; that is the divergence this tool
        surfaces at scale.</li>
      <li><b>"full_scan blocks, repolinter clean"</b> flags repos where full_scan found a
        blocking issue but no repolinter error-level header rule failed &mdash; the
        evidence for eventually retiring repolinter's license/copyright rules.</li>
      <li><b>Codes.</b> <code>SLH</code>=source-license-headers-exist,
        <code>QSLH</code>=qualcomm-source-license-headers-exist,
        <code>SQLH</code>=source-qualcomm-license-headers-exist;
        <code>NOLIC</code>/<code>INCOMPAT</code>/<code>UNCERT</code>/<code>NOCR</code>/<code>CRHOLDER</code>
        are full_scan finding kinds.</li>
    </ul>
    <button class="dl" id="dl-json">Download raw JSON</button>
  </div>
</div>

<script>
const DATA = @@DATA@@;

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
function sevClass(sev) { return sev === "error" ? "b-err" : "b-warn"; }

// ---- meta + cards ----
const m = DATA.meta;
document.getElementById("meta").innerHTML = [
  ["Orgs", (m.orgs || []).join(", ")],
  ["Ruleset", m.ruleset_url],
  ["Repolinter", m.repolinter_image],
  ["GitHub host", m.github_host],
  ["Workers", m.workers],
  ["Generated", m.generated_at],
].map(([k, v]) => "<b>" + esc(k) + "</b><span>" + esc(v) + "</span>").join("");

const t = DATA.totals;
const cards = [
  [t.repos_ok + " / " + t.repos_total, "Repos scanned OK", "ok"],
  [t.repos_incompat, "Repos w/ incompatible license", "err"],
  [t.repos_fs_only, "full_scan blocks, repolinter clean", "warn"],
  [t.agreement_pct + "%", "Overall file agreement", "ok"],
  [t.total_flagged_files, "full_scan blocking files (total)", "err"],
  [t.clone_failed, "Clone failures", "info"],
  [t.repolinter_unavailable, "Repolinter unavailable", "info"],
];
document.getElementById("cards").innerHTML = cards.map(([n, label, cls]) =>
  "<div class=\"card " + cls + "\"><div class=\"n\">" + esc(n) +
  "</div><div class=\"l\">" + esc(label) + "</div></div>").join("");

// ---- per-file badge helpers (shared vocabulary with the single-repo tool) ----
function fsBadges(list) {
  if (!list || !list.length) return "<span class=\"muted\">&mdash;</span>";
  return list.map(f => "<span class=\"badge " + sevClass(f.severity) + "\" title=\"" +
    esc(f.detail) + "\">" + esc(f.kind) + "</span>").join("");
}
function rlBadges(list) {
  if (!list || !list.length) return "<span class=\"muted\">&mdash;</span>";
  return list.map(f => {
    const cls = f.passed ? "b-ok" : (f.level === "error" ? "b-err" : "b-warn");
    return "<span class=\"badge " + cls + "\" title=\"" + esc(f.rule) + "\">" +
      esc(f.code) + " " + (f.passed ? "✓" : "✗") + "</span>";
  }).join("");
}
function tagBadges(tags) {
  if (!tags || !tags.length) return "<span class=\"muted\">&mdash;</span>";
  return tags.map(t => "<span class=\"tag" +
    (t.indexOf("compatibility") === 0 ? " compat" : "") + "\">" + esc(t) +
    "</span>").join("");
}

// ---- repo row model ----
function statusOf(r) {
  if (r.error) return r.error;
  if (!r.repolinter_ok) return "rl-unavailable";
  return "ok";
}
function statusBadge(st) {
  const cls = (st === "ok") ? "b-ok" :
    (st === "rl-unavailable") ? "b-warn" : "b-err";
  return "<span class=\"badge " + cls + "\">" + esc(st) + "</span>";
}
function sortVal(r, key) {
  const s = r.summary || {};
  switch (key) {
    case "repo_name": return r.repo_name.toLowerCase();
    case "license": return (r.license || "").toLowerCase();
    case "scanned": return s.scanned_files || 0;
    case "both": return s.both || 0;
    case "only_fs": return s.only_full_scan || 0;
    case "only_rl": return s.only_repolinter || 0;
    case "incompat": return s.incompat_count || 0;
    case "agreement": return r.error ? -1 : (s.agreement_pct != null ? s.agreement_pct : 100);
    case "status": return statusOf(r);
    default: return r.repo_name.toLowerCase();
  }
}
function matchFilter(r, f) {
  if (f === "ALL") return true;
  if (f === "ERROR") return !!r.error;
  if (f === "INCOMPAT") return (r.summary || {}).incompat_count > 0;
  if (f === "FSONLY") return !!r.fs_blocks_rl_clean;
  return true;
}

function repoDetail(r) {
  if (r.error) {
    return "<div class=\"banner\">" + esc(r.error) +
      (r.error_detail ? ": " + esc(r.error_detail) : "") + "</div>";
  }
  let head = "";
  if (!r.repolinter_ok) {
    head += "<div class=\"warnnote\">repolinter unavailable for this repo &mdash; " +
      "showing the full_scan side only." +
      (r.repolinter_error ? " " + esc(r.repolinter_error) : "") + "</div>";
  }
  if (!r.files || !r.files.length) {
    return head + "<div class=\"muted\" style=\"padding:10px\">No per-file " +
      "license/copyright findings. " +
      esc((r.summary || {}).scanned_files || 0) + " files scanned.</div>";
  }
  const rows = r.files.map(f =>
    "<tr><td class=path>" + esc(f.path) + "</td><td>" + fsBadges(f.full_scan) +
    "</td><td>" + rlBadges(f.repolinter) + "</td><td><span class=\"badge b-cat-" +
    f.category + "\">" + esc(f.category.replace(/_/g, " ")) + "</span></td><td>" +
    tagBadges(f.tags) + "</td></tr>").join("");
  return head + "<table class=sub><thead><tr><th>File</th><th>full_scan</th>" +
    "<th>repolinter (header)</th><th>Category</th><th>Divergence</th></tr></thead>" +
    "<tbody>" + rows + "</tbody></table>";
}

// ---- repos table ----
let state = { search: "", filter: "ALL", sort: "repo_name", dir: 1 };

function renderRepos() {
  const rows = DATA.repos.filter(r =>
    matchFilter(r, state.filter) &&
    r.repo_name.toLowerCase().indexOf(state.search.toLowerCase()) !== -1);
  rows.sort((a, b) => {
    const va = sortVal(a, state.sort), vb = sortVal(b, state.sort);
    return va < vb ? -state.dir : va > vb ? state.dir : 0;
  });
  document.getElementById("repos-empty").style.display = rows.length ? "none" : "block";
  document.getElementById("repos-body").innerHTML = rows.map((r, i) => {
    const s = r.summary || {};
    const cells = r.error
      ? "<td class=num>&mdash;</td><td class=num>&mdash;</td><td class=num>&mdash;</td>" +
        "<td class=num>&mdash;</td><td class=num>&mdash;</td><td class=num>&mdash;</td>"
      : "<td class=num>" + esc(s.scanned_files || 0) + "</td>" +
        "<td class=num>" + esc(s.both || 0) + "</td>" +
        "<td class=num>" + esc(s.only_full_scan || 0) + "</td>" +
        "<td class=num>" + esc(s.only_repolinter || 0) + "</td>" +
        "<td class=num>" + esc(s.incompat_count || 0) + "</td>" +
        "<td class=num>" + (s.agreement_pct != null ? esc(s.agreement_pct) + "%" : "&mdash;") + "</td>";
    return "<tr class=\"row\" onclick=\"toggle(" + i + ")\">" +
      "<td class=path>" + esc(r.repo_name) + "</td>" +
      "<td>" + esc(r.license || "&mdash;") + "</td>" +
      cells +
      "<td>" + statusBadge(statusOf(r)) + "</td></tr>" +
      "<tr class=\"detail\" id=\"d" + i + "\" style=\"display:none\">" +
      "<td colspan=9>" + repoDetail(r) + "</td></tr>";
  }).join("");
}
function toggle(i) {
  const d = document.getElementById("d" + i);
  if (d) d.style.display = d.style.display === "none" ? "table-row" : "none";
}
document.getElementById("search").oninput = e => {
  state.search = e.target.value; renderRepos();
};
document.querySelectorAll(".chip").forEach(c => c.onclick = () => {
  document.querySelectorAll(".chip").forEach(x => x.classList.remove("active"));
  c.classList.add("active"); state.filter = c.dataset.f; renderRepos();
});
document.querySelectorAll("#repos-table th[data-sort]").forEach(th => th.onclick = () => {
  const key = th.dataset.sort;
  state.dir = (state.sort === key) ? -state.dir : 1;
  state.sort = key; renderRepos();
});

// ---- raw JSON download ----
document.getElementById("dl-json").onclick = () => {
  const blob = new Blob([JSON.stringify(DATA, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "multi_comparison.json";
  a.click(); URL.revokeObjectURL(a.href);
};

renderRepos();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Build a repository license INVENTORY for a whole GitHub organization.

For every repo in a given org this tool reports, in one markdown table:

  * Repository name
  * Visibility (public / private / internal)
  * The resolved root-level license
  * Which file (with a clickable blob link) or config entry decided that license
  * An "issue" note when the license could not be determined

It is an inventory, NOT a compliance gate: it does NO per-file scanning. Per repo it
runs the SAME license-resolution workflow the full scan uses
(scanner.license_resolver.resolve_license_details -- scancode over the root LICENSE
file with a config-map fallback), so it introduces no new
license-detection logic. It reuses the org-enumeration and shallow-clone helpers from
compare_tools_remote.py.

Like compare_tools_remote.py this is a read-only operator/automation entry point, not
part of the GitHub Action.

Usage:
    python full-scan/scripts/org_license_report.py <ORG>
        [--repo-type all|public|private|internal] [--max-repos N]
        [--max-repo-size-mb MB] [--include-archived] [--workers N]
        [--output FILE] [--json] [--port N] [--open] [--no-serve] [--verbose]

    Auth: GITHUB_TOKEN (if set) raises the API rate limit and is used for cloning.
    Listing private/internal repos requires a GITHUB_TOKEN with org read access;
    without one the GitHub API returns only public repos regardless of --repo-type.
    Corporate SSL: REQUESTS_CA_BUNDLE (if set) is passed to git as GIT_SSL_CAINFO and
    to the API calls.

    Output: a markdown table (also printed to stdout) plus a self-contained interactive
    HTML report, written next to each other under <full-scan>/reports/ (or the --output
    stem); --json adds a machine-readable sibling. The HTML report is then served on a
    live HTTP server (0.0.0.0:--port, default 8000) -- the SAME mechanism
    compare_tools_remote uses, so the first run holds the server until Ctrl-C and later
    runs reuse a server already serving that directory. Pass --no-serve to only write
    the files. Progress/log lines go to stderr.

Runtime dependencies: `git` and `scancode` must be on PATH (resolve_license_details
shells out to scancode).
"""

import os
import io
import sys
import json
import logging
import tempfile
import contextlib
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

import click

# This file lives in <action-repo>/full-scan/scripts/. Put full-scan/ on sys.path
# (so the reused helpers can import the `scanner` package) and this scripts/ dir
# (so we can import the sibling diagnostic modules) regardless of how we're invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# pylint: disable=wrong-import-position
import compare_tools as ct
from compare_tools_remote import (clone_repo, list_org_repos, GITHUB_HOST,
                                   DEFAULT_MAX_REPO_SIZE_MB)
from scanner.license_resolver import resolve_license_details

LOG_PREFIX = "< org license inventory >"

# The 6-column header the report is required to have. Kept as a constant so the
# renderer and the tests agree on the exact column set/order.
_TABLE_HEADER = ("| No | Repo Name | Visibility (public/internal/private) | "
                 "Root License | License Decided Based On (File Path / Link) | "
                 "Issue Details |")
_TABLE_SEP = ("|----|-----------|--------------------------------------|"
              "--------------|---------------------------------------------|"
              "---------------|")


def _log(msg: str) -> None:
    """Emit a progress/status line to stderr (stdout is reserved for the report)."""
    print(f"{LOG_PREFIX} {msg}", file=sys.stderr)


def _esc(text) -> str:
    """Escape a value for a markdown table cell: a literal '|' would end the cell."""
    return str(text).replace("|", "&#124;")


def _owner_repo(repo_name: str) -> tuple:
    """Split "owner/repo" into (owner, repo); tolerate a bare name."""
    if "/" in repo_name:
        owner, repo = repo_name.split("/", 1)
        return owner, repo
    return repo_name, repo_name


def _issue_record(repo_name: str, visibility: str, issue: str) -> dict:
    """Build a record for a repo whose license could not be established."""
    return {
        "repo_name": repo_name,
        "visibility": visibility or "unknown",
        "license": "Unknown",
        "based_on": "",
        "issue": issue,
        "status": "issue",
        # Structured "based on" fields (for the HTML/JSON renderers); empty here.
        "source": "none",
        "license_file": "",
        "license_url": "",
        "config_project": "",
    }


def build_repo_record(repo_name: str, visibility: str, resolution,
                      default_branch: str, host: str = GITHUB_HOST) -> dict:
    """
    Map a LicenseResolution into a report row.

    A license_file/config resolution is "detected"; the based-on cell links the file
    (or names the config entry). A "none" resolution is a detection issue, its message
    keyed to the same three no-baseline cases resolve_license_details distinguishes:
    a present-but-undetected file, a present-but-empty file, or no file at all.

    Args:
        repo_name (str): "owner/repo".
        visibility (str): "public" / "private" / "internal" (or "unknown").
        resolution: The LicenseResolution from resolve_license_details.
        default_branch (str): Branch to point the blob link at.
        host (str): Git host for the blob link (default github.com).

    Returns:
        dict: {repo_name, visibility, license, based_on, issue, status}.
    """
    if resolution.source == "license_file" and resolution.license:
        owner, repo = _owner_repo(repo_name)
        name = resolution.license_file
        url = f"https://{host}/{owner}/{repo}/blob/{default_branch}/{name}"
        # More than one root license file is worth surfacing (the resolver used the
        # first by priority); reported as a note, the repo still counts as detected.
        issue = (f"{resolution.num_license_files} license files present; used {name}"
                 if resolution.num_license_files > 1 else "")
        return {
            "repo_name": repo_name,
            "visibility": visibility or "unknown",
            "license": resolution.license,
            "based_on": f"[{_esc(name)}]({url})",
            "issue": issue,
            "status": "detected",
            "source": "license_file",
            "license_file": name,
            "license_url": url,
            "config_project": "",
        }
    if resolution.source == "config" and resolution.license:
        return {
            "repo_name": repo_name,
            "visibility": visibility or "unknown",
            "license": resolution.license,
            "based_on": f"scanner/config.py entry ({resolution.config_project})",
            "issue": "",
            "status": "detected",
            "source": "config",
            "license_file": "",
            "license_url": "",
            "config_project": resolution.config_project,
        }
    # source == "none" (license is None): a detection issue. Distinguish the cases.
    if resolution.license_file:
        issue = "License present but not conclusively detected"
    elif resolution.empty_license_files:
        issue = "Root-level license file present but empty"
    else:
        issue = "No license file found"
    return _issue_record(repo_name, visibility, issue)


def render_markdown(org: str, records: list, skipped: list,
                    host: str = GITHUB_HOST) -> str:
    """
    Render the inventory as a markdown table plus a summary section.

    Args:
        org (str): The organization name (report header).
        records (list): Per-repo dicts from build_repo_record / _issue_record.
        skipped (list): Repos skipped during enumeration (fork/archived/size).
        host (str): Git host (unused in the body; kept for signature symmetry).

    Returns:
        str: The full markdown document (trailing newline included).
    """
    lines = [f"Org Name: {org}", "", _TABLE_HEADER, _TABLE_SEP]
    for i, rec in enumerate(records, 1):
        lines.append(
            f"| {i} | {_esc(rec['repo_name'])} | {_esc(rec['visibility'])} | "
            f"{_esc(rec['license'])} | {rec['based_on']} | {_esc(rec['issue'])} |")

    detected = sum(1 for r in records if r["status"] == "detected")
    issues = len(records) - detected
    lines += [
        "",
        "## Summary",
        f"- Total repositories scanned: {len(records)}",
        f"- Successfully detected: {detected}",
        f"- Detection issues: {issues}",
        f"- Skipped (size cap / fork / archived): {len(skipped)}",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# HTML report (self-contained, served like compare_tools_remote's report)
# --------------------------------------------------------------------------- #

# A self-contained interactive report: the data is embedded as JSON and rendered
# client-side (filter by text/visibility, "issues only", click a header to sort).
# No external assets, so it works when served over a plain HTTP file server.
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Org License Inventory</title>
<style>
  :root { --fg:#1b1f24; --muted:#57606a; --line:#d0d7de; --bg:#ffffff;
          --ok:#1a7f37; --okbg:#dafbe1; --warn:#9a6700; --warnbg:#fff8c5;
          --accent:#0969da; }
  * { box-sizing: border-box; }
  body { margin:0; color:var(--fg); background:#f6f8fa;
         font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  header { background:var(--bg); border-bottom:1px solid var(--line); padding:18px 24px; }
  header h1 { margin:0 0 4px; font-size:20px; }
  header .meta { color:var(--muted); font-size:13px; }
  main { padding:20px 24px 48px; }
  .cards { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:18px; }
  .card { background:var(--bg); border:1px solid var(--line); border-radius:8px;
          padding:12px 16px; min-width:120px; }
  .card .n { font-size:24px; font-weight:600; }
  .card .l { color:var(--muted); font-size:12px; text-transform:uppercase;
             letter-spacing:.03em; }
  .card.ok .n { color:var(--ok); } .card.warn .n { color:var(--warn); }
  .controls { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
  .controls input[type=text], .controls select {
      padding:7px 10px; border:1px solid var(--line); border-radius:6px; font-size:13px; }
  .controls input[type=text] { flex:1; min-width:220px; }
  .controls label { color:var(--muted); font-size:13px; user-select:none; }
  table { width:100%; border-collapse:collapse; background:var(--bg);
          border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  th, td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line);
           vertical-align:top; }
  th { background:#f6f8fa; cursor:pointer; white-space:nowrap; position:sticky; top:0; }
  th .arr { color:var(--muted); font-size:11px; }
  tr:last-child td { border-bottom:none; }
  tr.issue { background:#fff8f8; }
  tr.issue td.issue-cell { color:#cf222e; }
  td.num { color:var(--muted); text-align:right; width:44px; }
  code { background:#eff1f3; padding:1px 5px; border-radius:5px; font-size:12.5px; }
  .badge { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px;
           border:1px solid var(--line); }
  .badge.public { background:var(--okbg); color:var(--ok); border-color:#a6e0b5; }
  .badge.private { background:#ffebe9; color:#cf222e; border-color:#ffcecb; }
  .badge.internal { background:var(--warnbg); color:var(--warn); border-color:#efd780; }
  a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
  details { margin-top:18px; } summary { cursor:pointer; color:var(--muted); }
  footer { color:var(--muted); font-size:12px; margin-top:24px; }
</style>
</head>
<body>
<header>
  <h1 id="title">Org License Inventory</h1>
  <div class="meta" id="meta"></div>
</header>
<main>
  <section class="cards" id="cards"></section>
  <div class="controls">
    <input type="text" id="q" placeholder="Filter by repo, license, or issue...">
    <select id="vis">
      <option value="">All visibilities</option>
      <option value="public">public</option>
      <option value="private">private</option>
      <option value="internal">internal</option>
    </select>
    <label><input type="checkbox" id="issuesOnly"> Issues only</label>
    <span class="meta" id="shown"></span>
  </div>
  <table id="tbl">
    <thead><tr>
      <th data-k="_n">#</th>
      <th data-k="repo_name">Repo Name <span class="arr"></span></th>
      <th data-k="visibility">Visibility <span class="arr"></span></th>
      <th data-k="license">Root License <span class="arr"></span></th>
      <th data-k="based">License Decided Based On</th>
      <th data-k="issue">Issue Details <span class="arr"></span></th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <details id="skipped">
    <summary></summary>
    <table><tbody id="skiprows"></tbody></table>
  </details>
  <footer id="footer"></footer>
</main>
<script>
const DATA = @@DATA@@;
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let sortKey = "repo_name", sortAsc = true;

function basedCell(r) {
  if (r.license_url) return '<a href="' + esc(r.license_url) + '" target="_blank" rel="noopener">' +
      esc(r.license_file) + '</a>';
  if (r.config_project)
    return 'scanner/config.py entry (<code>' + esc(r.config_project) + '</code>)';
  return '';
}

function filtered() {
  const q = document.getElementById("q").value.trim().toLowerCase();
  const vis = document.getElementById("vis").value;
  const issuesOnly = document.getElementById("issuesOnly").checked;
  let rows = DATA.records.filter(r => {
    if (vis && r.visibility !== vis) return false;
    if (issuesOnly && r.status === "detected") return false;
    if (!q) return true;
    return (r.repo_name + " " + r.license + " " + r.issue).toLowerCase().includes(q);
  });
  rows.sort((a, b) => {
    const x = String(a[sortKey] || "").toLowerCase();
    const y = String(b[sortKey] || "").toLowerCase();
    return (x < y ? -1 : x > y ? 1 : 0) * (sortAsc ? 1 : -1);
  });
  return rows;
}

function render() {
  const t = DATA.totals;
  document.getElementById("cards").innerHTML =
    card(t.total, "Total repositories") + card(t.detected, "Detected", "ok") +
    card(t.issues, "Detection issues", "warn") + card(t.skipped, "Skipped");
  const rows = filtered();
  document.getElementById("rows").innerHTML = rows.map((r, i) =>
    '<tr class="' + (r.status === "detected" ? "" : "issue") + '">' +
    '<td class="num">' + (i + 1) + '</td>' +
    '<td><code>' + esc(r.repo_name) + '</code></td>' +
    '<td><span class="badge ' + esc(r.visibility) + '">' + esc(r.visibility) + '</span></td>' +
    '<td>' + esc(r.license) + '</td>' +
    '<td>' + basedCell(r) + '</td>' +
    '<td class="issue-cell">' + esc(r.issue) + '</td></tr>').join("");
  document.getElementById("shown").textContent =
    "showing " + rows.length + " of " + DATA.records.length;
}

function card(n, label, cls) {
  return '<div class="card ' + (cls || "") + '"><div class="n">' + n +
         '</div><div class="l">' + label + '</div></div>';
}

document.getElementById("title").textContent = "Org License Inventory — " + DATA.meta.org;
document.getElementById("meta").textContent =
  "type=" + DATA.meta.repo_type + " · host " + DATA.meta.host +
  " · generated " + DATA.meta.generated_at;
document.querySelectorAll("th[data-k]").forEach(th => th.addEventListener("click", () => {
  const k = th.getAttribute("data-k");
  if (k === "_n" || k === "based") return;
  if (sortKey === k) sortAsc = !sortAsc; else { sortKey = k; sortAsc = true; }
  render();
}));
["q", "vis", "issuesOnly"].forEach(id =>
  document.getElementById(id).addEventListener("input", render));
const sk = DATA.skipped || [];
document.querySelector("#skipped summary").textContent =
  "Skipped during enumeration (" + sk.length + ")";
document.getElementById("skiprows").innerHTML = sk.map(s =>
  '<tr><td><code>' + esc(s.repo_name) + '</code></td><td>' + esc(s.reason) + '</td></tr>').join("");
document.getElementById("footer").textContent =
  "Generated by org_license_report.py — license resolution only (no per-file scan).";
render();
</script>
</body>
</html>
"""


def build_report_data(org: str, records: list, skipped: list, host: str,
                       repo_type: str, generated_at: str) -> dict:
    """Assemble the JSON payload embedded in the HTML report / written by --json."""
    detected = sum(1 for r in records if r["status"] == "detected")
    return {
        "meta": {"org": org, "host": host, "repo_type": repo_type,
                 "generated_at": generated_at},
        "totals": {"total": len(records), "detected": detected,
                   "issues": len(records) - detected, "skipped": len(skipped)},
        "records": records,
        "skipped": skipped,
    }


def render_html(data: dict) -> str:
    """Render the self-contained interactive HTML report from build_report_data."""
    # Escape "</" so an embedded value can never close the <script> block early
    # (the same guard compare_tools.render_html uses).
    data_json = json.dumps(data).replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("@@DATA@@", data_json)


def resolve_one_repo(task: dict) -> dict:
    """
    Clone one repo and resolve its root license -- the only I/O step (worker).

    Runs in its own process when --workers > 1: resolve_license_details does a
    process-global os.chdir and reads cwd, so a separate process per in-flight repo
    keeps cwd isolated (the same reason compare_tools_remote uses a ProcessPool). A
    clone or detection failure NEVER raises for expected errors -- it returns an
    "issue" record so the whole-org run continues.

    Args:
        task (dict): {repo_name, clone_url, visibility, default_branch, host, token,
              ca_bundle, ref, verbose}.

    Returns:
        dict: A report record (see build_repo_record / _issue_record).
    """
    repo_name = task["repo_name"]
    visibility = task.get("visibility") or "unknown"
    default_branch = task.get("default_branch") or "HEAD"
    host = task.get("host") or GITHUB_HOST
    with tempfile.TemporaryDirectory(prefix="orginv-") as tmp:
        dest = os.path.join(tmp, "repo")
        ok, clone_err = clone_repo(task["clone_url"], dest, task["token"],
                                   task["ca_bundle"], task.get("ref"))
        if not ok:
            return _issue_record(repo_name, visibility, f"Clone failed: {clone_err}")

        prev_cwd = os.getcwd()
        captured = io.StringIO()
        try:
            os.chdir(dest)
            with contextlib.redirect_stdout(captured):
                resolution = resolve_license_details(repo_name)
        except Exception as exc:  # pylint: disable=broad-except
            return _issue_record(repo_name, visibility,
                                 f"License detection error: {exc}")
        finally:
            os.chdir(prev_cwd)

    if task.get("verbose") and captured.getvalue().strip():
        print(captured.getvalue().strip(), file=sys.stderr)
    return build_repo_record(repo_name, visibility, resolution, default_branch, host)


def _log_progress(done: int, total: int, rec: dict) -> None:
    """Print a one-line progress update for a finished repo."""
    detail = rec["license"] if rec["status"] == "detected" else rec["issue"]
    _log(f"[{done}/{total}] {rec['repo_name']} ({rec['visibility']}): {detail}")


@click.command()
@click.argument("org")
@click.option("--repo-type",
              type=click.Choice(["all", "public", "private", "internal"]),
              default="all", show_default=True,
              help="GitHub `type=` filter. 'all' (default) covers public + private + "
                   "internal; anything but 'public' needs a GITHUB_TOKEN with org "
                   "read access.")
@click.option("--max-repos", default=0, type=int,
              help="Cap the number of repos processed (0 = no cap). scancode is slow, "
                   "so a full-org run can take a while.")
@click.option("--max-repo-size-mb", default=DEFAULT_MAX_REPO_SIZE_MB, type=int,
              show_default=True,
              help="Skip enumerated repos larger than this many MB (0 = no cap). "
                   "Excludes giant mirrors like the kernel trees.")
@click.option("--include-archived", is_flag=True, default=False, show_default=True,
              help="Include archived repos (excluded by default).")
@click.option("--workers", default=4, type=int, show_default=True,
              help="Parallel worker PROCESSES (resolve_license_details is not "
                   "thread-safe). Use 1 for sequential/debug.")
@click.option("--output", type=click.Path(dir_okay=False, resolve_path=True),
              default=None,
              help="Markdown output path. Default: "
                   "<full-scan>/reports/org-license_<org>_<YYYYMMDD-HHMMSS>.md.")
@click.option("--json", "emit_json", is_flag=True, default=False,
              help="Also write a machine-readable JSON sibling next to the markdown.")
@click.option("--port", default=8000, type=int, show_default=True,
              help="Port to serve the HTML report on (0.0.0.0). The first run holds "
                   "the server until Ctrl-C; later runs reuse a server already "
                   "serving the reports dir on this port.")
@click.option("--open", "open_browser", is_flag=True, default=False,
              help="Open the served report in a browser when the server starts.")
@click.option("--no-serve", is_flag=True, default=False,
              help="Write the report files and exit without starting the HTTP server "
                   "(for cron/CI). By default the HTML report is served on --port.")
@click.option("--verbose", is_flag=True, default=False,
              help="Echo suppressed resolve_license chatter to stderr.")
def main(org: str, repo_type: str, max_repos: int, max_repo_size_mb: int,
         include_archived: bool, workers: int, output: str, emit_json: bool,
         port: int, open_browser: bool, no_serve: bool, verbose: bool) -> None:
    """
    Inventory every repository in ORG: resolved license, source, and any issue.

    Writes a markdown table and a self-contained interactive HTML report (plus an
    optional JSON), then serves the HTML on a live HTTP server (0.0.0.0:--port) so it
    can be viewed/shared -- the same serving mechanism compare_tools_remote uses. Use
    --no-serve to just write the files and exit.

    Exits 0 whenever a report was produced; exits 2 only when NO repo could be
    enumerated (bad org name, network, or a private/internal org with no token).
    """
    logging.basicConfig(level=logging.WARNING)

    token = os.environ.get("GITHUB_TOKEN") or ""
    ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or ""

    if repo_type != "public" and not token:
        _log(f"NOTE: --repo-type={repo_type} needs a GITHUB_TOKEN with org read "
             f"access to see private/internal repos; without one only public repos "
             f"are returned.")

    _log(f"Enumerating '{org}' repositories (type={repo_type})...")
    skipped = []
    repos = list_org_repos(org, token, ca_bundle, include_archived,
                           max_repo_size_mb, repo_type=repo_type,
                           skipped_out=skipped)
    if max_repos and len(repos) > max_repos:
        _log(f"NOTE: capping {len(repos)} repos to --max-repos={max_repos} "
             f"({len(repos) - max_repos} dropped).")
        repos = repos[:max_repos]
    if not repos:
        _log("ERROR: no repositories to inventory. Check the org name, network, and "
             "GITHUB_TOKEN (private/internal repos need an org-scoped token).")
        sys.exit(2)

    _log(f"Resolving licenses for {len(repos)} repo(s) with {workers} worker(s). "
         f"scancode is slow -- this can take a while.")

    tasks = [{
        "repo_name": r["repo_name"], "clone_url": r["clone_url"],
        "visibility": r.get("visibility"), "default_branch": r.get("default_branch"),
        "host": GITHUB_HOST, "token": token, "ca_bundle": ca_bundle,
        "ref": None, "verbose": verbose,
    } for r in repos]

    records = []
    total = len(tasks)
    done = 0
    if workers <= 1:
        for task in tasks:
            try:
                rec = resolve_one_repo(task)
            except Exception as exc:  # pylint: disable=broad-except
                rec = _issue_record(task["repo_name"], task.get("visibility"),
                                    f"Worker error: {exc}")
            done += 1
            _log_progress(done, total, rec)
            records.append(rec)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(resolve_one_repo, t): t for t in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    rec = future.result()
                except Exception as exc:  # pylint: disable=broad-except
                    rec = _issue_record(task["repo_name"], task.get("visibility"),
                                        f"Worker error: {exc}")
                done += 1
                _log_progress(done, total, rec)
                records.append(rec)

    # Stable, scannable order: alphabetical by repo name.
    records.sort(key=lambda r: r["repo_name"].lower())

    now = datetime.now()
    data = build_report_data(org, records, skipped, GITHUB_HOST, repo_type,
                             now.strftime("%Y-%m-%d %H:%M:%S"))
    markdown = render_markdown(org, records, skipped, GITHUB_HOST)
    html = render_html(data)

    # --output names the markdown file; the .html/.json siblings share its stem.
    # Default: reports/org-license_<org>_<stamp>.{md,html,json}.
    if output:
        md_path = output
        stem = os.path.splitext(output)[0]
        os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    else:
        stamp = now.strftime("%Y%m%d-%H%M%S")
        os.makedirs(ct.REPORTS_DIR, exist_ok=True)
        stem = os.path.join(ct.REPORTS_DIR,
                            f"org-license_{org.replace('/', '_')}_{stamp}")
        md_path = stem + ".md"
    html_path = stem + ".html"
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(markdown)
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(html)

    # The markdown report also goes to stdout; all progress/logs are on stderr.
    print(markdown)

    tot = data["totals"]
    _log(f"Done. {tot['total']} repo(s): {tot['detected']} detected, "
         f"{tot['issues']} with issues, {tot['skipped']} skipped.")
    _log(f"Markdown: {md_path}")
    _log(f"HTML:     {html_path}")
    if emit_json:
        json_path = stem + ".json"
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        _log(f"JSON:     {json_path}")

    if no_serve:
        sys.exit(0)
    # Serve the HTML on a live server (blocks until Ctrl-C), reusing an existing
    # server on this port if one already serves the reports dir -- the same mechanism
    # as compare_tools_remote.
    ct._serve_report(html_path, port, open_browser)  # pylint: disable=protected-access
    sys.exit(0)


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter

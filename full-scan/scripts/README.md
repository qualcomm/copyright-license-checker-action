# scripts/

Local, read-only **diagnostic** tools that compare this repo's full-repo
license/copyright scanner (`full_scan.py`) against **repolinter** and render the results
as a self-contained interactive HTML report.

These are NOT part of the GitHub Action and are NOT CI gates. They exist to build the
evidence — on real repos — for the roadmap goal of eventually retiring repolinter's
license/copyright rules in favour of `full_scan`. Nothing here changes what the Action
blocks or warns on.

Both tools reuse the real scanner in-process (they `import` `main.py` and `scanner/`),
so there is no output-parsing drift. Run them from the **repo root** as
`python scripts/<tool>.py ...` (a `sys.path` bootstrap lets them import the root-level
`main`/`scanner` packages from inside `scripts/`).

This directory also holds `jira_scan.py`, which is **not** a repolinter comparison — it is an
operator/automation runner that scans the repo referenced by a Jira ticket and posts the
findings back onto that ticket. It reuses the same in-process scan.

## Tools

| File | Scope | Fetches repos? |
|---|---|---|
| `compare_tools.py` | One repo you already have on disk | No — you point it at a local checkout (`--repo-path`) |
| `compare_tools_remote.py` | Many repos across GitHub org(s) | Yes — enumerates orgs via the GitHub API and shallow-clones each into a temp dir (auto-cleaned) |
| `jira_scan.py` | One repo referenced by a Jira ticket | Yes — reads the repo URL from a ticket field and shallow-clones it (auto-cleaned) |

Both write their report to `<repo-root>/reports/` (created on demand) and then **serve it
over HTTP on `0.0.0.0:8000`** (change with `--port`), so it can be opened from another machine.

The server serves the whole `reports/` directory, so a single server covers every report you
generate. The **first** run holds the server (Ctrl-C to stop). A **later** run whose `--port` is
already serving that directory detects it, prints the URL for the new report, and exits
immediately — it does **not** start a second server, so repeated runs don't pile up listeners on
new ports. Every report is reachable at `http://localhost:8000/<report-file>.html`. (If the port
is held by something unrelated, the tool says so and leaves the report on disk to open directly or
serve with a different `--port`.)

## Prerequisites

- **Python 3.12** for the full-scan path (`click` is the only extra import for the
  comparison tools; already in `requirements.txt`).
- `jira_scan.py` additionally needs the public **`jira`** library:
  `pip install -r full-scan/scripts/requirements.txt` (public PyPI only — no internal
  Qualcomm package).
- On `PATH`:
  - `scancode` (from `scancode-toolkit`) — the scanner shells out to it; the first run
    builds its license index and is slow.
  - `docker` — repolinter runs as the container `ghcr.io/todogroup/repolinter:v0.11.2`.
  - `git` — `compare_tools_remote.py` and `jira_scan.py` clone with it (and `full_scan`'s
    `RepoScan` uses `git ls-files`).

Tip: `docker pull ghcr.io/todogroup/repolinter:v0.11.2` once up front so parallel
workers don't race the first pull.

---

## `compare_tools.py` — single local repo

Runs both tools over one local checkout and renders where they agree/diverge per file
(the centrepiece is the license/copyright overlap: header-presence vs license
compatibility).

```
python scripts/compare_tools.py [owner/repo] [--repo-path PATH] [--include-untracked]
                                [--ruleset-url URL] [--repolinter-json FILE]
                                [--output FILE] [--open] [--port N] [--verbose]
```

- `[owner/repo]` (positional, **optional**) — used to resolve the repo's expected
  license (`main.get_license`) and to build report links. When omitted it is derived
  from the `--repo-path` checkout's `origin` remote, so you usually don't need to pass it.
- `--repo-path PATH` — the working tree to scan (default `.`). Resolved to an absolute
  path (required for the docker bind mount).
- `--include-untracked` — also scan untracked-but-not-ignored files. repolinter always
  scans the whole working tree, so this is usually needed for parity with a dirty tree.
- `--include-licenseignore` — also scan files the repo's `.licenseignore` excludes.
  full_scan honors `.licenseignore`; repolinter does not, so files it excludes otherwise
  show up as repolinter-only (tagged `excluded by .licenseignore`). Flip this on for parity
  or to audit vendored/upstream paths.
- `--ruleset-url URL` — repolinter ruleset (default: the Qualcomm `repolint-qcom.json`).
- `--repolinter-json FILE` — load a pre-saved repolinter `--format json` result instead
  of invoking docker (offline / repeatable).
- `--output FILE` — report path. Default:
  `<repo-root>/reports/<project>_<YYYYMMDD-HHMMSS>.html`.
- `--open` — open the served report in a browser when the server starts.
- `--port N` — serve port (**default 8000**). The first run holds the server; a later run whose
  port is already serving the reports dir reuses it (prints the URL and exits).
- `--verbose` — echo the suppressed `get_license`/scan chatter to stderr.

Example:

```
python scripts/compare_tools.py qualcomm/qre \
    --repo-path /path/to/qre --include-untracked --port 9000
```

---

## `compare_tools_remote.py` — many repos across GitHub org(s)

Enumerates repos in one or more **public github.com** orgs (or an explicit `--repos`
set), shallow-clones each into a throwaway temp dir, runs both tools, and aggregates
everything into one HTML report (with a `.json` sibling for auditing). The report has a
sortable/filterable repos table; click a repo row to expand its per-file comparison.

```
python scripts/compare_tools_remote.py [--orgs ORG ...] [--repos owner/repo ...]
                                       [--max-repos N] [--max-repo-size-mb MB]
                                       [--include-archived] [--workers N]
                                       [--ruleset-url URL] [--output FILE]
                                       [--open] [--port N] [--verbose]
```

- `--orgs ORG` (repeatable) — orgs to enumerate. Default:
  `qualcomm qualcomm-linux qualcomm-qrb-ros audioreach quic`. Ignored when `--repos` is
  given.
- `--repos owner/repo` (repeatable) — scan exactly these repos and skip API enumeration.
- `--max-repos N` — cap repos processed (`0` = no cap). scancode is slow, so a full-org
  run can take a long time; any truncation is logged (never silent).
- `--max-repo-size-mb MB` — skip enumerated repos larger than this (**default 500**;
  `0` disables). Excludes giant mirrors like the `qualcomm-linux` kernel trees
  (2.5–3.8 GB) that are impractical to clone + scancode. Explicit `--repos` are never
  size-filtered, so a big repo can still be forced that way. Skips are logged (never silent).
- `--include-archived` — include archived repos (excluded by default; forks are always
  skipped).
- `--include-licenseignore` — also scan files the repo's `.licenseignore` excludes, for
  parity with repolinter (which ignores `.licenseignore`). Applies to every repo in the run.
- `--workers N` — parallel worker **processes** (**default 4**). Processes, not threads:
  `full_scan` uses a process-global `os.chdir`, so it is not thread-safe. Each worker
  fully handles one repo (clone → scan → repolinter) with an isolated cwd. Use
  `--workers 1` for sequential/debug runs. Keep this modest — several concurrent scancode
  runs are CPU/memory heavy.
- `--ruleset-url URL` — repolinter ruleset (default: Qualcomm `repolint-qcom.json`).
- `--output FILE` — aggregate HTML path. Default:
  `<repo-root>/reports/multi_<YYYYMMDD-HHMMSS>.html` (a `.json` is written alongside).
- `--open`, `--port N` (**default 8000**), `--verbose` — same as above.

### Authentication / corporate SSL (environment variables)

- `GITHUB_TOKEN` — if set, raises the GitHub API rate limit (60/hr → 5000/hr) and is used
  for cloning. Public repos work unauthenticated, but you may hit rate limits.
- `REQUESTS_CA_BUNDLE` — if set, its CA bundle is used for API HTTPS and passed to git as
  `GIT_SSL_CAINFO` (for networks with SSL interception). Tokens are scrubbed from any
  clone error text so they never land in the report/JSON.

Examples:

```
# Quick smoke test on two named repos
python scripts/compare_tools_remote.py --repos qualcomm/copyright-license-checker-action \
    --repos qualcomm/qre --workers 2 --port 9000

# Bounded org run (validate enumeration without committing to hundreds of repos)
python scripts/compare_tools_remote.py --orgs qualcomm --max-repos 5
```

---

## `jira_scan.py` — scan the repo referenced by a Jira ticket

Fetches a repository URL from a Jira ticket field, shallow-clones the repo, runs the
full-repo scan (the same in-process path as the tools above), and posts the
findings/warnings — or a validation/clone/scan error — back onto the ticket as a single
comment. Unlike the comparison tools, it does **not** run repolinter and produces no HTML
report; it is a runner meant for an operator or an automation.

```
python scripts/jira_scan.py <ISSUE-KEY> [--url-field NAME_OR_ID] [--env-file PATH]
                            [--jira-url URL] [--ref BRANCH] [--include-untracked]
                            [--include-licenseignore] [--comment-limit N]
                            [--comment-visibility-group GROUP]
                            [--dry-run] [--fail-on-findings]
```

- `<ISSUE-KEY>` (positional, **required**) — the Jira issue, e.g. `OSSOPS-29471`.
- `--url-field NAME_OR_ID` — the ticket field holding the repo URL. Default `URL` (resolved
  to its `customfield_*` id by name, case-insensitive); pass an explicit `customfield_*` id
  to skip name resolution.
- `--env-file PATH` — a `.env` file to load (default `.env`; optional). Real environment
  variables always win over the file. See `.env.example`.
- `--jira-url URL` — overrides `JIRA_BASE_URL`.
- `--ref BRANCH` — branch/tag to clone (default: the repo's default branch).
- `--include-untracked` / `--include-licenseignore` — passed through to the scan (same
  meaning as in `compare_tools.py`).
- `--comment-limit N` — cap the comment length. `0` (default) uses `MAX_COMMENT_LENGTH`
  from the environment, else **16384** (the limit the OSSOPS Jira enforces, matching
  qnaro). The summary counts are always kept; per-file detail is truncated with an explicit
  "N more file(s) omitted" note if it would exceed the cap.
- `--comment-visibility-group GROUP` — restrict the posted comment so only members of the
  named Jira group can see it (e.g. `developers`), via the comment's visibility field.
  Applies to **every** comment the run posts (results *and* error reports), so scan output
  isn't exposed to ticket reporters/watchers outside the group. Defaults to
  `JIRA_COMMENT_VISIBILITY_GROUP` from the environment, else the comment is public. (The
  posting account must be allowed to set that group's visibility on the Jira instance.)
- `--dry-run` — do everything except post; print the comment to stdout. Use this first.
- `--fail-on-findings` — exit non-zero when blocking findings exist (default: report-only,
  exit 0).

### Authentication / config (environment variables, typically via `.env`)

`jira_scan.py` uses **HTTP Basic auth** — the same mechanism as `common_utils/djira.py`,
but it depends only on the public `jira` library, not on that module. Credentials can come
from `.env`, the environment, or your **`~/.jira-creds`** file (the file djira reads: line 1
= username, line 2 = password). If you already use djira, it works with no extra setup. The
env-var names match qnaro's, so the same `.env` values can be reused.

- `JIRA_API_SERVER` — Jira REST base. Defaults to `https://jira-dc-tools.qualcomm.com/jira`.
  It must include any context path (the trailing `/jira`) because REST calls go to
  `<JIRA_API_SERVER>/rest/api/2/...`; omitting it yields a 404. (`JIRA_BASE_URL` is an alias.)
- `JIRA_USER` / `JIRA_PASSWORD` — Jira username and password (or token). If unset, they fall
  back to the two lines of `~/.jira-creds` (override the fallback with `--creds-file`).
- `MAX_COMMENT_LENGTH` — comment-body cap (default 16384); see `--comment-limit`.
- `JIRA_COMMENT_VISIBILITY_GROUP` — restrict the posted comment to this Jira group (e.g.
  `developers`); default public. See `--comment-visibility-group`.
- `GITHUB_TOKEN` — clone auth for private/Enterprise repos (optional for public github.com).
- `REQUESTS_CA_BUNDLE` — CA bundle for the Jira API HTTPS and for git (`GIT_SSL_CAINFO`).
  Or pass `--insecure` to skip TLS verification entirely (as djira does) when no CA bundle
  is configured. The password is scrubbed from any error text.

Example:

```
# Validate end to end without posting
python scripts/jira_scan.py OSSOPS-29471 --dry-run

# Post the results comment
python scripts/jira_scan.py OSSOPS-29471
```

---

## Exit codes

The comparison tools are diagnostics, not gates:

- **0** — a report was produced (disagreement between the tools is the point).
- **2** — an operational failure prevented the comparison: for `compare_tools.py`,
  repolinter unavailable / target not a git repo; for `compare_tools_remote.py`, no repos
  could be enumerated and none were given via `--repos`. Per-repo clone/repolinter
  failures in the remote tool are captured in the report and do not fail the whole run.

`jira_scan.py` exit codes:

- **0** — the scan ran and its comment was posted (or printed, under `--dry-run`); with
  `--fail-on-findings`, also requires no blocking findings.
- **1** — a validation/clone/scan problem (missing/unparseable URL, clone or scan failure)
  was reported onto the ticket, or `--fail-on-findings` and blocking findings were present.
- **2** — Jira config missing, or the ticket/field could not be read (nothing could be
  posted).
- **3** — the scan ran but posting the comment to Jira failed.

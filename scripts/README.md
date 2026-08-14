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

## Tools

| File | Scope | Fetches repos? |
|---|---|---|
| `compare_tools.py` | One repo you already have on disk | No — you point it at a local checkout (`--repo-path`) |
| `compare_tools_remote.py` | Many repos across GitHub org(s) | Yes — enumerates orgs via the GitHub API and shallow-clones each into a temp dir (auto-cleaned) |

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

- **Python 3.12** for the full-scan path (`click` is the only extra import; already in
  `requirements.txt`).
- On `PATH`:
  - `scancode` (from `scancode-toolkit`) — the scanner shells out to it; the first run
    builds its license index and is slow.
  - `docker` — repolinter runs as the container `ghcr.io/todogroup/repolinter:v0.11.2`.
  - `git` — `compare_tools_remote.py` clones with it (and `full_scan`'s `RepoScan` uses
    `git ls-files`).

Tip: `docker pull ghcr.io/todogroup/repolinter:v0.11.2` once up front so parallel
workers don't race the first pull.

---

## `compare_tools.py` — single local repo

Runs both tools over one local checkout and renders where they agree/diverge per file
(the centrepiece is the license/copyright overlap: header-presence vs license
compatibility).

```
python scripts/compare_tools.py <owner/repo> [--repo-path PATH] [--include-untracked]
                                [--ruleset-url URL] [--repolinter-json FILE]
                                [--output FILE] [--open] [--port N] [--verbose]
```

- `<owner/repo>` (positional, required) — used only to resolve the repo's expected
  license (`main.get_license`).
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
                                       [--max-repos N] [--include-archived]
                                       [--workers N] [--ruleset-url URL]
                                       [--output FILE] [--open] [--port N] [--verbose]
```

- `--orgs ORG` (repeatable) — orgs to enumerate. Default:
  `qualcomm qualcomm-linux qualcomm-qrb-ros audioreach quic`. Ignored when `--repos` is
  given.
- `--repos owner/repo` (repeatable) — scan exactly these repos and skip API enumeration.
- `--max-repos N` — cap repos processed (`0` = no cap). scancode is slow, so a full-org
  run can take a long time; any truncation is logged (never silent).
- `--include-archived` — include archived repos (excluded by default; forks and huge
  kernel mirrors are always skipped).
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

## Exit codes

Both tools are diagnostics, not gates:

- **0** — a report was produced (disagreement between the tools is the point).
- **2** — an operational failure prevented the comparison: for `compare_tools.py`,
  repolinter unavailable / target not a git repo; for `compare_tools_remote.py`, no repos
  could be enumerated and none were given via `--repos`. Per-repo clone/repolinter
  failures in the remote tool are captured in the report and do not fail the whole run.

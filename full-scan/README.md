# full-scan

A **whole-repository** copyright and license compliance scanner, packaged as a GitHub
composite Action. It complements the pull-request **patch scan** (at the repo root): where the
patch scan only inspects a PR's diff, `full-scan` walks **every source file** in a repository
so it also catches legacy files committed before the checker was enabled.

This directory is **self-contained** — it imports nothing from the patch scan. It also has its
own **file-handling rules** (a wider source-extension list, a relaxed tier for build files, and
BitBake files excluded outright) and its own findings, so what blocks here is not identical to
the patch scan: see [COMPLIANCE.md](COMPLIANCE.md).

## Run it

As a GitHub Action (composite; wired in behind the shared preflight orchestrator):

```yaml
- uses: qualcomm/copyright-license-checker-action/full-scan@<ref>
  with:
    repo_name: ${{ github.repository }}
    fail_on_findings: 'false'   # 'true' fails the build on blocking findings
```

Locally (from the repo root):

```bash
pip install -r full-scan/requirements.txt          # scancode must end up on PATH
python full-scan/full_scan.py <owner>/<repo> <fail_on_findings> --repo-path <checkout>
# e.g. python full-scan/full_scan.py qualcomm/some-repo false --repo-path .
```

`fail_on_findings` is optional (defaults to `true` on the CLI; the Action defaults it to
`false` / report-only). `--include-untracked` and `--include-licenseignore` widen the scan.

## Layout

```
full-scan/
├── action.yml            # composite Action (setup-python 3.12 -> pip install -> full_scan.py)
├── COMPLIANCE.md         # what blocks vs. warns here (deltas on top of ../COMPLIANCE.md)
├── full_scan.py          # entry point: resolve license -> RepoScan -> FullScanner -> report
├── requirements.txt      # runtime deps (scancode-toolkit, pathspec, click)
├── scanner/              # self-contained scan engine
│   ├── full_repo.py          # RepoScan: enumerate files (git ls-files + extension/.licenseignore filters)
│   ├── full_scanner.py       # FullScanner: run scancode, classify licenses/copyright
│   ├── license_resolver.py   # resolve the repo's license (hardened vs scancode misdetection)
│   ├── licenses.py           # PERMISSIVE / COPYLEFT allow-lists
│   ├── config.py             # per-project license overrides
│   └── ignore_config.py      # .licenseignore matcher
├── scripts/              # local diagnostics (NOT shipped): compare full-scan vs repolinter
├── docs/                 # architecture & flow (docs/architecture.md)
└── tests/                # pytest suite
```

## Develop

```bash
pip install pytest          # dev-only; not a runtime dependency
python -m pytest full-scan/tests/ -q
```

See [docs/architecture.md](docs/architecture.md) for the end-to-end architecture,
[COMPLIANCE.md](COMPLIANCE.md) for what blocks vs. warns, and
[scripts/README.md](scripts/README.md) for the repolinter-comparison diagnostics.

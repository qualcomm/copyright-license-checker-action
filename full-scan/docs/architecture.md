# Full-Repository Scan — Architecture & Flow

How the **full-repository scan** works, how a consumer repo triggers it, and how it fits
alongside the pull-request patch scan.

> The diagrams below are [Mermaid](https://mermaid.js.org/) and render automatically on
> GitHub. To view locally, use the VSCode extension *"Markdown Preview Mermaid Support"* or
> paste a block into <https://mermaid.live>.

## Why it exists

The PR **patch scan** (`main.py`, at the repo root) only inspects the commit diff of a pull
request — the lines added/removed. A repository onboarded *after* it already has history never
has its legacy files checked; only new PR diffs are scanned.

The **full-repository scan** (`full-scan/full_scan.py`) closes that gap: it walks every source
file in the working tree and scans each file in its entirety. By default it covers git-tracked
files; pass `--include-untracked` to also scan untracked-but-not-`.gitignore`d files. It is a
**separate entry point and separate action**, and is **fully self-contained** — it imports
nothing from the patch scan (it bundles its own license lists, config, and `.licenseignore`
matcher).

---

## 1. Cross-repo flow (how a consumer triggers it)

```mermaid
flowchart TD
    subgraph CONSUMER["📦 Consumer repo"]
        SRC["Tracked source files<br/>+ LICENSE"]
        WF[".github/workflows/…<br/>on: push / schedule / workflow_dispatch"]
    end

    subgraph RW["🔁 qcom-reusable-workflows"]
        ORCH["reusable-qcom-preflight-checks-orchestrator.yml<br/>input: enable-full-repo-scan"]
        JOB{"enable-full-repo-scan == true?"}
        RFS["reusable-full-repo-scan.yml<br/>1. checkout consumer repo<br/>2. call the action below"]
    end

    subgraph ACT["🎯 copyright-license-checker-action"]
        AY["full-scan/action.yml (composite)<br/>setup-python 3.12 → pip install requirements<br/>→ python full_scan.py &lt;repo&gt; &lt;fail_on_findings&gt;"]
        FS["full-scan/full_scan.py"]
    end

    WF -->|"uses: …orchestrator.yml@ref<br/>enable-full-repo-scan: true"| ORCH
    ORCH --> JOB
    JOB -->|no| SKIP["job skipped"]
    JOB -->|yes| RFS
    RFS -->|"uses: qualcomm/copyright-license-checker-action/full-scan@ref"| AY
    AY --> FS
    RFS -. "checks out" .-> SRC
    FS -->|reads working tree| SRC
```

The action's steps run with the working directory set to the **consumer** checkout, so
`git ls-files` and license resolution (which read the current dir) see the consumer repo — not
this action's repo.

---

## 2. Inside `full_scan.py`

```mermaid
flowchart TD
    A["full_scan.main()<br/>argv: repo_name, fail_on_findings"] --> B["resolve_license(repo_name)<br/>(scanner/license_resolver.py)"]
    B --> C{"license type?"}
    C -->|permissive| P["allowed = PERMISSIVE_LICENSES"]
    C -->|copyleft| Q["allowed = COPYLEFT_LICENSES"]
    C -->|other| R["allowed = [that license]"]

    P & Q & R --> D["RepoScan(include_untracked, include_licenseignore)"]
    D --> E["FullScanner(repo_scan, allowed).run()"]
    E --> Z["beautify_scan_output()"]
    Z --> X{"blocking files AND fail_on_findings?"}
    X -->|yes| RED["print report → exit 1 (job fails)"]
    X -->|no| GREEN["print report / no issues → exit 0 (job passes)"]
```

`resolve_license` resolves the repo's top-level license from its `LICENSE` file (falling back
to a per-project config map, then a `BSD-3-Clause-Clear` default). It deliberately distrusts
scancode's low-confidence `proprietary-license` catch-all on the LICENSE file — scancode
sometimes mis-detects a standard OSS LICENSE as proprietary, which would otherwise flag every
compliant file in the repo.

---

## 3. Inside `RepoScan` + `FullScanner.run()`

```mermaid
flowchart TD
    subgraph RS["RepoScan (scanner/full_repo.py)"]
        A1["git ls-files<br/>(tracked; + untracked with --include-untracked)"] --> A2{"excluded ext?<br/>.patch/.md/.json/.yml"}
        A2 -->|yes| A3["skip"]
        A2 -->|no| A4{"matches .licenseignore?"}
        A4 -->|yes| A3
        A4 -->|no| A5{"source ext (.c .cpp .cc .py .go .rs .S …)<br/>or license-optional (.mk/.bp/.bb/.bbclass)?"}
        A5 -->|no| A3
        A5 -->|yes| A6["keep in self.files"]
    end

    A6 --> B1

    subgraph FSN["FullScanner (scanner/full_scanner.py)"]
        B1["scan_files(): copy each file into a temp dir (keep rel path)"] --> B2["run scancode ONCE<br/>--license --copyright"]
        B2 --> B3["rebuild each file's expression from<br/>CONFIDENT matches only"]
        B3 --> B4["for each file:"]
        B4 --> C1{"license detected?"}
        C1 -->|no| C2["No license header found (blocking*)"]
        C1 -->|yes| C3["classify_license() → ok / error / warning"]
        B4 --> D1{"any copyright?"}
        D1 -->|no| D2["No copyright statement (blocking*)"]
        C2 & C3 & D2 --> E1["sort into flagged_files / warning_files"]
    end
```

\* License-optional build files (`.mk`/`.bp`/`.bb`/`.bbclass`) are exempt from the
missing-header and missing-copyright checks; only an incompatible/uncertain license they
actually carry is reported.

---

## 4. `classify_license()` decision logic

Permissiveness is decided **structurally** (AND/OR aware) first; only if that fails do we split
error-vs-warning on the flattened license list.

```mermaid
flowchart TD
    A["SPDX expression<br/>e.g. '(MIT OR Apache-2.0) AND BSD-3-Clause'"] --> B{"empty?"}
    B -->|yes| OK["ok"]
    B -->|no| C{"is_expression_permissive?<br/>every AND group permissive;<br/>an OR group passes on any permissive option"}
    C -->|yes| OK
    C -->|no| D{"exactly one license,<br/>and it's the proprietary marker?"}
    D -->|yes| ERR["error (blocking)"]
    D -->|no| E{"is EVERY license uncertain/unknown?<br/>(LicenseRef-scancode-*)"}
    E -->|yes| WARN["warning (non-blocking)"]
    E -->|no| ERR
```

**Worked examples**

| SPDX expression | Result | Why |
|---|---|---|
| `BSD-3-Clause-Clear` | ok | permissive |
| `MIT OR GPL-2.0-only` | ok | OR group has a permissive option |
| `(MIT OR Apache-2.0) AND GPL-2.0` | error | permissive OR group, but the trailing AND term is disallowed |
| `GPL-2.0-only` | error | concrete disallowed license |
| `LicenseRef-scancode-unknown` | warning | only uncertain/unknown licenses |

---

## Checks performed per file

| Check | Condition | Severity |
|---|---|---|
| Missing license header | no license detected | blocking |
| Incompatible license | detected license not permitted for the repo | blocking |
| Missing copyright | no copyright statement in the file | blocking |
| Unexpected copyright holder | holder doesn't match the expected Qualcomm/LF pattern | warning |
| Uncertain license | only `LicenseRef-scancode-*` (unknown) licenses | warning |

## Exit behavior

`fail_on_findings` defaults to `false` (report-only, always exits `0`) so a periodic scan of a
legacy repository never unexpectedly breaks CI. Set it to `true` to make blocking findings fail
the run (exit `1`).

## Relationship to the patch scan

| | Patch scan | Full-repo scan |
|---|---|---|
| Entry point | `main.py` (repo root) | `full-scan/full_scan.py` |
| Action | `action.yml` (repo root) | `full-scan/action.yml` |
| Input | a `.patch` diff (added/removed lines) | whole files (`git ls-files`; + untracked opt-in) |
| Purpose | gate a PR's changes | periodic audit + catch legacy files |
| Coupling | — | none — full-scan is fully self-contained |

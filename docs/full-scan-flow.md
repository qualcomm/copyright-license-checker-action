# Full-Repository Scan — Architecture & Flow

This document explains how the **full-repository scan** works and how it fits
alongside the existing pull-request patch scan.

> The diagrams below are [Mermaid](https://mermaid.js.org/). They render
> automatically on GitHub. To view locally, use the VSCode extension
> *"Markdown Preview Mermaid Support"* or paste a block into
> <https://mermaid.live>.

## Why it exists

The PR **patch scan** ([`main.py`](../main.py)) only inspects the commit diff of
a pull request — the lines added/removed. That means a repository onboarded
*after* it already has history never has its legacy files checked; only new PR
diffs are scanned.

The **full-repository scan** ([`full_scan.py`](../full_scan.py)) closes that gap:
it walks every tracked source file in the working tree and scans each file in
its entirety. It is a **separate entry point and separate action** — it does not
modify or share mutable state with the patch scan.

---

## 1. Overall flow

```mermaid
flowchart TD
    A["GitHub Actions triggers<br/>full-scan/action.yml<br/>(schedule / manual)"] --> B["Checkout consumer repo"]
    B --> C["Setup Python 3.8<br/>pip install ../requirements.txt"]
    C --> D["python ../full_scan.py<br/>&lt;repo_name&gt; &lt;fail_on_findings&gt;"]

    D --> E["full_scan.main()"]
    E --> F["get_license(repo_name)<br/>(imported from main.py)"]
    F --> G{"license type?"}
    G -->|permissive| H1["allowed = PERMISSIVE_LICENSES"]
    G -->|copyleft| H2["allowed = COPYLEFT_LICENSES"]
    G -->|other| H3["allowed = [that license]"]

    H1 & H2 & H3 --> I["RepoScan()<br/>enumerate files"]
    I --> J["FullScanner(repo_scan, allowed).run()"]
    J --> K["beautify_scan_output()"]
    K --> L{"blocking files<br/>AND fail_on_findings?"}
    L -->|yes| M["print report<br/>exit 1"]
    L -->|no| N["print report / no issues<br/>exit 0"]
```

---

## 2. Inside `RepoScan` + `FullScanner.run()`

```mermaid
flowchart TD
    subgraph RS["RepoScan (scanner/full_repo.py)"]
        A1["git ls-files<br/>(tracked files only)"] --> A2{"ends with<br/>.patch/.bb/.md/.json/.yml?"}
        A2 -->|yes| A3["skip"]
        A2 -->|no| A4{"matches<br/>.licenseignore?"}
        A4 -->|yes| A3
        A4 -->|no| A5{"is a source ext?<br/>.c .cpp .py .go ..."}
        A5 -->|no| A3
        A5 -->|yes| A6["keep in self.files"]
    end

    A6 --> B1

    subgraph FS["FullScanner (scanner/full_scanner.py)"]
        B1["scan_files(): copy each file<br/>into temp dir (keep rel path)"] --> B2["run scancode ONCE<br/>--license --copyright"]
        B2 --> B3["parse JSON into<br/>path: license, copyrights"]
        B3 --> B4["for each file:"]
        B4 --> C1{"license detected?"}
        C1 -->|no| C2["No license header found<br/>(blocking)"]
        C1 -->|yes| C3["classify_license()<br/>ok / error / warning"]
        B4 --> D1{"any copyright?"}
        D1 -->|no| D2["No copyright statement<br/>(blocking)"]
        C2 & C3 & D2 --> E1["sort into<br/>flagged_files / warning_files"]
    end
```

---

## 3. `classify_license()` decision logic

This mirrors `main.py`'s two-stage decision. Permissiveness is decided
**structurally** (AND/OR aware) first; only if that fails do we split
error-vs-warning on the flattened license list.

```mermaid
flowchart TD
    A["SPDX expression<br/>e.g. 'MIT OR GPL-2.0-only'"] --> B{"empty?"}
    B -->|yes| OK["ok"]
    B -->|no| C{"is_expression_permissive?<br/>(AND/OR aware — mirrors patch path)"}
    C -->|yes| OK
    C -->|no| D{"exactly one license,<br/>and it's the proprietary marker?"}
    D -->|yes| ERR["error (blocking)"]
    D -->|no| E{"is EVERY license<br/>uncertain/unknown?<br/>(LicenseRef-scancode-*)"}
    E -->|yes| WARN["warning (non-blocking)"]
    E -->|no| ERR
```

**Worked examples**

| SPDX expression | Result | Why |
|---|---|---|
| `BSD-3-Clause-Clear` | ok | permissive |
| `MIT OR GPL-2.0-only` | ok | OR group has a permissive option |
| `GPL-2.0-only` | error | concrete disallowed license |
| `proprietary-license AND BSD-3-Clause` | error | not permissive; concrete disallowed mixed in |
| `LicenseRef-scancode-unknown` | warning | only uncertain/unknown licenses |

---

## Checks performed per file

| Check | Condition | Severity |
|---|---|---|
| Missing license header | no license detected | blocking |
| Incompatible license | detected license not permitted for the repo | blocking |
| Missing copyright | no copyright statement in the file | blocking |
| Uncertain license | only `LicenseRef-scancode-unknown*` licenses | warning (non-blocking) |

## Exit behavior

`fail_on_findings` defaults to `false` (report-only, always exits `0`) so a
periodic scan of a legacy repository never unexpectedly breaks CI. Set it to
`true` to make blocking findings fail the run (exit `1`).

## Relationship to the patch scan

| | Patch scan | Full-repo scan |
|---|---|---|
| Entry point | `main.py` | `full_scan.py` |
| Action | `action.yml` | `full-scan/action.yml` |
| Input | a `.patch` diff (added/removed lines) | whole tracked files (`git ls-files`) |
| Purpose | gate a PR's changes | periodic audit + catch legacy files |
| Shared code | — | read-only license data imported from `main.py` |

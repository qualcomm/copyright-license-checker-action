# Compliance Documentation: full-repository scan

This file documents what is specific to the **full-repository scan** — the whole-tree audit
run by `full-scan/full_scan.py` — and not to the pull-request patch check.

Everything the two scanners share (the five blocking scenarios, the uncertain-license
warnings, the permissive/copyleft categories, `.licenseignore` mechanics, troubleshooting)
is documented in [../COMPLIANCE.md](../COMPLIANCE.md). Read that first; the sections below
are additions and exceptions on top of it.

Because this scan walks every source file rather than a diff, it also reports findings on
legacy files that nobody touched in the change under test.

---

## Exception to "Missing License on New Source Files"

Applies to [../COMPLIANCE.md](../COMPLIANCE.md) section 4.

In the full-repository scan, build-system files (`.mk`, `.bp`) and certain trivial marker
files (e.g. `__init__.py`) are exempt from both the missing-license and missing-copyright
requirements (they routinely have neither). A present-but-incompatible license in one of
them is still a blocking error. See "License-Optional Files" below.

---

## Unexpected Copyright Holder (warning)

**What triggers this:**
- A source file HAS a copyright statement, but its holder does not match the expected
  Qualcomm / Linux Foundation pattern.

A file with NO copyright at all is still a **blocking** error ("No copyright statement
found"); only a present-but-unexpected holder warns.

**Expected holders (any one satisfies the check, matched case-insensitively against
scancode's detected copyright statements):**
- `Copyright ... Qualcomm Innovation Center, Inc`
- `Qualcomm Technologies, Inc`
- `Copyright (c) <year 2012-2022> The Linux Foundation`

**Example - Warning:**
```
⚠️ WARNINGS (Non-blocking):
📄 File: src/third_party/vendor.c
⚠️ COPYRIGHT WARNINGS:
  - Copyright holder does not match the expected Qualcomm/Linux Foundation pattern, review manually
```

**What to do:**
- Confirm the copyright is correct for the file (e.g. it is legitimately third-party / vendored).
- For vendored dependencies, consider adding the path to `.licenseignore`.
- For first-party code, use a standard Qualcomm copyright header.

**Compliance Impact:** LOW - Attribution review; does not block development.

---

## License-Optional Files

Some files are scanned under a relaxed "license-optional" tier, because they routinely ship
without a license header or copyright:

**By extension:** `.mk` (Makefiles), `.bp` (Android blueprints)

**By filename:** `__init__.py` (empty/trivial Python package markers), matched by basename so
any `__init__.py` in any package qualifies. To exempt another file, add its name to
`LICENSE_OPTIONAL_FILES` in [scanner/full_repo.py](scanner/full_repo.py).

> BitBake files (`.bb`, `.bbclass`, `.bbappend`) are **excluded from the full-repository scan
> entirely** — they are recipe/class metadata rather than shipped source, so they are not
> scanned at all (not even for an incompatible license).

| Situation | Result |
|---|---|
| No license header | ✅ OK (not flagged) |
| No copyright statement | ✅ OK (not flagged) |
| Present incompatible license (e.g. `GPL-2.0-only` under a BSD repo) | 🚨 **BLOCKING** |
| Uncertain `LicenseRef-scancode-*` license | ⚠️ WARNING (non-blocking) |

In other words: these files are never *required* to carry licensing information, but if they
do declare a license it must still be compatible with the repository.

---

## When the license baseline can't be established

The full-repository scan needs a repository-level license baseline to judge each file
against, and it establishes that baseline **only** from real evidence — it does **not**
fabricate a default:
1. a license detected by scancode in a **root-level license file** (`LICENSE` /
   `LICENSE.txt` / `LICENSE.md` / `COPYING`, incl. British/lowercase spellings), or
2. an explicit entry in **[scanner/config.py](scanner/config.py)** for repositories onboarded there.

If none of those establish a baseline, the scan **stops** and performs no per-file
analysis — it never assumes a permissive default. The stop reports one of:

| Situation | Result |
|---|---|
| License detected by scancode in a root-level license file | ✅ Scanned against the detected license |
| No license file, but repo is in `scanner/config.py` | ✅ Scanned against the configured license |
| No root-level license file (and not in config) | ⛔ **Stopped** — "No Root-Level Licence Found" |
| Root license file present but **empty** | ⛔ **Stopped** — "No Root-Level Licence Found" (empty) |
| Root license file present but license **not identifiable** | ⛔ **Stopped** — "License Not Conclusively Detected" |

A stop is **not** a pass: no files were analyzed. In the Jira runner it posts a clear
"scan not performed" comment, never "No issues found (OK)". Exit behavior follows
`fail_on_findings`: **fails the build** (non-zero) when `true`, **report-only** (exit 0)
otherwise.

**How to fix:** please fix this issue (add or complete a recognized license file at the
repository root, or onboard the repository in `scanner/config.py`) **or reach out to the
ossops.support team for help**, then re-run.

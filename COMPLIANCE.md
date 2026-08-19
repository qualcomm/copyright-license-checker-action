# Compliance Documentation

## Overview

This GitHub Action enforces copyright and license compliance for code changes in pull requests. It analyzes the diff/patch to ensure that all modifications adhere to the repository's licensing policies and copyright requirements.

**Action Repository**: https://github.com/qualcomm/copyright-license-checker-action

## Build Blocking Scenarios

The following scenarios will **BLOCK** your build and require remediation before the PR can be merged:

### 1. Incompatible License Added

**What triggers this:**
- Adding code with a license that is not in the repository's allowed license list
- Introducing code under GPL, AGPL, or other copyleft licenses when the repository uses permissive licenses (BSD, MIT, Apache)
- Adding proprietary or restrictive licenses

**Example:**
```
🚨 BLOCKING ERROR:
📄 File: src/new_module.c
🚨 License issues detected:
  - Incompatible license added: GPL-2.0-only
```

**How to fix:**
- Remove the incompatible code
- Replace with code under a compatible license
- Obtain permission to relicense the code
- Add the license to the allowed list if it's actually compatible (requires approval)

**Compliance Impact:** HIGH - Mixing incompatible licenses can create legal issues and licensing conflicts

---

### 2. License Deletion Without Replacement

**What triggers this:**
- Removing license headers from existing files
- Deleting license statements without adding new ones
- Modifying files in a way that removes license information

**Example:**
```
🚨 BLOCKING ERROR:
📄 File: src/utils.py
🚨 License issues detected:
  - License deleted: BSD-3-Clause-Clear
```

**How to fix:**
- Restore the original license header
- Add an appropriate license header if it was missing
- Ensure license information is preserved during refactoring

**Compliance Impact:** HIGH - Removing license information can violate licensing terms and create legal ambiguity

---

### 3. License Change (Modification)

**What triggers this:**
- Changing the license of existing code from one license to another
- Replacing license headers with different licenses
- Modifying license terms

**Example:**
```
🚨 BLOCKING ERROR:
📄 File: src/core.cpp
🚨 License issues detected:
  - License deleted: MIT and license added: Apache-2.0
```

**How to fix:**
- Revert to the original license
- Obtain proper authorization for license changes
- Document the reason for license change if legitimate
- Ensure all copyright holders agree to the license change

**Compliance Impact:** CRITICAL - Changing licenses without proper authorization can violate copyright law

---

### 4. Missing License on New Source Files

**What triggers this:**
- Adding new source code files without license headers
- Creating new modules without proper licensing information

**Supported source file extensions:**
```
.c, .cpp, .h, .hpp, .java, .py, .js, .ts, .rb, .go, .swift, .kt, .kts, .sh
```

**Example:**
```
🚨 BLOCKING ERROR:
📄 File: src/new_feature.py
🚨 License issues detected:
  - No license added for source file: src/new_feature.py
```

**How to fix:**
- Add appropriate license header to the file
- Use the repository's standard license template
- Include SPDX identifier for clarity


**Compliance Impact:** MEDIUM - New code without licenses creates ambiguity about usage rights

**Full-repository scan exception — license-optional files:** In the
full-repository scan, build-system files (`.mk`, `.bp`) and certain trivial marker
files (e.g. `__init__.py`) are exempt from both the missing-license and
missing-copyright requirements (they routinely have neither). A
present-but-incompatible license in one of them is still a blocking error. See
"Full-Repository Scan: License-Optional Files" below.

---

### 5. Copyright Deletion

**What triggers this:**
- Removing copyright statements from existing code
- Deleting copyright holder information
- Modifying copyright notices inappropriately

**Example:**
```
🚨 BLOCKING ERROR:
📄 File: src/algorithm.c
⚠️ Copyright issues detected:
  - Copyright deletions detected: ['Copyright (c) 2024 Original Author']
```

**How to fix:**
- Restore the original copyright statement
- Add your copyright in addition to (not replacing) existing copyrights
- Follow the pattern: keep old copyrights, add new ones

**Allowed Exception:**
The action allows the following copyright transition:
- FROM: "Qualcomm Innovation Center, Inc. All rights"
- TO: "Qualcomm Technologies, Inc. and/or its subsidiaries"

**Compliance Impact:** HIGH - Removing copyright notices can violate copyright law and attribution requirements

---

## Non-Blocking Warnings

The following scenarios generate **WARNINGS** but do NOT block the build:

### Uncertain/Unknown License Detection

**What triggers this:**
- Scancode detects uncertain or unknown license patterns
- Any `LicenseRef-scancode-*` license that is not in the known permissive list
- Ambiguous license text that scancode cannot confidently identify

**Specific patterns treated as warnings:**
- `LicenseRef-scancode-unknown-*` (e.g., `LicenseRef-scancode-unknown-license-reference`)
- `LicenseRef-scancode-warranty-disclaimer` (just a disclaimer, not a license)
- `LicenseRef-scancode-proprietary-*` (when mixed with other uncertain licenses)
- Any other `LicenseRef-scancode-*` not explicitly in the permissive list

**How it works:**
The action intelligently evaluates license expressions:
1. If ALL licenses in the expression are uncertain/unknown → **WARNING**
2. If ANY license is a known incompatible license (GPL, AGPL, etc.) → **BLOCKING ERROR**
3. Mixed uncertain licenses are treated as warnings to allow manual review

**Example - Warning:**
```
⚠️ WARNINGS (Non-blocking):
📄 File: src/vendor/third_party.c
⚠️ License warnings:
  - Incompatible license added: LicenseRef-scancode-unknown-license-reference AND LicenseRef-scancode-proprietary-license AND LicenseRef-scancode-warranty-disclaimer
```

**Example - Blocking Error (mixed with known incompatible):**
```
🚨 BLOCKING ERROR:
📄 File: src/module.c
🚨 License issues detected:
  - Incompatible license added: GPL-2.0-only AND LicenseRef-scancode-unknown-license-reference
```
*This blocks because GPL-2.0-only is a known incompatible license*

**What to do:**
- Manually review the file to identify the actual license
- Add proper license headers if missing or unclear
- Update the code to use clear, standard SPDX license identifiers
- Consider adding the file to `.licenseignore` if it's a known false positive or vendored dependency
- If the license is genuinely unknown, work with the code owner to clarify licensing

**Why this is a warning, not an error:**
Scancode may flag licenses as "unknown" due to:
- Non-standard license formatting
- Partial or truncated license text
- Custom license variations
- Detection algorithm limitations

These cases require human review but shouldn't automatically block development, as they may be false positives or require clarification rather than immediate remediation.

**Compliance Impact:** LOW - Requires manual review but doesn't block development. However, unresolved unknown licenses should be addressed before production release.

---

### Special Case: Sole Proprietary License

**What triggers this:**
- A file contains ONLY `LicenseRef-scancode-proprietary-license` with no other licenses

**Example - Blocking Error:**
```
🚨 BLOCKING ERROR:
📄 File: src/proprietary.c
🚨 License issues detected:
  - Incompatible license added: LicenseRef-scancode-proprietary-license
```

**Why this blocks:**
When scancode identifies a file as having ONLY a proprietary license (not mixed with other uncertain licenses), it's a clear indication of incompatible licensing that should be addressed immediately.

**How to fix:**
- Remove the proprietary code
- Replace with code under a compatible license
- Add proper open-source license headers
- Obtain permission to relicense the code

**Note:** If `LicenseRef-scancode-proprietary-license` appears mixed with other uncertain licenses (e.g., `LicenseRef-scancode-unknown-*`), it's treated as a warning for manual review, as this may indicate scancode detection ambiguity rather than actual proprietary code.

**Compliance Impact:** HIGH - Proprietary code in open-source repositories creates licensing conflicts

---

### Unexpected Copyright Holder (Full-Repository Scan Only)

**What triggers this:**
- A source file HAS a copyright statement, but its holder does not match the expected Qualcomm / Linux Foundation pattern.

This applies only to the **full-repository scan** (the whole-tree audit), not the pull-request patch check. A file with NO copyright at all is still a **blocking** error ("No copyright statement found"); only a present-but-unexpected holder warns.

**Expected holders (any one satisfies the check, matched case-insensitively against scancode's detected copyright statements):**
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

## Full-Repository Scan: License-Optional Files

This applies only to the **full-repository scan** (the whole-tree audit), not the
pull-request patch check.

Some files are scanned under a relaxed "license-optional" tier, because they
routinely ship without a license header or copyright:

**By extension:** `.mk` (Makefiles), `.bp` (Android blueprints)

**By filename:** `__init__.py` (empty/trivial Python package markers), matched by
basename so any `__init__.py` in any package qualifies. To exempt another file, add
its name to `LICENSE_OPTIONAL_FILES` in `full-scan/scanner/full_repo.py`.

> BitBake files (`.bb`, `.bbclass`, `.bbappend`) are **excluded from the full-repository
> scan entirely** — they are recipe/class metadata rather than shipped source, so they are
> not scanned at all (not even for an incompatible license).

| Situation | Result |
|---|---|
| No license header | ✅ OK (not flagged) |
| No copyright statement | ✅ OK (not flagged) |
| Present incompatible license (e.g. `GPL-2.0-only` under a BSD repo) | 🚨 **BLOCKING** |
| Uncertain `LicenseRef-scancode-*` license | ⚠️ WARNING (non-blocking) |

In other words: these files are never *required* to carry licensing information,
but if they do declare a license it must still be compatible with the repository.

---

## Full-Repository Scan: No Root-Level Licence Found

This applies only to the **full-repository scan** (the whole-tree audit), not the
pull-request patch check.

The full-repository scan needs a repository-level license baseline to judge each file
against. It establishes that baseline from a **root-level license file**
(`LICENSE` / `LICENSE.txt` / `LICENSE.md` / `COPYING`), or from an explicit entry in
`scanner/config.py` for repositories onboarded there.

If a repository has **neither** — no root-level license file **and** no configured
license — the scan does **not** assume a default. Instead it stops with the status
**"No Root-Level Licence Found"** and performs no per-file license or copyright
analysis. This avoids fabricating a permissive baseline and then wrongly flagging every
file in, for example, a GPL repository that simply never committed a `LICENSE` file.

An **empty / whitespace-only** license file counts as *no license* (it declares nothing),
so it triggers the same stop — the report clarifies that a file exists but is empty. A
**non-empty** license file that scancode merely can't classify is different: it keeps the
`BSD-3-Clause-Clear` default (a safety net for genuine BSD files that scancode mis-detects),
so it is still scanned.

| Situation | Result |
|---|---|
| Root license file present, license detected | Scanned normally against the detected license |
| Non-empty license file, not conclusively detected | Scanned against the `BSD-3-Clause-Clear` default |
| No license file, but repo is in `scanner/config.py` | Scanned normally against the configured license |
| Empty license file (or none) **and** not in `scanner/config.py` | ⛔ **Scan stopped** — status "No Root-Level Licence Found" |

Exit behavior follows `fail_on_findings`: the stopped scan **fails the build** (non-zero
exit) when `fail_on_findings` is `true`, and is **report-only** (exit 0) otherwise.

**How to fix:** add a license file at the repository root, or onboard the repository in
`scanner/config.py`, then re-run.

---

## License Categories

### Permissive Licenses (Generally Allowed)
```
BSD-3-Clause, MIT, Apache-2.0, BSD-3-Clause-Clear, ISC, CC0-1.0, Zlib
```

### Copyleft Licenses (Restricted)
```
GPL-2.0, GPL-3.0, AGPL-3.0, LGPL-3.0
```

**Note:** The specific allowed licenses depend on your repository's configuration in `scanner/config.py`

---

## Best Practices for Compliance

### 1. Always Include License Headers
Every source file should have a clear license header:

**Example:**
```
Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
SPDX-License-Identifier: BSD-3-Clause-Clear
```

### 2. Preserve Existing Copyrights
When modifying files:
- Keep all existing copyright statements
- Add your copyright below existing ones
- Never remove or modify existing copyright holders

### 3. Use Standard License Identifiers
- Use SPDX identifiers for clarity
- Avoid custom or ambiguous license text
- Reference standard license texts

### 4. Document Third-Party Code
- Clearly mark third-party code
- Include original license information
- Consider using `.licenseignore` for vendored dependencies

### 5. Review Before Committing
- Check license headers before committing
- Verify copyright statements are accurate
- Run the action locally if possible

---

## Exemptions and Overrides

### Using .licenseignore

Create a `.licenseignore` file at the repository root to exclude files from license checks:

```
# Ignore vendored dependencies
vendor/**
third_party/**

# Ignore generated files
*.generated.js
build/**

# Ignore test fixtures
tests/fixtures/**
```

**Use with caution:** Only ignore files where license checking is not applicable or creates false positives.

---

## Troubleshooting

### Build Blocked - What to Do?

1. **Read the error message carefully** - It tells you exactly what's wrong
2. **Check the specific file** mentioned in the error
3. **Review the compliance scenario** that matches your error
4. **Apply the recommended fix**
5. **Test locally** if possible before pushing

### Common Mistakes

❌ **Removing license headers during refactoring**
✅ Preserve all license information when restructuring code

❌ **Copying code without preserving licenses**
✅ Always maintain original license and copyright information

❌ **Adding GPL code to BSD-licensed projects**
✅ Verify license compatibility before adding third-party code

❌ **Forgetting license headers on new files**
✅ Use templates or IDE snippets to add headers automatically

---

## Contact and Support

### For Internal Developers

**Contact:** lost.dev  
**POC:** targoy (Tarun Goyal)

For questions about license compliance or to request exemptions:
- Review your organization's open source compliance policies
- Consult with your legal team for complex licensing questions
- File an issue in the action repository for technical problems
- Reach out to the POC for internal support and guidance

---

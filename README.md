# copyright-license-checker-action

## About

This action is a GitHub Action that checks for copyright and license issues in a repository. It uses the scancode library to detect licenses and the patch library to parse patch files.

📋 **[View Detailed Compliance Documentation](COMPLIANCE.md)** - Learn about all scenarios that can block your build and how to fix them.

## Usage

To use this action, you need to create a GitHub Action workflow file in your repository (.github/workflows/copyright-license-checker-action.yml). Here's an example of how to use this action

```yml
name: Run Copyright and License Check
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  copyright-license-detector:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout PR head
        uses: actions/checkout@v3
        with:
          fetch-depth: 0  # Full history so we can diff properly
          ref: ${{ github.event.pull_request.head.ref }}
          repository: ${{ github.event.pull_request.head.repo.full_name }}
 
      - name: Add PR base repo as remote and fetch it
        run: |
          git remote add upstream https://github.com/${{ github.event.pull_request.base.repo.full_name }}.git
          git fetch upstream
 
      - name: Generate final patch between base and head
        run: |
          git diff upstream/${{ github.event.pull_request.base.ref }} > pr.patch
          head -n 100 pr.patch

      - name: Run copyright/license detector
        uses: qualcomm/copyright-license-checker-action@main
        with:
          patch_file: pr.patch
          repo_name: ${{ github.repository }}

```

### Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `patch_file` | *(required)* | Path to the patch file to check. |
| `repo_name` | *(required)* | The name of the GitHub repository. |
| `mode` | `opensource` | `opensource` or `proprietary`. See [Proprietary Mode](COMPLIANCE.md#proprietary-mode) for details. |
| `proprietary_entities` | *(empty)* | CSV extra copyright-holder strings treated as internal authorship in `proprietary` mode. Quote a name containing commas. |

`proprietary_entities` uses standard CSV escaping. For example, provide an
entity with a comma as `proprietary_entities: '"Acme Corp, Inc.",Other Co'`.

### Checking a Proprietary Codebase

If you are checking an internally developed proprietary codebase rather than an open-source repository, set `mode: proprietary`:

```yml
      - name: Run copyright/license detector
        uses: qualcomm/copyright-license-checker-action@main
        with:
          patch_file: pr.patch
          repo_name: ${{ github.repository }}
          mode: proprietary
```

In this mode, Qualcomm-authored files are expected to carry a proprietary rights statement rather than an OSS license. Vendored permissive open-source code is allowed but warns for review and attribution. See [Proprietary Mode](COMPLIANCE.md#proprietary-mode) for the full behavior.

## Scenarios Covered
### License Detection
This action detects licenses in the code changes and ensures that any added licenses are permissive and compliant with the repository's policies.

**Uncertain License Handling**: When scancode detects uncertain or unknown licenses (any license containing `LicenseRef-scancode-unknown`), added or replacement detections are treated as **warnings** rather than blocking errors. This generic pattern matching catches all scancode unknown license variants (e.g., `LicenseRef-scancode-unknown`, `LicenseRef-scancode-unknown-license-reference`, etc.). The build will continue with a warning message, allowing you to review these cases manually without blocking the CI/CD pipeline.

In `mode: proprietary`, deleting a `LicenseRef-scancode-*` reference without a
replacement license is a blocking error, so license-removal checks remain
strict.

### Copyright Changes
The action checks for any changes in copyright statements within the code and flags any deletions or modifications of existing copyright holders

### Source File Identification
The action identifies source files based on their extensions and ensures that appropriate licenses are added to new source files as per repository's policies

```text
'.c', '.cpp', '.h', '.hpp', '.java', '.py', '.js', '.ts', '.rb', '.go', '.swift', '.kt', '.kts', '.sh'
```

**Excluded File Types**: The following file types are automatically excluded from all license and copyright checks:
- `.md` (Markdown documentation files)
- `.patch` (Patch files)
- `.bb` (BitBake recipe files)
- `.json` (JSON files)
- `.yml` (YAML files)

### Compliance Reporting
The action provides a detailed report with two categories:
- **🚨 BLOCKING ERRORS**: Issues that will fail the build (incompatible licenses, copyright deletions, etc.)
- **⚠️ WARNINGS (Non-blocking)**: Uncertain or unknown added/replacement license detections that should be reviewed

The action will only fail the build if there are blocking errors. Warnings are informational and allow the build to proceed.

### License Ignore Configuration

The action supports an optional `.licenseignore` file to exclude files or paths from license checks. Create a `.licenseignore` file at the repository root and list patterns (git‑style wildcards) for files that should be ignored.

## Developing Agentic Workflows

Agentic Workflows use a reviewable Markdown source file and a generated GitHub
Actions lock file. For example,
`.github/workflows/daily-malicious-code-scan.md` is the source for
`.github/workflows/daily-malicious-code-scan.lock.yml`, and
`.github/workflows/pull-request-malicious-code-scan.md` is the source for its
matching lock file. GitHub Actions runs the lock files; do not edit them by
hand.

The daily scan reviews changes from the preceding three days and publishes
concrete post-merge findings as code-scanning alerts. The pull-request scan is
a separate, pre-merge gate that reviews the PR diff through read-only GitHub
tools. It never checks out, installs, or executes pull-request code, and it
does not create alerts, comments, or issues. Arbitrary agent network egress is
denied; controlled GitHub MCP and Copilot inference transport are still needed
to perform the review.

For the pull-request scan:

- A complete review with no concrete finding succeeds and writes a bounded job
  summary.
- A concrete finding writes a concise summary and fails the `fail_on_finding`
  job.
- Missing, truncated, or partially reviewed PR data produces an incomplete,
  failed scan. A detection failure or absent detector verdict also fails the
  scan.

The scan is triggered alongside existing CI; it does not prevent other
`pull_request` workflows from starting. To use it as a merge gate, configure
branch protection or a ruleset after merge. Prefer an organization-required
workflow that is controlled outside the PR. Where only required status checks
are available, require all generated scanner jobs: `activation`, `agent`,
`detection`, `fail_on_finding`, `safe_outputs`, and `conclusion`. Recheck these
names after every gh-aw upgrade. A successful agent assessment is evidence from
an automated review, not a guarantee that a change is safe.

Before relying on the gate for untrusted forks, verify a real non-collaborator
fork run can authenticate for Copilot inference and retrieve the complete diff.
Do not broaden token permissions or expose repository secrets to make this work.

After changing an Agentic Workflow source file, regenerate and validate its
lock file:

```sh
gh aw compile daily-malicious-code-scan pull-request-malicious-code-scan --validate
gh aw compile pull-request-malicious-code-scan --no-emit --validate
```

Commit both files. If compilation reports new actions or restricted secrets,
review the generated change before rerunning the compiler with `--approve`.
Use `--no-emit` for non-mutating validation. `--dry-run` deliberately changes
the emitted lock for dry-run behavior, so run it only in an isolated checkout or
regenerate the normal deployment lock before committing.

## Documentation

- **[COMPLIANCE.md](COMPLIANCE.md)** - Comprehensive guide on build-blocking scenarios, compliance requirements, and troubleshooting
- **[GitHub Action Repository](https://github.com/qualcomm/copyright-license-checker-action)** - Source code and latest updates

### Runtime dependencies

`requirements.txt` is the human-maintained list of direct runtime
requirements. `requirements.lock` is the complete, hash-verified dependency
graph for Python 3.12 on Linux x86_64, generated and installed with
[uv](https://docs.astral.sh/uv/). The composite action installs only from the
lock file.

After changing `requirements.txt`, regenerate and commit the lock file:

```sh
./scripts/update-lock.sh
```

The script requires uv 0.11.16 and preserves the versions already recorded in
the lock where possible. Run `./scripts/update-lock.sh --upgrade` to
intentionally refresh the complete dependency graph.
CI validates the committed lock and emits a warning when regeneration would
change it; CI does not upgrade dependencies or fail solely because the lock is
stale.

On a network that intercepts TLS, use the trusted corporate bundle described
below, for example:
`SSL_CERT_FILE=/path/to/combined-ca-bundle.pem ./scripts/update-lock.sh`.

Verify the locked installation without changing your environment:

```sh
uv pip sync --dry-run --strict --require-hashes --only-binary=lxml requirements.lock
```

### Real ScanCode container check

The unit tests mock the ScanCode CLI. Use the dedicated Dockerfile to validate
the real ScanCode installation and scan this repository's `LICENSE` file. The
image installs the hash-verified dependencies from the checked-out branch's
`requirements.lock` using uv. ScanCode's required `extractcode-7z` wheel is
published for Linux x86_64, so build this validation image for that platform:

```sh
DOCKER_BUILDKIT=1 docker build --platform linux/amd64 \
  --tag copyright-license-checker-scancode-test \
  --file Dockerfile.scancode-test .
docker run --rm --platform linux/amd64 copyright-license-checker-scancode-test
```

The build intentionally uses `--only-binary=lxml`, matching the action's
wheel-only installation requirement. To validate a dependency update, check
out the branch containing its updated `requirements.txt` input and
`requirements.lock` before building.
The image installs the native libarchive, libgomp, and libmagic prerequisites
required by ScanCode. It also locates libarchive at runtime. The image targets
`linux/amd64`, matching the GitHub Action runtime.

On networks that intercept TLS, provide the trusted corporate CA bundle as a
BuildKit secret; it is used only while installing packages and is not stored in
the image:

```sh
DOCKER_BUILDKIT=1 docker build --platform linux/amd64 \
  --secret id=corp_ca,src=/path/to/combined-ca-bundle.pem \
  --tag copyright-license-checker-scancode-test \
  --file Dockerfile.scancode-test .
```

## Copyright and License

```text
Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
SPDX-License-Identifier: BSD-3-Clause
```

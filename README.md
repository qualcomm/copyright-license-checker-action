# copyright-license-checker-action

## About

This action is a GitHub Action that checks for copyright and license issues in a repository. It uses the scancode library to detect licenses and the patch library to parse patch files.

📋 **[View Detailed Compliance Documentation](COMPLIANCE.md)** - Learn about all scenarios that can block your build and how to fix them.

## Usage

To use this action, you need to create a GitHub Action workflow file in your repository (.github/workflows/copyright-license-checker-action.yml). Here's an example of how to use this action

```yml
name: Run Copyright and License Check
on:
  pull_request_target:
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

## Scenarios Covered
### License Detection
This action detects licenses in the code changes and ensures that any added licenses are permissive and compliant with the repository's policies.

**Uncertain License Handling**: When scancode detects uncertain or unknown licenses (any license containing `LicenseRef-scancode-unknown`), these are treated as **warnings** rather than blocking errors. This generic pattern matching catches all scancode unknown license variants (e.g., `LicenseRef-scancode-unknown`, `LicenseRef-scancode-unknown-license-reference`, etc.). The build will continue with a warning message, allowing you to review these cases manually without blocking the CI/CD pipeline.

### Copyright Changes
The action checks for any changes in copyright statements within the code and flags any deletions or modifications of existing copyright holders

### Source File Identification
The action identifies source files based on their extensions and ensures that appropriate licenses are added to new source files as per repository's policies

```text
'.c', '.cpp', '.h', '.hpp', '.java', '.py', '.js', '.ts', '.rb', '.go', '.swift', '.kt', '.kts'
```

**Excluded File Types**: The following file types are automatically excluded from all license and copyright checks:
- `.md` (Markdown documentation files)
- `.patch` (Patch files)
- `.bb` (BitBake recipe files)

### Compliance Reporting
The action provides a detailed report with two categories:
- **🚨 BLOCKING ERRORS**: Issues that will fail the build (incompatible licenses, copyright deletions, etc.)
- **⚠️ WARNINGS (Non-blocking)**: Uncertain or unknown license detections that won't block the build but should be reviewed

The action will only fail the build if there are blocking errors. Warnings are informational and allow the build to proceed.

### License Ignore Configuration

The action supports an optional `.licenseignore` file to exclude files or paths from license checks. Create a `.licenseignore` file at the repository root and list patterns (git‑style wildcards) for files that should be ignored.

## Documentation

- **[COMPLIANCE.md](COMPLIANCE.md)** - Comprehensive guide on build-blocking scenarios, compliance requirements, and troubleshooting
- **[GitHub Action Repository](https://github.com/qualcomm/copyright-license-checker-action)** - Source code and latest updates

## Copyright and License

```text
Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
SPDX-License-Identifier: BSD-3-Clause
```

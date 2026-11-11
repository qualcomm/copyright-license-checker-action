# Reusable CLI Implementation Summary

This document records the completed, backward-compatible stages of the
reusable CLI work described in `REUSABLE_CLI_PLAN.md`.

## Branch Stack

The branches form a stack. Each branch contains the commits from the branch to
its left and should be reviewed or merged in this order:

```text
test/reusable-cli-characterization
  -> refactor/namespaced-cli
  -> feat/installable-cli
  -> refactor/action-use-packaged-cli
  -> docs/standalone-cli-usage
```

## Step 1: CLI Characterization

**Branch:** `craigez:test/reusable-cli-characterization`

This initial test branch defined the existing command-line behavior before the
implementation moved or was packaged. It covers command invocation, working
directory behavior, invalid paths, ScanCode failures, reports, and GitHub
Actions annotations.

## Step 2: Namespaced CLI

**Branch:** `refactor/namespaced-cli`

This branch moved the characterized implementation behind its new package and
CLI boundary:

- Moved the implementation to `src/copyright_license_checker`.
- Kept the root `main.py` and top-level `scanner` package as compatibility
  wrappers for existing Action users and import paths.
- Added the new CLI boundary with optional repository name, `--repo-root`,
  typed operational errors, and explicit CLI exit-code handling.

## Step 3: Installable Console Command

**Branch:** `feat/installable-cli`\
**Commit:** `cf12150` — `feat(cli): add installable console command`

This branch made the checker installable as a Python application:

- Added PEP 517 and project metadata in `pyproject.toml` for the
  `copyright-license-checker` distribution and console command.
- Declared Python 3.12 support, BSD-3-Clause licensing, and pinned runtime
  dependencies, including ScanCode and the wheel-compatible lxml pin.
- Added a single package version source, used by build metadata and
  `copyright-license-checker --version`.
- Kept `requirements.txt` for the existing Action and container workflows,
  with a test ensuring it mirrors the authoritative runtime dependencies in
  `pyproject.toml`.
- Updated tox to build a wheel and source distribution, install the wheel in
  isolation with ScanCode, and exercise the generated command outside the
  source checkout.

## Step 4: GitHub Action Uses the Packaged CLI

**Branch:** `refactor/action-use-packaged-cli`\
**Commit:** `1900781` — `refactor(action): invoke packaged checker CLI`

This branch migrated the composite Action without changing its public inputs:

- Installs the Action project from `github.action_path` rather than installing
  a separate requirements file and executing `main.py` directly.
- Runs `copyright-license-checker` with the existing patch, repository name,
  mode, and proprietary-entity inputs.
- Passes `--repo-root "$GITHUB_WORKSPACE"` explicitly, preserving repository
  configuration lookup while leaving relative patch paths rooted at the Action
  step's working directory.
- Retains `GITHUB_ACTIONS=true` behavior so blocking findings still emit
  workflow annotations.
- Adds tests for the Action definition and for report/annotation parity under
  Action-style invocation.

## Step 5: Standalone CLI Documentation

**Branch:** `docs/standalone-cli-usage`\
**Commit:** `c33c0e3` — `docs(cli): document installation and standalone usage`

This branch documents both supported consumption paths:

- Local-checkout installation with `pip` or `pipx`, including the lxml
  wheel-only option and ScanCode verification.
- Patch generation and command examples for open-source and proprietary
  repositories.
- `--repo-root` behavior, `.licenseignore` Git-style patterns, and the CLI
  exit-code contract.
- A generic CI shell example that does not depend on GitHub Actions.
- The existing GitHub Action workflow guidance remains in its own README
  section.

## Verification

Each implementation stage was verified with the Python 3.12 tox suite. The
final stack builds both wheel and source distributions, verifies the packaged
console command with ScanCode installed, runs 183 unit tests with 94% coverage,
and passes Black, Flake8, and Pylint.

## Compatibility Status

The reusable CLI is complete while preserving the compatibility scaffolding
planned for the initial release:

- `python main.py` remains available for legacy callers.
- The top-level `scanner` package remains available for legacy imports.
- The GitHub Action preserves its existing inputs and behavior while using the
  installed command internally.

Removing those wrappers is intentionally deferred to the future major-release
stage of the plan.

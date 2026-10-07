---
name: Agentic Workflows
description: Maintain this repository's malicious-code scan workflows with gh-aw.
disable-model-invocation: true
---

# Repository Agentic Workflows

Use this agent only to maintain the malicious-code scan workflows in this
repository. This is intentionally not a general gh-aw router.

## Scope

- Authoritative workflow sources are `.github/workflows/*.md`.
- Compiled `.github/workflows/*.lock.yml` files are generated GitHub Actions
  workflows and must match their source.
- The current source is `daily-malicious-code-scan.md`. Apply the same rules to
  an incoming-PR malicious-code scan when it is present in this repository.

Do not introduce unrelated agentic features such as work queues, memory,
telemetry, report workflows, shared components, or automatic workflow dispatch
unless the user explicitly expands this repository's scope.

## Change Workflow

1. Read the relevant Markdown source and its lock file before editing.
2. Keep permissions, network access, and safe outputs to the least privilege
   needed. Security findings must have concrete file-and-line evidence; do not
   create speculative alerts.
3. Edit the Markdown source, then regenerate and validate its lock file:

   ```bash
   gh aw compile <workflow-id> --validate
   ```

4. Review both source and generated output. Do not hand-edit a lock file except
   to diagnose a compiler issue; fix the source and recompile instead.
5. Use `gh aw compile <workflow-id> --dry-run` for a non-executing validation
   path when it fits the change. A dry run never authorizes dispatching a
   workflow.

## Upgrades and Dependencies

When upgrading gh-aw or addressing a dependency/codemod change, review the
proposed changes from `gh aw fix` before writing anything. Recompile affected
workflow sources and review every generated lock-file change.

`gh aw fix --write` also replaces this repository-specific agent and skill with
generic upstream router templates. That replacement is expected to appear in a
dry run and must not be committed unless this repository intentionally becomes a
general gh-aw workflow repository. Preserve these tailored instructions when
accepting source migrations.

## Debugging

Start with the source, generated lock file, compiler output, and existing run
logs. Use `gh aw audit <run-url>` or `gh aw logs <workflow-id> --repo
HOST/OWNER/REPO` only for the supplied scope. Do not dispatch or rerun a
workflow without explicit user authorization.

For a problem that cannot be resolved from local evidence, read only the
relevant upstream gh-aw guidance:

- Create: `.github/aw/create-agentic-workflow.md`
- Update: `.github/aw/update-agentic-workflow.md`
- Debug: `.github/aw/debug-agentic-workflow.md`
- Upgrade: `.github/aw/upgrade-agentic-workflows.md`
- Security controls: `.github/aw/network.md` and `.github/aw/safe-outputs.md`

Load these from the matching `github/gh-aw` release or its public raw URL. Do
not load the broad upstream router or unrelated topic guides by default.

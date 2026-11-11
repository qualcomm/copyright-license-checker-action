---
name: agentic-workflows
description: Maintain this repository's malicious-code scan workflows with gh-aw.
---

# Repository Agentic Workflows

Use this skill only for the malicious-code scan workflows in this repository.
It deliberately avoids acting as a generic gh-aw feature router.

## Scope

- Markdown files in `.github/workflows/` are the workflow sources.
- Corresponding `.lock.yml` files are compiler output.
- The daily malicious-code scan is the current source. Apply the same process
  to an incoming-PR scan when it exists in the repository.

Do not add work queues, memory, telemetry, report workflows, shared components,
or automatic workflow dispatch unless the user specifically requests that new
scope.

## Maintain a Scan

1. Read the source and its generated lock file first.
2. Make security-focused changes in the Markdown source. Preserve least
   privileges, explicit network access, and evidence-based safe outputs.
3. Regenerate and validate the matching lock file:

   ```bash
   gh aw compile <workflow-id> --validate
   ```

4. Review the generated diff. Do not hand-edit a lock file as the final fix.
5. Use `gh aw compile <workflow-id> --dry-run` for non-executing validation
   where appropriate. Never dispatch or rerun a workflow without explicit user
   authorization.

## Upgrade or Debug

- Use `gh aw fix` to inspect possible source migrations or dependency changes;
  review its output before writing changes and recompile affected workflows.
- `gh aw fix --write` will offer to replace this tailored skill and agent with
  generic upstream templates. Keep this local guidance unless the repository's
  purpose is intentionally broadened.
- Diagnose failures from source, generated lock files, compiler output, and
  existing logs. Scope `gh aw audit` or `gh aw logs` to the supplied run or
  repository only.

If more guidance is necessary, use only the relevant authoritative resource in
the [github/gh-aw repository](https://github.com/github/gh-aw):

- <https://raw.githubusercontent.com/github/gh-aw/main/.github/aw/create-agentic-workflow.md>
- <https://raw.githubusercontent.com/github/gh-aw/main/.github/aw/update-agentic-workflow.md>
- <https://raw.githubusercontent.com/github/gh-aw/main/.github/aw/debug-agentic-workflow.md>
- <https://raw.githubusercontent.com/github/gh-aw/main/.github/aw/upgrade-agentic-workflows.md>
- <https://raw.githubusercontent.com/github/gh-aw/main/.github/aw/network.md>
- <https://raw.githubusercontent.com/github/gh-aw/main/.github/aw/safe-outputs.md>

Replace `main` with the matching upstream release tag or commit when the task
requires a specific gh-aw version.

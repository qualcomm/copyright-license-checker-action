---
on:
  pull_request:
    branches: [main]
    types: [opened, reopened, synchronize, ready_for_review]
    forks: ["*"]

permissions:
  copilot-requests: write
  contents: read
  pull-requests: read
  security-events: read

safe-outputs:
  create-code-scanning-alert:
    max: 20

network:
  allowed: []
---

# Pull Request Malicious Code Scan

Review only the changed files and diff of the triggering pull request for
evidence of secret exfiltration, unexpected network access, suspicious system
commands, obfuscation, hidden backdoors, or privilege escalation. Do not run,
install, or otherwise execute pull request code.

Use repository and pull request context to distinguish intentional behavior
from anomalies. Create a code scanning alert only when there is concrete file
and line evidence. Include the category, severity, evidence, likely impact,
confidence, and recommended remediation. Do not report speculative or
style-only concerns.

Use `noop` with a short reason when no concrete security issue is found,
evidence is insufficient, or a finding duplicates an existing alert.

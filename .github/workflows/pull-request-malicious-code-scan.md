---
on:
  pull_request:
    branches: [main]
    types: [opened, reopened, synchronize, ready_for_review]
    forks: ["*"]
  roles: all
  report-blocked-version: false

permissions:
  copilot-requests: write
  contents: read
  pull-requests: read
  security-events: read

timeout-minutes: 10
max-turns: 8
max-ai-credits: 100

network:
  allowed: []

tools:
  bash: false
  cli-proxy: false
  edit: false

safe-outputs:
  create-issue: false
  noop:
    report-as-issue: false
  report-failure-as-issue: false
  report-failed-jobs: false
  report-incomplete:
    create-issue: false
  report-missing-data:
    create-issue: false
  report-missing-tool:
    create-issue: false
  threat-detection:
    max-ai-credits: 50
    report-as-issue: false
---

# Pull Request Malicious Code Scan

Review only the changed files and diff of the triggering pull request for
evidence of secret exfiltration, unexpected network access, suspicious system
commands, obfuscation, hidden backdoors, or privilege escalation. Scan all
admitted contributors, including non-members from forks. Do not run, install,
or otherwise execute pull request code.

Use repository and pull request context to distinguish intentional behavior
from anomalies. Report concrete findings in the workflow output with the
category, severity, evidence, likely impact, confidence, and recommended
remediation. Do not create code scanning alerts, comments, issues, or any
other repository output; the daily post-merge scan owns alert creation. Do not
report speculative or style-only concerns.

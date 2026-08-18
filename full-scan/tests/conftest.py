import os
import sys

# Put the full-scan/ directory (this file's grandparent) at the front of
# sys.path so tests can `import scanner...` / `import full_scan` regardless of
# where pytest is invoked from, and so full-scan's scanner package wins over the
# repo-root patch-scan scanner package.
_FULL_SCAN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _FULL_SCAN)

# Also expose full-scan/scripts/ so tests can `import compare_tools` /
# `import compare_tools_remote` (the diagnostics live there and self-bootstrap
# the scanner import on load).
sys.path.insert(0, os.path.join(_FULL_SCAN, "scripts"))

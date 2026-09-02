import os
import sys

# Put the full-scan/ directory (this file's grandparent) at the front of
# sys.path so tests can `import scanner...` / `import full_scan` regardless of
# where pytest is invoked from, and so full-scan's scanner package wins over the
# repo-root patch-scan scanner package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

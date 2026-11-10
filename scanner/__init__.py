"""Compatibility namespace for the pre-packaging ``scanner`` imports."""

import sys
from pathlib import Path


_SRC_ROOT = str(Path(__file__).resolve().parents[1] / "src")
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

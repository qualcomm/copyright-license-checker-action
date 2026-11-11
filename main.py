"""Compatibility wrapper for callers that execute ``python main.py``."""

import sys
from pathlib import Path

_SRC_ROOT = str(Path(__file__).resolve().parent / "src")
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

# pylint: disable=wrong-import-position
from copyright_license_checker import cli as _cli  # noqa: E402

if __name__ == "__main__":
    _cli.legacy_main()
else:
    sys.modules[__name__] = _cli

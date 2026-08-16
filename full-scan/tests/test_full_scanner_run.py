from scanner.full_scanner import FullScanner
from scanner.licenses import PERMISSIVE_LICENSES


class _FakeRepoScan:
    """Minimal RepoScan stand-in for exercising FullScanner.run() without git or
    scancode: get_files() drives iteration and is_license_optional() picks the
    relaxed tier. root is unused because scan_files is stubbed out."""

    def __init__(self, files, optional=()):
        self._files = list(files)
        self._optional = set(optional)
        self.root = "."

    def get_files(self):
        return self._files

    def is_license_optional(self, path):
        return path in self._optional


def _scanner(files, scan_results, optional=()):
    scanner = FullScanner(_FakeRepoScan(files, optional), PERMISSIVE_LICENSES)
    # Stub the scancode invocation: run() consumes whatever scan_files returns.
    scanner.scan_files = lambda paths: scan_results
    return scanner


def test_run_incompatible_blocks():
    scanner = _scanner(["a.c"], {"a.c": {
        "license": "GPL-2.0",
        "copyrights": ["Copyright (c) 2024 Qualcomm Technologies, Inc"],
        "scan_errors": [],
    }})
    flagged, warnings = scanner.run()
    assert "Incompatible license: GPL-2.0" in flagged["a.c"]["license_issues"]
    assert "a.c" not in warnings


def test_run_missing_copyright_blocks():
    scanner = _scanner(["a.c"], {"a.c": {
        "license": "MIT",          # permissive -> no license issue
        "copyrights": [],          # ...but no copyright at all -> blocking
        "scan_errors": [],
    }})
    flagged, warnings = scanner.run()
    assert flagged["a.c"]["license_issues"] == []
    assert "No copyright statement found" in flagged["a.c"]["copyright_issues"]


def test_run_scan_errors_suppress_copyright():
    # A scancode per-file error makes copyright "unknown", not "absent": the
    # missing-copyright block is suppressed, but license classification still runs.
    scanner = _scanner(["a.c"], {"a.c": {
        "license": "GPL-2.0",
        "copyrights": [],
        "scan_errors": ["boom"],
    }})
    flagged, warnings = scanner.run()
    assert "Incompatible license: GPL-2.0" in flagged["a.c"]["license_issues"]
    assert flagged["a.c"]["copyright_issues"] == []


def test_run_optional_incompatible_still_blocks():
    # License-optional build files (.mk/.bp) skip missing-header/missing-copyright,
    # but an incompatible license they DO carry is still a blocking error.
    scanner = _scanner(
        ["Android.mk"],
        {"Android.mk": {"license": "GPL-2.0", "copyrights": [], "scan_errors": []}},
        optional=["Android.mk"],
    )
    flagged, warnings = scanner.run()
    assert "Incompatible license: GPL-2.0" in flagged["Android.mk"]["license_issues"]
    # No missing-copyright finding despite empty copyrights (relaxed tier).
    assert flagged["Android.mk"]["copyright_issues"] == []


def test_run_optional_missing_license_is_clean():
    # A license-optional file with NO license and NO copyright is not flagged.
    scanner = _scanner(
        ["Android.mk"],
        {"Android.mk": {"license": None, "copyrights": [], "scan_errors": []}},
        optional=["Android.mk"],
    )
    flagged, warnings = scanner.run()
    assert flagged == {}
    assert warnings == {}

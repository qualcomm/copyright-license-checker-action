# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Allowed-license lists for the full-repository scan.

Copied verbatim from the patch-scan entry point (main.py) so the full-scan path
is self-contained and imports nothing from the patch scan. The patch scan owns
its own copy; keep these in sync if that list changes.
"""

# Permissive licenses (and known-permissive LicenseRef ids / compound expressions).
PERMISSIVE_LICENSES = [
    "BSD-3-Clause",
    "MIT",
    "Apache-1.0",
    "Apache-1.1",
    "Apache-2.0",
    "BSD-3-Clause-Clear",
    "FreeBSD-DOC",
    "Zlib",
    "BSD-1-Clause",
    "BSD-2-Clause",
    "BSD-2-Clause-first-lines",
    "BSD-2-Clause-Views",
    "BSD-3-Clause-Sun",
    "BSD-4-Clause-Shortened",
    "BSD-3-Clause-Attribution",
    "BSD-4-Clause",
    "ISC",
    "CC0-1.0",
    "ICU",
    "LicenseRef-scancode-unicode",
    "Apache-2.0 WITH LLVM-exception",
    "Apache-2.0 WITH LLVM-exception AND Apache-2.0 AND LLVM-exception",
]

COPYLEFT_LICENSES = [
    "GPL-1.0-only",
    "GPL-1.0-or-later",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0",
    "GPL-3.0-or-later",
    "AGPL-3.0",
    "LGPL-3.0",
    "GPL-2.0",
    "GPL-2.0+",
    "GPL-2.0-only WITH Linux-syscall-note",
    "AGPL-1.0-only",
    "AGPL-1.0-or-later",
    "LicenseRef-scancode-agpl-2.0",
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
]

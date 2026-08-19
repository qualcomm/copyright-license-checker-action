"""
Shared patch-file fixtures for the test suite.

Each constant is a minimal but realistic ``git diff`` fragment. The Patch parser
splits on ``diff .* b/<path>`` headers and on ``+++ ...`` to find file content,
so fixtures must include both to be parsed as real changes.
"""

# A modified source file that adds a copyright line and keeps the license.
MODIFIED_WITH_ADDED_COPYRIGHT = """diff --git a/src/foo.c b/src/foo.c
index 1234567..89abcde 100644
--- a/src/foo.c
+++ b/src/foo.c
@@ -1,4 +1,5 @@
 /*
+ * Copyright (c) 2024 Qualcomm Technologies, Inc. and/or its subsidiaries.
  * SPDX-License-Identifier: BSD-3-Clause-Clear
  */
 int foo(void) { return 0; }
"""

# A modified source file where an existing copyright is deleted outright.
MODIFIED_WITH_DELETED_COPYRIGHT = """diff --git a/src/bar.c b/src/bar.c
index 1234567..89abcde 100644
--- a/src/bar.c
+++ b/src/bar.c
@@ -1,4 +1,3 @@
 /*
- * Copyright (c) 2019 Some Other Author. All rights reserved.
  * SPDX-License-Identifier: BSD-3-Clause-Clear
  */
 int bar(void) { return 0; }
"""

# The sanctioned QUIC -> QTI rebrand transition.
MODIFIED_QUIC_TO_QTI_TRANSITION = """diff --git a/src/rebrand.c b/src/rebrand.c
index 1234567..89abcde 100644
--- a/src/rebrand.c
+++ b/src/rebrand.c
@@ -1,4 +1,4 @@
 /*
- * Copyright (c) 2022 Qualcomm Innovation Center, Inc. All rights reserved.
+ * Copyright (c) 2024 Qualcomm Technologies, Inc. and/or its subsidiaries.
  * SPDX-License-Identifier: BSD-3-Clause-Clear
  */
"""

# A brand new source file (new file mode -> ADDED).
ADDED_SOURCE_FILE = """diff --git a/src/new_module.c b/src/new_module.c
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/src/new_module.c
@@ -0,0 +1,4 @@
+/*
+ * Copyright (c) 2024 Qualcomm Technologies, Inc. and/or its subsidiaries.
+ */
+int new_module(void) { return 0; }
"""

# A deleted source file (deleted file mode -> DELETED).
DELETED_SOURCE_FILE = """diff --git a/src/gone.c b/src/gone.c
deleted file mode 100644
index 1234567..0000000
--- a/src/gone.c
+++ /dev/null
@@ -1,3 +0,0 @@
-/*
- * Copyright (c) 2019 Someone Else.
- */
"""

# A renamed file with no content change.
RENAMED_SOURCE_FILE = """diff --git a/src/old_name.c b/src/new_name.c
similarity index 100%
rename from src/old_name.c
rename to src/new_name.c
"""

# A binary file change.
BINARY_FILE = """diff --git a/assets/logo.png b/assets/logo.png
new file mode 100644
index 0000000..1234567
GIT binary patch
literal 8
LcmZQzU|?Wm0Ac}f

"""

# Files whose extensions are unconditionally skipped by the parser.
EXCLUDED_EXTENSIONS = """diff --git a/README.md b/README.md
index 1234567..89abcde 100644
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
 # Title
+Some new documentation line.
diff --git a/recipe.bb b/recipe.bb
index 1234567..89abcde 100644
--- a/recipe.bb
+++ b/recipe.bb
@@ -1 +1,2 @@
 SUMMARY = "thing"
+LICENSE = "MIT"
diff --git a/data.json b/data.json
index 1234567..89abcde 100644
--- a/data.json
+++ b/data.json
@@ -1 +1,2 @@
 {}
+{"a": 1}
diff --git a/ci.yml b/ci.yml
index 1234567..89abcde 100644
--- a/ci.yml
+++ b/ci.yml
@@ -1 +1,2 @@
 on: push
+jobs: {}
diff --git a/fix.patch b/fix.patch
index 1234567..89abcde 100644
--- a/fix.patch
+++ b/fix.patch
@@ -1 +1,2 @@
 old
+new
"""

# --- Regression-harness fixtures (see tests/test_regression_snapshot.py) ---
# One file per patch so each maps to scancode detection keys "0_added.txt" /
# "0_deleted.txt" -- the batch scanner indexes by position within the
# patch's own source-file list, which is always a single entry here.

# A pure addition: no deleted lines, so only "0_added.txt" is scanned.
ADDITION_ONLY = """diff --git a/src/module.c b/src/module.c
index 1234567..89abcde 100644
--- a/src/module.c
+++ b/src/module.c
@@ -1,2 +1,3 @@
 int module(void) {
+    /* newly added block */
     return 0;
"""

# A pure deletion: no added lines, so only "0_deleted.txt" is scanned.
DELETION_ONLY = """diff --git a/src/utils.py b/src/utils.py
index 1234567..89abcde 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -1,3 +1,2 @@
 def utils():
-    # license text being removed
     return None
"""

# Both an addition and a deletion in the same file, so both "0_added.txt"
# and "0_deleted.txt" are scanned.
ADDITION_AND_DELETION = """diff --git a/src/core.cpp b/src/core.cpp
index 1234567..89abcde 100644
--- a/src/core.cpp
+++ b/src/core.cpp
@@ -1,3 +1,3 @@
 int core() {
-    // old license text
+    // new license text
     return 0;
"""

# A new source file with no license header at all.
NEW_FILE_NO_LICENSE = """diff --git a/src/new_feature.py b/src/new_feature.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/src/new_feature.py
@@ -0,0 +1,2 @@
+def new_feature():
+    return None
"""

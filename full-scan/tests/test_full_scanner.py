# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import pytest

from scanner.full_scanner import (
    FullScanner,
    confident_license_expression,
    copyright_matches_expected,
)
from scanner.licenses import PERMISSIVE_LICENSES


def make_scanner():
    # classify_license / is_expression_permissive use only the permissive list;
    # the RepoScan argument is unused by those methods, so None is fine here.
    return FullScanner(None, PERMISSIVE_LICENSES)


@pytest.mark.parametrize("expression, expected", [
    ("BSD-3-Clause-Clear", "ok"),                    # plain permissive
    ("MIT OR GPL-2.0", "ok"),                         # OR group: a permissive option is enough
    ("GPL-2.0 OR MIT", "ok"),                         # order-independent
    ("(MIT OR Apache-2.0) AND BSD-3-Clause", "ok"),   # every AND group permissive
    ("GPL-2.0", "error"),                             # concrete copyleft
    ("(MIT OR Apache-2.0) AND GPL-2.0", "error"),     # regression: trailing AND MUST be evaluated
    ("(MIT OR GPL-2.0) AND GPL-3.0-only", "error"),   # leading OR ok, trailing AND disallowed
])
def test_classify_license(expression, expected):
    assert make_scanner().classify_license(expression) == expected


def test_lone_proprietary_is_error():
    # A lone proprietary marker always blocks.
    assert make_scanner().classify_license(
        "LicenseRef-scancode-proprietary-license") == "error"


def test_only_uncertain_is_warning():
    # An unknown LicenseRef-scancode-* not in the permissive list, alone, warns.
    assert make_scanner().classify_license(
        "LicenseRef-scancode-unknown-license-reference") == "warning"


# --- confident_license_expression: the bare-word noise filter -----------------
# (MIN_CONFIDENT_MATCH_LENGTH: drop short bare-word license references scancode
# merged into a real detection; keep SPDX tags and matches >= 3 tokens.)

def _detection(*matches):
    """Wrap raw scancode match dicts into a single-detection file_result."""
    return {"license_detections": [{"matches": list(matches)}]}


def test_confident_expression_drops_short_bareword():
    # A real BSD header (long match) sits next to a 2-token "gpl" bare word that
    # scancode merged in. The short reference must be dropped so it does not
    # manufacture a false "incompatible license" -- the canonical case this
    # whole filter exists to prevent.
    file_result = _detection(
        {"matcher": "2-hash", "matched_length": 120,
         "spdx_license_expression": "BSD-3-Clause-Clear"},
        {"matcher": "3-seq", "matched_length": 2,
         "spdx_license_expression": "GPL-1.0-or-later"},
    )
    assert confident_license_expression(file_result) == "BSD-3-Clause-Clear"


def test_confident_expression_keeps_spdx_tag():
    # An explicit SPDX-License-Identifier tag is kept regardless of token length
    # (matched_length 1), so a real one-line SPDX header is never dropped.
    file_result = _detection(
        {"matcher": "1-spdx-id", "matched_length": 1,
         "spdx_license_expression": "MIT"},
    )
    assert confident_license_expression(file_result) == "MIT"


def test_confident_expression_none_when_all_noise():
    # When every match is short bare-word noise, nothing confident remains, so
    # the expression is None -- which run() reads as "no license header found".
    file_result = _detection(
        {"matcher": "2-hash", "matched_length": 2,
         "spdx_license_expression": "GPL-1.0-or-later"},
    )
    assert confident_license_expression(file_result) is None
    assert confident_license_expression({}) is None


# --- copyright_matches_expected: the expected-holder pattern ------------------

def test_copyright_lf_year_cap():
    # The Linux-Foundation branch intentionally covers only years 2012-2022
    # (kept byte-for-byte identical to the repolinter ruleset). A regression that
    # widened the window would silently accept third-party LF copyrights, so pin
    # the boundaries.
    assert copyright_matches_expected(["Copyright (c) 2018 The Linux Foundation"]) is True
    assert copyright_matches_expected(["Copyright (c) 2024 The Linux Foundation"]) is False
    assert copyright_matches_expected(["Copyright (c) 2011 The Linux Foundation"]) is False


def test_copyright_qti_matches_and_thirdparty_does_not():
    # "Qualcomm Technologies, Inc" matches anywhere in a statement; an unrelated
    # third-party holder does not (that mismatch becomes the non-blocking warning).
    assert copyright_matches_expected(
        ["Copyright (c) 2024 Qualcomm Technologies, Inc. and/or its subsidiaries"]) is True
    assert copyright_matches_expected(["Copyright 2020 Google LLC"]) is False

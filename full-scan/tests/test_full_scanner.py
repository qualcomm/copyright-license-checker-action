# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import pytest

from scanner.full_scanner import (
    MATCH_SPDX_EXPRESSION_KEYS,
    FullScanner,
    confident_license_expression,
    copyright_matches_expected,
    expression_allowed_by,
    match_spdx_expression,
)
from scanner.licenses import PERMISSIVE_LICENSES, COPYLEFT_LICENSES


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


# --- expression_allowed_by: the shared AND/OR allow-list evaluator ------------
# (Same kernel behind per-file classification AND full_scan's repo bucket
# selection, so a compound all-permissive repo license selects the full
# permissive set instead of a singleton bucket that would flag every file.)

@pytest.mark.parametrize("expression, allowed, expected", [
    ("BSD-3-Clause-Clear", PERMISSIVE_LICENSES, True),                   # single permissive
    ("BSD-3-Clause-Clear AND BSD-3-Clause", PERMISSIVE_LICENSES, True),  # compound all-permissive
    ("MIT OR GPL-2.0", PERMISSIVE_LICENSES, True),                       # OR: one permissive option suffices
    ("(MIT OR Apache-2.0) AND GPL-2.0", PERMISSIVE_LICENSES, False),     # trailing AND disallowed
    ("GPL-2.0-only", PERMISSIVE_LICENSES, False),                        # copyleft not in permissive
    ("GPL-2.0-only AND GPL-3.0-only", COPYLEFT_LICENSES, True),          # compound all-copyleft vs copyleft list
    ("BSD-3-Clause AND GPL-2.0-only", PERMISSIVE_LICENSES, False),       # mixed: not all permissive
    ("BSD-3-Clause AND GPL-2.0-only", COPYLEFT_LICENSES, False),         # mixed: not all copyleft
])
def test_expression_allowed_by(expression, allowed, expected):
    assert expression_allowed_by(expression, allowed) is expected


def test_is_expression_permissive_delegates():
    # The method is a thin wrapper over the module-level kernel, bound to the
    # scanner's permissive list -- so the two must agree.
    for expr in ("BSD-3-Clause-Clear AND BSD-3-Clause", "(MIT OR Apache-2.0) AND GPL-2.0"):
        assert (make_scanner().is_expression_permissive(expr)
                == expression_allowed_by(expr, PERMISSIVE_LICENSES))


# --- confident_license_expression: the bare-word noise filter -----------------
# (MIN_CONFIDENT_MATCH_LENGTH: drop short bare-word license references scancode
# merged into a real detection; keep SPDX tags and matches >= 3 tokens.)
#
# Every case below runs TWICE, once per name scancode has used for the per-match
# SPDX field ('spdx_license_expression' up to output format 3.2.0 / scancode
# 32.2.x, 'license_expression_spdx' from format 4.1.0 / scancode 32.5.0). The
# rename was silent and its failure mode is total -- an unrecognized key drops
# every match, so every file reports "no license header found" and the repo
# baseline aborts as undetected -- and the old single-shape mocks here passed
# right through it. Parametrizing pins BOTH shapes so the next rename fails the
# suite instead of CI.

def _detection(spdx_key, *matches):
    """
    Wrap raw scancode match dicts into a single-detection file_result.

    Each match is given as {'matcher', 'matched_length', 'spdx'}; the neutral
    'spdx' value is emitted under `spdx_key`, so one test body covers both
    schema versions.
    """
    renamed = []
    for match in matches:
        match = dict(match)
        match[spdx_key] = match.pop('spdx')
        renamed.append(match)
    return {"license_detections": [{"matches": renamed}]}


@pytest.mark.parametrize("spdx_key", MATCH_SPDX_EXPRESSION_KEYS)
def test_confident_expression_drops_short_bareword(spdx_key):
    # A real BSD header (long match) sits next to a 2-token "gpl" bare word that
    # scancode merged in. The short reference must be dropped so it does not
    # manufacture a false "incompatible license" -- the canonical case this
    # whole filter exists to prevent.
    file_result = _detection(
        spdx_key,
        {"matcher": "2-hash", "matched_length": 120, "spdx": "BSD-3-Clause-Clear"},
        {"matcher": "3-seq", "matched_length": 2, "spdx": "GPL-1.0-or-later"},
    )
    assert confident_license_expression(file_result) == "BSD-3-Clause-Clear"


@pytest.mark.parametrize("spdx_key", MATCH_SPDX_EXPRESSION_KEYS)
def test_confident_expression_keeps_spdx_tag(spdx_key):
    # An explicit SPDX-License-Identifier tag is kept regardless of token length
    # (matched_length 1), so a real one-line SPDX header is never dropped.
    file_result = _detection(
        spdx_key,
        {"matcher": "1-spdx-id", "matched_length": 1, "spdx": "MIT"},
    )
    assert confident_license_expression(file_result) == "MIT"


@pytest.mark.parametrize("spdx_key", MATCH_SPDX_EXPRESSION_KEYS)
def test_confident_expression_none_when_all_noise(spdx_key):
    # When every match is short bare-word noise, nothing confident remains, so
    # the expression is None -- which run() reads as "no license header found".
    file_result = _detection(
        spdx_key,
        {"matcher": "2-hash", "matched_length": 2, "spdx": "GPL-1.0-or-later"},
    )
    assert confident_license_expression(file_result) is None
    assert confident_license_expression({}) is None


# --- match_spdx_expression: the cross-version field read ----------------------

def test_confident_expression_reads_real_scancode_32_5_match():
    # A match captured VERBATIM from scancode 32.5.0 (the pinned version), field
    # names and all, so the parser is pinned against the real payload and not
    # only against hand-written mocks -- the hand-written ones are exactly what
    # let the 4.1.0 rename through. The 32.2.1 match for the same file is
    # byte-identical except for the SPDX field name.
    file_result = {"license_detections": [{"matches": [{
        "license_expression": "clear-bsd",
        "license_expression_spdx": "BSD-3-Clause-Clear",
        "from_file": "bsd_spdx.c",
        "start_line": 2,
        "end_line": 2,
        "matcher": "1-spdx-id",
        "score": 100.0,
        "matched_length": 7,
        "match_coverage": 100.0,
        "rule_relevance": 100,
        "rule_identifier": "spdx-license-identifier-clear_bsd-7650404303",
        "rule_url": None,
    }]}]}
    assert confident_license_expression(file_result) == "BSD-3-Clause-Clear"


def test_match_spdx_expression_prefers_newest_key():
    # Both keys present (no scancode emits this, but the lookup order is the
    # contract): the newest name wins, so a future scancode that keeps the old
    # key as a deprecated alias is read from the current field.
    assert match_spdx_expression({
        "license_expression_spdx": "MIT",
        "spdx_license_expression": "Apache-2.0",
    }) == "MIT"


def test_match_spdx_expression_none_when_absent_or_empty():
    # No known key, or a present-but-empty value, yields None -- the match is
    # then dropped by confident_license_expression. If scancode ever renames the
    # field again this is the behavior that makes the scan report "no license
    # header found" everywhere, so add the new name to
    # MATCH_SPDX_EXPRESSION_KEYS rather than letting it degrade silently.
    assert match_spdx_expression({"matcher": "1-spdx-id"}) is None
    assert match_spdx_expression({"license_expression_spdx": ""}) is None
    assert match_spdx_expression({"some_future_spdx_field": "MIT"}) is None


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

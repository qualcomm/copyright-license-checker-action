import pytest

from scanner.full_scanner import FullScanner
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

import io
import os
import re
import sys
import json
import socket
import logging
import functools
import webbrowser
import contextlib
import subprocess
from datetime import datetime
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import click

# This file lives in <action-repo>/scripts/, one level below the repo root where
# main.py and the scanner package live. Put the repo root on sys.path so
# `python scripts/compare_tools.py ...` resolves those imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pylint: disable=wrong-import-position
from main import get_license, PERMISSIVE_LICENSES, COPYLEFT_LICENSES
from scanner.full_repo import (
    RepoScan,
    SOURCE_FILE_EXTENSIONS,
    LICENSE_OPTIONAL_EXTENSIONS,
)
from scanner.full_scanner import FullScanner

"""
Compare repolinter against our full-repo license/copyright scanner.

This is a read-only DIAGNOSTIC (not a CI gate). It runs both tools over the same
repository and renders a self-contained, interactive HTML report showing where the
two agree and where they diverge:

    * repolinter (todogroup/repolinter via docker) with the Qualcomm ruleset --
      a broad repo-health linter that, among many rules, checks that source files
      carry a license/copyright HEADER.
    * our full_scan -- a per-file license/copyright scanner that additionally
      evaluates license COMPATIBILITY (e.g. GPL under a BSD repo) and AND/OR
      SPDX expressions, which repolinter does not do.

The report covers ALL repolinter rules, but the per-file license/copyright overlap
is the centerpiece: it is the evidence for eventually retiring repolinter's
license/copyright rules in favour of full_scan.

Usage:
    python scripts/compare_tools.py <repo_name> [--repo-path PATH] [--include-untracked]
                                    [--include-licenseignore] [--ruleset-url URL]
                                    [--repolinter-json FILE] [--output FILE]
                                    [--open] [--port N] [--verbose]

    The report is ALWAYS served over HTTP on 0.0.0.0:8000 (change the port with
    --port) until Ctrl-C, so it can be viewed from other machines.

    repo_name -- github "owner/repo"; required because full_scan resolves the
                 repo license from it (see main.get_license).

    By default the report is written to
    <action-repo>/reports/<project>_<YYYYMMDD-HHMMSS>.html (the reports/ dir is
    created on demand), so older reports are retained and easy to track. Override
    with --output.

For the org-wide, multi-repo version that enumerates GitHub orgs and shallow-clones
each repo automatically, see scripts/compare_tools_remote.py.

Runtime dependencies: `docker` and `scancode` must be on PATH (unless
--repolinter-json is used, which skips docker).
"""

LOG_PREFIX = "< repolinter vs full-scan comparison >"

# This script lives in <action-repo>/scripts/, so the action repo root is its
# parent's parent. Reports default to a `reports/` dir at that root (created on
# demand), independent of the scanned repo (--repo-path) or the cwd.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(REPO_ROOT, "reports")

REPOLINTER_IMAGE = "ghcr.io/todogroup/repolinter:v0.11.2"
DEFAULT_RULESET_URL = (
    "https://raw.githubusercontent.com/qualcomm/.github/main/repolint-qcom.json"
)

# The three per-file "does a source file carry a license/copyright header" rules
# in the effective (base + qcom) ruleset -- the only repolinter rules with a
# per-source-file analog to our scanner. name -> level. Everything else repolinter
# reports is repo-level (LICENSE-file-exists, README, CI, package metadata, ...)
# and is shown as context only.
HEADER_RULES = {
    "source-license-headers-exist": "error",       # copyright + (SPDX | BSD text); no .sh
    "source-qualcomm-license-headers-exist": "warning",  # Qualcomm/LF copyright; + .sh
    "qualcomm-source-license-headers-exist": "error",    # Qualcomm/LF copyright; + .sh (child)
}

# Short display codes for the header rules (used in the compact grid + legend).
RULE_CODES = {
    "source-license-headers-exist": "SLH",
    "qualcomm-source-license-headers-exist": "QSLH",
    "source-qualcomm-license-headers-exist": "SQLH",
}

# What each tool's per-file header analysis actually covers, for scope labeling.
SCANNER_EXTS = set(SOURCE_FILE_EXTENSIONS) | set(LICENSE_OPTIONAL_EXTENSIONS)
REPOLINTER_HEADER_EXTS = {
    ".js", ".c", ".cc", ".cpp", ".h", ".hpp", ".ts", ".sh",
    ".rs", ".java", ".go", ".bbclass", ".S",
}

# The fixed messages FullScanner emits, mapped to stable codes so downstream
# logic never string-matches raw messages. prefix -> (kind, severity, category).
_SCANNER_MESSAGE_MAP = (
    ("No license header found", ("NOLIC", "error", "license")),
    ("Incompatible license:", ("INCOMPAT", "error", "license")),
    ("Uncertain license, review manually:", ("UNCERT", "warning", "license")),
    ("No copyright statement found", ("NOCR", "error", "copyright")),
    ("Copyright holder does not match", ("CRHOLDER", "warning", "copyright")),
)


class RepolinterUnavailable(Exception):
    """Raised when repolinter could not be run or produced no usable JSON."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class ScannerFinding:
    """One full_scan finding on a file, parsed into a stable code."""
    kind: str       # NOLIC | INCOMPAT | UNCERT | NOCR
    severity: str   # error | warning
    category: str   # license | copyright
    detail: str     # the raw scanner message


@dataclass
class RepolinterFinding:
    """One header-rule outcome for a file (pass or fail)."""
    rule_name: str
    code: str       # SLH | QSLH | SQLH
    level: str      # error | warning
    passed: bool
    message: str = None


@dataclass
class RepoLevelResult:
    """A non-header repolinter rule outcome (context only)."""
    rule_name: str
    level: str      # error | warning | off
    rule_type: str
    status: str     # PASSED | NOT_PASSED_ERROR | NOT_PASSED_WARN | IGNORED | ERROR
    message: str = None


@dataclass
class ScannerView:
    """Normalized full_scan results."""
    license: str
    per_file: dict = field(default_factory=dict)     # path -> [ScannerFinding]
    scanned_paths: set = field(default_factory=set)   # everything RepoScan surfaced
    ignored_paths: set = field(default_factory=set)   # files .licenseignore excluded
    flagged_count: int = 0
    warning_count: int = 0


@dataclass
class RepolinterView:
    """Normalized repolinter results."""
    per_file: dict = field(default_factory=dict)      # path -> [RepolinterFinding] (pass+fail)
    evaluated_paths: set = field(default_factory=set)  # union of header-rule target paths
    repo_level: list = field(default_factory=list)     # [RepoLevelResult]
    errored: bool = False
    err_msg: str = None
    top_passed: bool = True


@dataclass
class ComparisonRecord:
    """One row of the per-file license/copyright comparison."""
    path: str
    ext: str
    category: str   # BOTH | ONLY_FULL_SCAN | ONLY_REPOLINTER
    scanner_findings: list = field(default_factory=list)
    repolinter_findings: list = field(default_factory=list)   # all header outcomes
    tags: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Running the two tools
# --------------------------------------------------------------------------- #

def normalize_path(path: str) -> str:
    """
    Canonicalize a repo-relative path so the two tools' paths match.

    Both tools emit repo-relative, forward-slash paths; the only observed skew is
    a possible leading './' or (when a target is reported mount-absolute) a
    '/src/' prefix, so strip those defensively.

    Args:
        path (str): A path from either tool.

    Returns:
        str: The canonical repo-relative path.
    """
    path = (path or "").strip().replace("\\", "/")
    for prefix in ("/src/", "./"):
        if path.startswith(prefix):
            path = path[len(prefix):]
    return path


def _ext_of(path: str) -> str:
    """Return the file extension (with dot) of a path, or '' if none."""
    _, ext = os.path.splitext(path)
    return ext


def run_full_scan(repo_name: str, repo_path: str, include_untracked: bool,
                  verbose: bool = False, include_licenseignore: bool = False) -> tuple:
    """
    Run our full-repo scanner in-process and return structured results.

    Replicates full_scan.main (resolve license -> allowed set -> RepoScan ->
    FullScanner.run) WITHOUT its sys.exit. get_license and RepoScan are
    cwd-relative, so we chdir into repo_path and restore cwd in a finally (chdir
    is process-global and full_scan never restores it). get_license prints to
    stdout, so its output is captured (echoed to stderr only under --verbose).

    Args:
        repo_name (str): The github "owner/repo" (for license resolution).
        repo_path (str): Absolute path to the repository working tree.
        include_untracked (bool): Also scan untracked-but-not-ignored files.
        verbose (bool): Echo captured get_license chatter to stderr.
        include_licenseignore (bool): Also scan files matched by .licenseignore.

    Returns:
        tuple: (license, flagged_files, warning_files, scanned_paths,
                ignored_paths). ignored_paths are source files .licenseignore
                excluded (recorded even when include_licenseignore is True).
    """
    prev_cwd = os.getcwd()
    captured = io.StringIO()
    try:
        os.chdir(repo_path)
        with contextlib.redirect_stdout(captured):
            license_id = get_license(repo_name)
        if license_id in PERMISSIVE_LICENSES:
            allowed_licenses = PERMISSIVE_LICENSES
        elif license_id in COPYLEFT_LICENSES:
            allowed_licenses = COPYLEFT_LICENSES
        else:
            allowed_licenses = [license_id]

        repo_scan = RepoScan(include_untracked=include_untracked,
                             include_licenseignore=include_licenseignore)
        scanned_paths = set(repo_scan.get_files())
        ignored_paths = set(repo_scan.get_ignored_files())
        flagged_files, warning_files = FullScanner(repo_scan, allowed_licenses).run()
    finally:
        os.chdir(prev_cwd)

    if verbose and captured.getvalue().strip():
        print(captured.getvalue().strip(), file=sys.stderr)

    return license_id, flagged_files, warning_files, scanned_paths, ignored_paths


def build_repolinter_cmd(repo_path: str, ruleset_url: str) -> list:
    """
    Build the docker command that runs repolinter and prints JSON to stdout.

    Uses an absolute bind mount, drops '-t' (a TTY would pollute stdout so the
    JSON could not be parsed), and adds --format json --dryRun (guaranteed
    no-write) and --rm (container hygiene).

    Args:
        repo_path (str): Absolute path to the repository to scan.
        ruleset_url (str): URL of the repolinter ruleset.

    Returns:
        list: The argv for subprocess.run.
    """
    return [
        "docker", "run", "--rm",
        "-v", f"{repo_path}:/src", "-w", "/src",
        REPOLINTER_IMAGE,
        "--format", "json", "--dryRun",
        "--rulesetUrl", ruleset_url,
    ]


def run_repolinter(repo_path: str, ruleset_url: str,
                   repolinter_json: str = None) -> dict:
    """
    Run repolinter (or load a pre-saved result) and return the parsed LintResult.

    repolinter exits non-zero when an error-level rule fails, which is normal, so
    we do NOT use check=True and we parse stdout regardless of exit code. Node
    deprecation warnings go to stderr, so stdout should be exactly one JSON doc.

    Args:
        repo_path (str): Absolute path to the repository to scan.
        ruleset_url (str): URL of the repolinter ruleset.
        repolinter_json (str): If given, read+parse this file and skip docker.

    Returns:
        dict: The parsed repolinter LintResult.

    Raises:
        RepolinterUnavailable: docker missing/unreachable or no parseable JSON.
    """
    if repolinter_json:
        try:
            with open(repolinter_json, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RepolinterUnavailable(
                f"could not read --repolinter-json '{repolinter_json}': {exc}"
            ) from exc

    try:
        proc = subprocess.run(
            build_repolinter_cmd(repo_path, ruleset_url),
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as exc:
        raise RepolinterUnavailable(
            "docker executable not found on PATH. Install docker, or pass "
            "--repolinter-json with a pre-saved repolinter --format json result."
        ) from exc

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        raise RepolinterUnavailable(
            f"repolinter did not produce parseable JSON (docker exit "
            f"{proc.returncode}).\nstderr: {stderr[:800]}\nstdout: {stdout[:400]}"
        ) from exc


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #

def _parse_scanner_message(message: str):
    """Map a raw scanner message to (kind, severity, category), or None."""
    for prefix, triple in _SCANNER_MESSAGE_MAP:
        if message.startswith(prefix):
            return triple
    return None


def normalize_scanner(license_id: str, flagged_files: dict, warning_files: dict,
                      scanned_paths: set, ignored_paths: set = None) -> ScannerView:
    """
    Fold full_scan's flagged + warning dicts into a per-file ScannerFinding view.

    Args:
        license_id (str): The resolved repo license.
        flagged_files (dict): Blocking findings from FullScanner.run.
        warning_files (dict): Non-blocking findings from FullScanner.run.
        scanned_paths (set): All paths RepoScan surfaced (the scanner's scope).
        ignored_paths (set): Paths .licenseignore excluded (for divergence tags).

    Returns:
        ScannerView: The normalized view.
    """
    view = ScannerView(
        license=license_id,
        scanned_paths={normalize_path(p) for p in scanned_paths},
        ignored_paths={normalize_path(p) for p in (ignored_paths or set())},
        flagged_count=len(flagged_files),
        warning_count=len(warning_files),
    )
    for source in (flagged_files, warning_files):
        for path, issues in source.items():
            key = normalize_path(path)
            findings = view.per_file.setdefault(key, [])
            for message in issues.get("license_issues", []) + issues.get("copyright_issues", []):
                parsed = _parse_scanner_message(message)
                if parsed is None:
                    continue
                kind, severity, category = parsed
                findings.append(ScannerFinding(kind, severity, category, message))
    return view


def normalize_repolinter(lint_result: dict) -> RepolinterView:
    """
    Fold a repolinter LintResult into per-file header outcomes + repo-level rules.

    Guards the fact that a FormatResult omits 'lintResult' entirely when its
    status is IGNORED or ERROR.

    Args:
        lint_result (dict): The parsed repolinter LintResult.

    Returns:
        RepolinterView: The normalized view.
    """
    view = RepolinterView(
        errored=bool(lint_result.get("errored")),
        err_msg=lint_result.get("errMsg"),
        top_passed=bool(lint_result.get("passed")),
    )

    for result in lint_result.get("results", []):
        info = result.get("ruleInfo", {})
        name = info.get("name", "<unknown>")
        level = info.get("level", "off")
        rule_type = info.get("ruleType", "")
        status = result.get("status", "")
        lint = result.get("lintResult")

        if name in HEADER_RULES:
            # Per-file header rule: record each target's pass/fail.
            if not lint:
                continue
            code = RULE_CODES.get(name, name)
            for target in lint.get("targets", []):
                path = target.get("path")
                if not path:
                    continue
                key = normalize_path(path)
                view.evaluated_paths.add(key)
                view.per_file.setdefault(key, []).append(
                    RepolinterFinding(
                        rule_name=name,
                        code=code,
                        level=level,
                        passed=bool(target.get("passed")),
                        message=target.get("message"),
                    )
                )
        else:
            # Repo-level (or non-header per-file) rule: context only.
            message = lint.get("message") if lint else result.get("runMessage")
            view.repo_level.append(
                RepoLevelResult(name, level, rule_type, status, message)
            )
    return view


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #

def _compute_tags(record: ComparisonRecord, scanner_scope: set,
                  include_untracked: bool, ignored_scope: set = None) -> list:
    """Compute human-readable divergence tags for one comparison record."""
    tags = []
    ignored_scope = ignored_scope or set()
    scanner_kinds = {f.kind for f in record.scanner_findings}
    failed_rl = [f for f in record.repolinter_findings if not f.passed]
    failed_rules = {f.rule_name for f in failed_rl}

    # Compatibility is unique to full_scan -- repolinter checks header presence
    # only, never license compatibility, so INCOMPAT/UNCERT can never match.
    if scanner_kinds & {"INCOMPAT", "UNCERT"}:
        tags.append("compatibility (full_scan-only)")

    if "NOLIC" in scanner_kinds and "source-license-headers-exist" in failed_rules:
        tags.append("missing-header agreement")
    if "NOCR" in scanner_kinds and failed_rules & {
        "source-qualcomm-license-headers-exist", "qualcomm-source-license-headers-exist"
    }:
        tags.append("missing-copyright agreement")
    # full_scan's copyright-holder warning now covers repolinter's Qualcomm-copyright
    # rules -- when both fire, they agree the holder is not Qualcomm/LF.
    if "CRHOLDER" in scanner_kinds and failed_rules & {
        "source-qualcomm-license-headers-exist", "qualcomm-source-license-headers-exist"
    }:
        tags.append("copyright-holder agreement")

    if record.category == "ONLY_FULL_SCAN" and record.ext in (
        SCANNER_EXTS - REPOLINTER_HEADER_EXTS
    ):
        tags.append(f"scope: {record.ext} only scanned by full_scan")
    if record.category == "ONLY_REPOLINTER" and record.ext in (
        REPOLINTER_HEADER_EXTS - SCANNER_EXTS
    ):
        tags.append(f"scope: {record.ext} only scanned by repolinter")

    # ONLY_REPOLINTER files full_scan never scanned: distinguish *why* full_scan
    # did not see them -- excluded by the repo's .licenseignore (full_scan honors
    # it, repolinter does not) vs genuinely untracked. Both are "not in scope",
    # so check the .licenseignore set first.
    if record.category == "ONLY_REPOLINTER" and record.path not in scanner_scope:
        if record.path in ignored_scope:
            tags.append("excluded by .licenseignore (use --include-licenseignore)")
        elif not include_untracked:
            tags.append("scope: untracked (use --include-untracked)")

    if failed_rl and all(f.level == "warning" for f in failed_rl):
        tags.append("level: repolinter warning-only")

    # ONLY_REPOLINTER where the generic license-header rule PASSED but a
    # Qualcomm-specific copyright rule FAILED: repolinter enforces a Qualcomm/LF
    # copyright that full_scan does not require (full_scan accepts any permissive
    # license). Common for vendored/third-party sources -- the key policy
    # divergence to surface for the eventual repolinter-rule retirement.
    slh_passed = any(
        f.rule_name == "source-license-headers-exist" and f.passed
        for f in record.repolinter_findings
    )
    qcom_failed = failed_rules & {
        "source-qualcomm-license-headers-exist", "qualcomm-source-license-headers-exist"
    }
    if record.category == "ONLY_REPOLINTER" and slh_passed and qcom_failed:
        tags.append("repolinter: non-Qualcomm copyright (full_scan clean)")

    return tags


def build_comparison(scanner_view: ScannerView, repolinter_view: RepolinterView,
                     include_untracked: bool) -> list:
    """
    Build the per-file comparison over the union of files flagged by either tool.

    A file is "repolinter-flagged" only if a header rule FAILED on it (a passing
    target is not a finding). A file is "full_scan-flagged" if it has any finding.

    Args:
        scanner_view (ScannerView): Normalized full_scan results.
        repolinter_view (RepolinterView): Normalized repolinter results.
        include_untracked (bool): Whether untracked files were in scanner scope.

    Returns:
        list: ComparisonRecord objects, sorted by category then path.
    """
    rl_failed_paths = {
        path for path, findings in repolinter_view.per_file.items()
        if any(not f.passed for f in findings)
    }
    union = set(scanner_view.per_file) | rl_failed_paths

    records = []
    for path in union:
        scanner_findings = scanner_view.per_file.get(path, [])
        repolinter_findings = repolinter_view.per_file.get(path, [])
        has_fs = bool(scanner_findings)
        has_rl = path in rl_failed_paths
        if has_fs and has_rl:
            category = "BOTH"
        elif has_fs:
            category = "ONLY_FULL_SCAN"
        else:
            category = "ONLY_REPOLINTER"

        record = ComparisonRecord(
            path=path,
            ext=_ext_of(path),
            category=category,
            scanner_findings=scanner_findings,
            repolinter_findings=repolinter_findings,
        )
        record.tags = _compute_tags(record, scanner_view.scanned_paths,
                                     include_untracked, scanner_view.ignored_paths)
        records.append(record)

    order = {"BOTH": 0, "ONLY_FULL_SCAN": 1, "ONLY_REPOLINTER": 2}
    records.sort(key=lambda r: (order.get(r.category, 9), r.path))
    return records


def build_report_data(meta: dict, scanner_view: ScannerView,
                      repolinter_view: RepolinterView, records: list,
                      flagged_files: dict, warning_files: dict,
                      raw_repolinter: dict) -> dict:
    """
    Assemble the JSON-serializable structure embedded into the HTML report.

    Args:
        meta (dict): Run metadata (repo, license, ruleset, scope, timestamp...).
        scanner_view (ScannerView): Normalized full_scan results.
        repolinter_view (RepolinterView): Normalized repolinter results.
        records (list): ComparisonRecord objects.
        flagged_files (dict): Raw full_scan blocking findings.
        warning_files (dict): Raw full_scan warning findings.
        raw_repolinter (dict): The raw repolinter LintResult (for download/audit).

    Returns:
        dict: The report data.
    """
    both = sum(1 for r in records if r.category == "BOTH")
    only_fs = sum(1 for r in records if r.category == "ONLY_FULL_SCAN")
    only_rl = sum(1 for r in records if r.category == "ONLY_REPOLINTER")
    union = both + only_fs + only_rl
    incompat = sum(
        1 for r in records if any(f.kind == "INCOMPAT" for f in r.scanner_findings)
    )
    # Files that fail an error-level repolinter header rule (file-level, to match
    # this report's per-file focus). Repo-level rule failures live in the
    # "Repolinter: all rules" tab, not this card.
    rl_error_files = sum(
        1 for r in records
        if any(not f.passed and f.level == "error" for f in r.repolinter_findings)
    )

    files = []
    for record in records:
        files.append({
            "path": record.path,
            "ext": record.ext,
            "category": record.category,
            "tags": record.tags,
            "full_scan": [
                {"kind": f.kind, "severity": f.severity,
                 "category": f.category, "detail": f.detail}
                for f in record.scanner_findings
            ],
            "repolinter": [
                {"rule": f.rule_name, "code": f.code, "level": f.level,
                 "passed": f.passed, "message": f.message}
                for f in record.repolinter_findings
            ],
        })

    repolinter_all = [
        {"name": res.rule_name, "level": res.level, "ruleType": res.rule_type,
         "status": res.status, "message": res.message}
        for res in repolinter_view.repo_level
    ]
    # Append the header rules as their own summary rows (aggregated pass/fail).
    for name in HEADER_RULES:
        evaluated = [
            f for findings in repolinter_view.per_file.values()
            for f in findings if f.rule_name == name
        ]
        if not evaluated:
            continue
        failed = sum(1 for f in evaluated if not f.passed)
        repolinter_all.append({
            "name": name, "level": HEADER_RULES[name], "ruleType": "file-starts-with",
            "status": "NOT_PASSED_ERROR" if failed and HEADER_RULES[name] == "error"
            else "NOT_PASSED_WARN" if failed else "PASSED",
            "message": f"{failed} of {len(evaluated)} matched file(s) missing the "
                       f"required header/copyright",
        })

    return {
        "meta": meta,
        "summary": {
            "both": both, "only_full_scan": only_fs, "only_repolinter": only_rl,
            "agreement_pct": round(both / union * 100) if union else 100,
            "incompat_count": incompat,
            "rl_error_files": rl_error_files,
            "fs_blocking": scanner_view.flagged_count,
            "fs_warning": scanner_view.warning_count,
            "scanned_files": len(scanner_view.scanned_paths),
        },
        "files": files,
        "repolinter_all": repolinter_all,
        "full_scan_all": {
            "flagged": [
                {"path": normalize_path(p),
                 "license_issues": v.get("license_issues", []),
                 "copyright_issues": v.get("copyright_issues", [])}
                for p, v in flagged_files.items()
            ],
            "warning": [
                {"path": normalize_path(p),
                 "license_issues": v.get("license_issues", []),
                 "copyright_issues": v.get("copyright_issues", [])}
                for p, v in warning_files.items()
            ],
        },
        "raw_repolinter": raw_repolinter,
    }


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #

def render_html(data: dict) -> str:
    """
    Render the self-contained interactive HTML report.

    Embeds `data` as a JSON literal (with '</' escaped so a stray '</script>' in
    any string cannot break out of the <script> block) and inlines all CSS/JS, so
    the file works offline and is trivial to store/share.

    Args:
        data (dict): The structure from build_report_data.

    Returns:
        str: The complete HTML document.
    """
    data_json = json.dumps(data).replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("@@DATA@@", data_json)


def resolve_output_path(output: str, repo_name: str, now: datetime) -> str:
    """
    Decide where the report is written and ensure its directory exists.

    When --output is not given, auto-name it <project>_<YYYYMMDD-HHMMSS>.html
    under the action repo's reports/ dir, so older reports are kept and easy to
    track. <project> is the repo_name's last path segment, sanitized. When
    --output IS given, honor it verbatim (creating its parent dir).

    Args:
        output (str): The --output value (absolute path) or None.
        repo_name (str): The github "owner/repo" (source of <project>).
        now (datetime): Timestamp for the auto-generated filename.

    Returns:
        str: The resolved output file path.
    """
    if output:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        return output

    project = re.sub(r"[^A-Za-z0-9._-]", "_", repo_name.rsplit("/", 1)[-1]) or "report"
    stamp = now.strftime("%Y%m%d-%H%M%S")
    os.makedirs(REPORTS_DIR, exist_ok=True)
    return os.path.join(REPORTS_DIR, f"{project}_{stamp}.html")


def _write_report(html: str, output_path: str) -> None:
    """Write the HTML report to output_path."""
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(html)


def _lan_ip() -> str:
    """Best-effort primary LAN IP of this host (for the shareable URL)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "0.0.0.0"


def _serve_report(output_path: str, port: int, open_browser: bool) -> None:
    """
    Serve the report over HTTP on 0.0.0.0:port so other machines can view it.

    Serves the report's directory (SimpleHTTPRequestHandler needs a directory);
    blocks until Ctrl-C. Binding to 0.0.0.0 exposes that whole directory on the
    network, so the exposed path is printed as a warning.

    Args:
        output_path (str): Absolute path to the written HTML report.
        port (int): TCP port to bind.
        open_browser (bool): Open the served URL locally when the server starts.
    """
    directory = os.path.dirname(output_path) or "."
    filename = os.path.basename(output_path)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
    try:
        # ThreadingHTTPServer (one thread per connection): a browser opens several
        # parallel/keep-alive sockets, and a single-threaded HTTPServer gets wedged
        # on the first, leaving new connections unanswered (blank page). daemon
        # threads let Ctrl-C exit without waiting on lingering client connections.
        httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
        httpd.daemon_threads = True
    except OSError as exc:
        # Port in use / not permitted. The report is already written, so do NOT
        # crash the whole run after the (slow) scan -- report clearly and return.
        print(f"{LOG_PREFIX} Could not serve on port {port}: {exc}.", file=sys.stderr)
        print(f"{LOG_PREFIX} The report is saved at {output_path} -- open it "
              f"directly, or re-run with --port <N> to use a free port.",
              file=sys.stderr)
        return

    local_url = f"http://localhost:{port}/{filename}"
    lan_url = f"http://{_lan_ip()}:{port}/{filename}"
    print(f"{LOG_PREFIX} Serving report at:")
    print(f"{LOG_PREFIX}   {lan_url}   (share this with other machines)")
    print(f"{LOG_PREFIX}   {local_url}")
    print(f"{LOG_PREFIX} NOTE: bound to 0.0.0.0 -- the whole directory '{directory}' "
          f"is exposed on your network. Ctrl-C to stop.", file=sys.stderr)
    if open_browser:
        try:
            webbrowser.open(local_url)
        except Exception:  # pylint: disable=broad-except
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{LOG_PREFIX} Server stopped.", file=sys.stderr)
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

@click.command()
@click.argument("repo_name")
@click.option(
    "--repo-path",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Path to the repository working tree to scan. Resolved to an absolute "
         "path (required for the docker bind mount).",
)
@click.option(
    "--include-untracked",
    is_flag=True,
    default=False,
    show_default=True,
    help="Also scan untracked-but-not-ignored files with full_scan. Repolinter "
         "always scans the working tree, so this is usually needed for parity.",
)
@click.option(
    "--include-licenseignore",
    is_flag=True,
    default=False,
    show_default=True,
    help="Also scan files the repo's .licenseignore excludes. Repolinter does "
         "not honor .licenseignore, so this is needed for parity on repos that "
         "use it (otherwise those files show as repolinter-only).",
)
@click.option(
    "--ruleset-url",
    default=DEFAULT_RULESET_URL,
    show_default=True,
    help="repolinter ruleset URL.",
)
@click.option(
    "--repolinter-json",
    default=None,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Load a pre-saved repolinter --format json result instead of running "
         "docker (offline / repeatable).",
)
@click.option(
    "--output",
    default=None,
    type=click.Path(dir_okay=False, resolve_path=True),
    help="Where to write the HTML report. Default: "
         "<action-repo>/reports/<project>_<YYYYMMDD-HHMMSS>.html (dir auto-created).",
)
@click.option("--open", "open_browser", is_flag=True, default=False,
              help="Open the served report in a browser when the server starts.")
@click.option("--port", default=8000, show_default=True, type=int,
              help="Port to serve the report on. The report is ALWAYS served on "
                   "0.0.0.0 until Ctrl-C; use this only to change the port.")
@click.option("--verbose", is_flag=True, default=False,
              help="Echo suppressed get_license/scan chatter to stderr.")
def main(repo_name: str, repo_path: str, include_untracked: bool,
         include_licenseignore: bool, ruleset_url: str, repolinter_json: str,
         output: str, open_browser: bool, port: int, verbose: bool) -> None:
    """
    Compare repolinter and full_scan over a repository and write an HTML report.

    REPO_NAME is the github "owner/repo", used by full_scan to resolve the repo
    license. This is a diagnostic: it exits 0 whenever a report was produced and
    exits 2 only on an operational failure that prevents the comparison.
    """
    logging.basicConfig(level=logging.WARNING)

    # 1. full_scan (scancode is slow) -- must run before repolinter because it
    #    chdir's; it restores cwd, but keep the ordering explicit.
    print(f"{LOG_PREFIX} Running full-repo scancode scan (this can take a minute)...",
          file=sys.stderr)
    try:
        license_id, flagged_files, warning_files, scanned_paths, ignored_paths = \
            run_full_scan(repo_name, repo_path, include_untracked, verbose,
                          include_licenseignore)
    except subprocess.CalledProcessError as exc:
        print(f"{LOG_PREFIX} ERROR: '{repo_path}' is not a git repository "
              f"(git ls-files failed): {exc}", file=sys.stderr)
        sys.exit(2)

    scanner_view = normalize_scanner(
        license_id, flagged_files, warning_files, scanned_paths, ignored_paths
    )

    # 2. repolinter.
    print(f"{LOG_PREFIX} Running repolinter...", file=sys.stderr)
    repolinter_failed_reason = None
    raw_repolinter = {}
    try:
        raw_repolinter = run_repolinter(repo_path, ruleset_url, repolinter_json)
    except RepolinterUnavailable as exc:
        repolinter_failed_reason = str(exc)

    repolinter_view = normalize_repolinter(raw_repolinter)
    if repolinter_view.errored:
        repolinter_failed_reason = (
            f"repolinter reported an error: {repolinter_view.err_msg}"
        )

    # 3. Comparison + report. One timestamp drives both the filename and the
    #    in-report "generated" stamp so they always agree.
    now = datetime.now()
    output = resolve_output_path(output, repo_name, now)
    records = build_comparison(scanner_view, repolinter_view, include_untracked)
    meta = {
        "repo_name": repo_name,
        "license": license_id,
        "ruleset_url": ruleset_url,
        "repolinter_image": REPOLINTER_IMAGE,
        "include_untracked": include_untracked,
        "include_licenseignore": include_licenseignore,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "repolinter_source": "cached-json" if repolinter_json else "docker",
        "repolinter_ok": repolinter_failed_reason is None,
        "repolinter_error": repolinter_failed_reason,
    }
    data = build_report_data(
        meta, scanner_view, repolinter_view, records,
        flagged_files, warning_files, raw_repolinter,
    )
    _write_report(render_html(data), output)

    # 4. Terminal summary.
    summary = data["summary"]
    print(f"{LOG_PREFIX} Repo license (full_scan): {license_id}")
    if repolinter_failed_reason:
        print(f"{LOG_PREFIX} WARNING: repolinter unavailable -- report is "
              f"full_scan-only.\n{repolinter_failed_reason}", file=sys.stderr)
    print(f"{LOG_PREFIX} Flagged by BOTH: {summary['both']} | "
          f"only full_scan: {summary['only_full_scan']} | "
          f"only repolinter: {summary['only_repolinter']} | "
          f"agreement: {summary['agreement_pct']}%")
    print(f"{LOG_PREFIX} Report written to: {output}")

    # Always serve the report (on --port, default 8000) so it is viewable from
    # other machines; blocks until Ctrl-C, then falls through to the exit below.
    _serve_report(output, port, open_browser)

    # Exit 2 only on operational failure that prevented the comparison.
    sys.exit(2 if repolinter_failed_reason else 0)


# --------------------------------------------------------------------------- #
# HTML template (self-contained: inline CSS + vanilla JS, no external assets)
# --------------------------------------------------------------------------- #

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Repolinter vs full_scan</title>
<style>
  :root {
    --bg: #0f1420; --panel: #171d2b; --panel2: #1e2536; --border: #2b3346;
    --fg: #e6ebf5; --muted: #9aa7bd; --accent: #4f8cff;
    --err: #ff5c6c; --warn: #ffb84d; --ok: #43d19e; --info: #7aa2ff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  header { padding: 20px 24px; background: var(--panel);
    border-bottom: 1px solid var(--border); }
  h1 { margin: 0 0 4px; font-size: 20px; }
  h2 { font-size: 16px; margin: 24px 0 10px; }
  .meta { color: var(--muted); font-size: 13px; display: grid;
    grid-template-columns: max-content 1fr; gap: 2px 12px; margin-top: 10px; max-width: 900px; }
  .meta b { color: var(--fg); font-weight: 600; }
  .wrap { padding: 0 24px 60px; max-width: 1200px; margin: 0 auto; }
  .banner { background: #3a1d22; border: 1px solid var(--err); color: #ffd7db;
    padding: 10px 14px; border-radius: 8px; margin: 16px 0; white-space: pre-wrap; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
    gap: 12px; margin: 18px 0; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; }
  .card .n { font-size: 26px; font-weight: 700; }
  .card .l { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .card.err .n { color: var(--err); } .card.warn .n { color: var(--warn); }
  .card.ok .n { color: var(--ok); } .card.info .n { color: var(--info); }
  .tabs { display: flex; gap: 6px; border-bottom: 1px solid var(--border); margin-top: 10px;
    flex-wrap: wrap; }
  .tab { padding: 9px 14px; cursor: pointer; color: var(--muted); border: 1px solid transparent;
    border-bottom: none; border-radius: 8px 8px 0 0; }
  .tab.active { color: var(--fg); background: var(--panel); border-color: var(--border); }
  .panel { display: none; background: var(--panel); border: 1px solid var(--border);
    border-top: none; border-radius: 0 0 10px 10px; padding: 16px; }
  .panel.active { display: block; }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
  input[type=text], select { background: var(--panel2); color: var(--fg);
    border: 1px solid var(--border); border-radius: 7px; padding: 7px 10px; font-size: 13px; }
  input[type=text] { min-width: 220px; }
  .chip { padding: 5px 11px; border-radius: 999px; border: 1px solid var(--border);
    background: var(--panel2); color: var(--muted); cursor: pointer; font-size: 12px; }
  .chip.active { color: #fff; border-color: var(--accent); background: #22304e; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
    vertical-align: top; }
  th { color: var(--muted); font-weight: 600; cursor: pointer; user-select: none; white-space: nowrap; }
  tr.row { cursor: pointer; }
  tr.row:hover { background: var(--panel2); }
  td.path { font-family: ui-monospace, "SF Mono", Menlo, monospace; word-break: break-all; }
  .detail td { background: #10151f; color: var(--muted); font-size: 12.5px; }
  .detail ul { margin: 4px 0 4px 18px; padding: 0; }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 6px; font-size: 11px;
    font-weight: 600; margin: 1px 3px 1px 0; white-space: nowrap; }
  .b-err { background: #3a1d22; color: var(--err); border: 1px solid #5b2730; }
  .b-warn { background: #3a2f1a; color: var(--warn); border: 1px solid #5b4a27; }
  .b-ok { background: #14342a; color: var(--ok); border: 1px solid #1f5343; }
  .b-info { background: #1c2540; color: var(--info); border: 1px solid #2c3a63; }
  .b-cat-BOTH { background: #3a2f1a; color: var(--warn); border: 1px solid #5b4a27; }
  .b-cat-ONLY_FULL_SCAN { background: #1c2540; color: var(--info); border: 1px solid #2c3a63; }
  .b-cat-ONLY_REPOLINTER { background: #2a2033; color: #c69bff; border: 1px solid #43315b; }
  .tag { display: inline-block; padding: 1px 7px; border-radius: 6px; font-size: 11px;
    background: var(--panel2); color: var(--muted); border: 1px solid var(--border);
    margin: 1px 3px 1px 0; }
  .tag.compat { color: var(--warn); border-color: #5b4a27; }
  .legend { color: var(--muted); }
  .legend li { margin: 6px 0; }
  .legend code { background: var(--panel2); padding: 1px 5px; border-radius: 4px; color: var(--fg); }
  .muted { color: var(--muted); } .empty { color: var(--muted); padding: 20px; text-align: center; }
  button.dl { background: var(--panel2); color: var(--fg); border: 1px solid var(--border);
    border-radius: 7px; padding: 7px 12px; cursor: pointer; }
</style>
</head>
<body>
<header>
  <h1>Repolinter <span class="muted">vs</span> full_scan &mdash; compliance comparison</h1>
  <div id="meta" class="meta"></div>
</header>
<div class="wrap">
  <div id="banner"></div>
  <div id="cards" class="cards"></div>

  <div class="tabs">
    <div class="tab active" data-tab="cmp">License/Copyright comparison</div>
    <div class="tab" data-tab="rl">Repolinter: all rules</div>
    <div class="tab" data-tab="fs">full_scan: all findings</div>
    <div class="tab" data-tab="leg">Legend</div>
  </div>

  <div id="tab-cmp" class="panel active">
    <div class="controls">
      <input type="text" id="search" placeholder="Filter by file path...">
      <span class="chip active" data-cat="ALL">All</span>
      <span class="chip" data-cat="BOTH">Both</span>
      <span class="chip" data-cat="ONLY_FULL_SCAN">Only full_scan</span>
      <span class="chip" data-cat="ONLY_REPOLINTER">Only repolinter</span>
    </div>
    <table id="cmp-table">
      <thead><tr>
        <th data-sort="path">File</th>
        <th data-sort="ext">Ext</th>
        <th>full_scan</th>
        <th>repolinter (header)</th>
        <th data-sort="category">Category</th>
        <th>Divergence</th>
      </tr></thead>
      <tbody id="cmp-body"></tbody>
    </table>
    <div id="cmp-empty" class="empty" style="display:none">
      No per-file license/copyright findings from either tool. They agree the
      scanned files are clean.</div>
  </div>

  <div id="tab-rl" class="panel">
    <div class="controls">
      <input type="text" id="rl-search" placeholder="Filter by rule name...">
      <select id="rl-status">
        <option value="ALL">All statuses</option>
        <option value="PASSED">Passed</option>
        <option value="NOT_PASSED_ERROR">Failed (error)</option>
        <option value="NOT_PASSED_WARN">Failed (warning)</option>
        <option value="IGNORED">Ignored</option>
        <option value="ERROR">Errored</option>
      </select>
    </div>
    <table>
      <thead><tr><th>Rule</th><th>Level</th><th>Type</th><th>Status</th><th>Message</th></tr></thead>
      <tbody id="rl-body"></tbody>
    </table>
  </div>

  <div id="tab-fs" class="panel">
    <h2>Blocking findings</h2>
    <table><thead><tr><th>File</th><th>License issues</th><th>Copyright issues</th></tr></thead>
      <tbody id="fs-flagged"></tbody></table>
    <h2>Warnings (non-blocking)</h2>
    <table><thead><tr><th>File</th><th>License warnings</th><th>Copyright warnings</th></tr></thead>
      <tbody id="fs-warning"></tbody></table>
  </div>

  <div id="tab-leg" class="panel legend">
    <ul>
      <li><b>Header presence vs compatibility.</b> repolinter's header rules only
        check that a copyright line and an SPDX/BSD notice <i>exist</i>; they never
        evaluate whether the license is <i>allowed</i>. full_scan's
        <code>INCOMPAT</code>/<code>UNCERT</code> findings have no repolinter analog.</li>
      <li><b>Extension scope differs.</b> Only full_scan scans
        <code>.rb .swift .kt .kts .mk .bp .bb</code>; only repolinter scans
        <code>.cc .rs .bbclass .S</code>.</li>
      <li><b>Tracked vs working tree.</b> repolinter scans the whole working tree;
        full_scan scans git-tracked files unless <code>--include-untracked</code> is set.</li>
      <li><b>.licenseignore.</b> full_scan honors the repo's <code>.licenseignore</code>;
        repolinter does not. Excluded files appear as repolinter-only, tagged
        <code>excluded by .licenseignore</code>; pass <code>--include-licenseignore</code>
        to scan them too.</li>
      <li><b>Codes.</b> <code>SLH</code>=source-license-headers-exist (error),
        <code>QSLH</code>=qualcomm-source-license-headers-exist (error),
        <code>SQLH</code>=source-qualcomm-license-headers-exist (warning).
        <code>NOLIC</code>/<code>INCOMPAT</code>/<code>UNCERT</code>/<code>NOCR</code>
        are full_scan finding kinds.</li>
    </ul>
    <button class="dl" id="dl-raw">Download raw repolinter JSON</button>
  </div>
</div>

<script>
const DATA = @@DATA@@;

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
function sevClass(sev) { return sev === "error" ? "b-err" : "b-warn"; }

// ---- meta + banner ----
const m = DATA.meta;
document.getElementById("meta").innerHTML = [
  ["Repository", m.repo_name],
  ["Repo license (full_scan)", m.license],
  ["Ruleset", m.ruleset_url],
  ["Repolinter", m.repolinter_image + " (" + m.repolinter_source + ")"],
  ["Scan scope", m.include_untracked ? "tracked + untracked" :
     "tracked only (repolinter also scans untracked; pass --include-untracked for parity)"],
  ["Generated", m.generated_at],
].map(([k, v]) => "<b>" + esc(k) + "</b><span>" + esc(v) + "</span>").join("");

if (!m.repolinter_ok) {
  document.getElementById("banner").innerHTML =
    "<div class=\"banner\">⚠ repolinter unavailable — this report is " +
    "full_scan-only.\n" + esc(m.repolinter_error) + "</div>";
}

// ---- summary cards ----
const s = DATA.summary;
const cards = [
  ["both", "Flagged by BOTH", "warn"],
  ["only_full_scan", "Only full_scan", "info"],
  ["only_repolinter", "Only repolinter", "info"],
  ["agreement_pct", "Overlap (both) %", "ok"],
  ["incompat_count", "Incompatible license", "err"],
  ["rl_error_files", "Files failing repolinter error-rule", "err"],
  ["fs_blocking", "full_scan blocking files", "err"],
  ["scanned_files", "Files scanned (full_scan)", ""],
];
document.getElementById("cards").innerHTML = cards.map(([k, label, cls]) =>
  "<div class=\"card " + cls + "\"><div class=\"n\">" + esc(s[k]) +
  (k === "agreement_pct" ? "%" : "") + "</div><div class=\"l\">" +
  esc(label) + "</div></div>").join("");

// ---- tabs ----
document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".panel").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  document.getElementById("tab-" + t.dataset.tab).classList.add("active");
});

// ---- comparison table ----
let cmpState = { search: "", cat: "ALL", sort: "path", dir: 1 };

function fsBadges(list) {
  if (!list.length) return "<span class=\"muted\">—</span>";
  return list.map(f => "<span class=\"badge " + sevClass(f.severity) + "\" title=\"" +
    esc(f.detail) + "\">" + esc(f.kind) + "</span>").join("");
}
function rlBadges(list) {
  if (!list.length) return "<span class=\"muted\">—</span>";
  return list.map(f => {
    const cls = f.passed ? "b-ok" : (f.level === "error" ? "b-err" : "b-warn");
    const mark = f.passed ? "✓" : "✗";
    return "<span class=\"badge " + cls + "\" title=\"" + esc(f.rule) + "\">" +
      esc(f.code) + " " + mark + "</span>";
  }).join("");
}
function tagBadges(tags) {
  return tags.map(t => "<span class=\"tag" +
    (t.indexOf("compatibility") === 0 ? " compat" : "") + "\">" + esc(t) +
    "</span>").join("") || "<span class=\"muted\">—</span>";
}

function renderCmp() {
  const rows = DATA.files.filter(f =>
    (cmpState.cat === "ALL" || f.category === cmpState.cat) &&
    f.path.toLowerCase().indexOf(cmpState.search.toLowerCase()) !== -1);
  rows.sort((a, b) => {
    const va = a[cmpState.sort] || "", vb = b[cmpState.sort] || "";
    return va < vb ? -cmpState.dir : va > vb ? cmpState.dir : 0;
  });
  const body = document.getElementById("cmp-body");
  document.getElementById("cmp-empty").style.display =
    DATA.files.length ? "none" : "block";
  body.innerHTML = rows.map((f, i) => {
    const detail =
      "<tr class=\"detail\" id=\"d" + i + "\" style=\"display:none\"><td colspan=6>" +
      "<b>full_scan:</b><ul>" +
      (f.full_scan.map(x => "<li>" + esc(x.detail) + "</li>").join("") ||
        "<li class=muted>no findings</li>") + "</ul>" +
      "<b>repolinter (header rules):</b><ul>" +
      (f.repolinter.map(x => "<li>" + esc(x.rule) + " — " +
        (x.passed ? "passed" : "FAILED (" + x.level + ")") +
        (x.message ? ": " + esc(x.message) : "") + "</li>").join("") ||
        "<li class=muted>not evaluated by repolinter</li>") + "</ul></td></tr>";
    return "<tr class=\"row\" onclick=\"toggle(" + i + ")\">" +
      "<td class=path>" + esc(f.path) + "</td>" +
      "<td>" + esc(f.ext || "—") + "</td>" +
      "<td>" + fsBadges(f.full_scan) + "</td>" +
      "<td>" + rlBadges(f.repolinter) + "</td>" +
      "<td><span class=\"badge b-cat-" + f.category + "\">" +
        esc(f.category.replace(/_/g, " ")) + "</span></td>" +
      "<td>" + tagBadges(f.tags) + "</td></tr>" + detail;
  }).join("");
}
function toggle(i) {
  const d = document.getElementById("d" + i);
  if (d) d.style.display = d.style.display === "none" ? "table-row" : "none";
}
document.getElementById("search").oninput = e => {
  cmpState.search = e.target.value; renderCmp();
};
document.querySelectorAll("#tab-cmp .chip").forEach(c => c.onclick = () => {
  document.querySelectorAll("#tab-cmp .chip").forEach(x => x.classList.remove("active"));
  c.classList.add("active"); cmpState.cat = c.dataset.cat; renderCmp();
});
document.querySelectorAll("#cmp-table th[data-sort]").forEach(th => th.onclick = () => {
  const key = th.dataset.sort;
  cmpState.dir = (cmpState.sort === key) ? -cmpState.dir : 1;
  cmpState.sort = key; renderCmp();
});

// ---- repolinter all-rules table ----
let rlState = { search: "", status: "ALL" };
function statusBadge(st) {
  const map = { PASSED: "b-ok", NOT_PASSED_ERROR: "b-err", NOT_PASSED_WARN: "b-warn",
    IGNORED: "b-info", ERROR: "b-err" };
  return "<span class=\"badge " + (map[st] || "b-info") + "\">" + esc(st || "?") + "</span>";
}
function renderRl() {
  const rows = DATA.repolinter_all.filter(r =>
    (rlState.status === "ALL" || r.status === rlState.status) &&
    r.name.toLowerCase().indexOf(rlState.search.toLowerCase()) !== -1);
  document.getElementById("rl-body").innerHTML = rows.map(r =>
    "<tr><td class=path>" + esc(r.name) + "</td><td>" + esc(r.level) +
    "</td><td>" + esc(r.ruleType) + "</td><td>" + statusBadge(r.status) +
    "</td><td class=muted>" + esc(r.message) + "</td></tr>").join("") ||
    "<tr><td colspan=5 class=empty>No matching rules.</td></tr>";
}
document.getElementById("rl-search").oninput = e => {
  rlState.search = e.target.value; renderRl();
};
document.getElementById("rl-status").onchange = e => {
  rlState.status = e.target.value; renderRl();
};

// ---- full_scan all findings ----
document.getElementById("fs-flagged").innerHTML = DATA.full_scan_all.flagged.map(f =>
  "<tr><td class=path>" + esc(f.path) + "</td><td>" +
  (f.license_issues.map(esc).join("<br>") || "<span class=muted>—</span>") +
  "</td><td>" + (f.copyright_issues.map(esc).join("<br>") ||
  "<span class=muted>—</span>") + "</td></tr>").join("") ||
  "<tr><td colspan=3 class=empty>No blocking findings.</td></tr>";
document.getElementById("fs-warning").innerHTML = DATA.full_scan_all.warning.map(f =>
  "<tr><td class=path>" + esc(f.path) + "</td><td>" +
  (f.license_issues.map(esc).join("<br>") || "<span class=muted>—</span>") +
  "</td><td>" +
  ((f.copyright_issues || []).map(esc).join("<br>") || "<span class=muted>—</span>") +
  "</td></tr>").join("") ||
  "<tr><td colspan=3 class=empty>No warnings.</td></tr>";

// ---- raw JSON download ----
document.getElementById("dl-raw").onclick = () => {
  const blob = new Blob([JSON.stringify(DATA.raw_repolinter, null, 2)],
    { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "repolinter.json";
  a.click(); URL.revokeObjectURL(a.href);
};

renderCmp(); renderRl();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter

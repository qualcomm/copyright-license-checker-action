# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Run the full-repository license/copyright scan for a repo referenced by a Jira ticket.

Given a Jira issue key (e.g. OSSOPS-29471) whose custom field named "URL" holds a
repository link (e.g. https://github.com/qualcomm/time-services), this tool:

  1. Fetches the repo URL from that field (resolved to its customfield id by name).
  2. Validates the URL is present and parseable.
  3. Shallow-clones the repo and runs the full-repo scan against it (in-process, the
     same code path as full_scan.py -- see compare_tools.run_full_scan).
  4. Collects the findings/warnings.
  5. Posts the results back onto the ticket as a single comment (capped to Jira's
     comment size limit). Validation/clone/scan failures are posted as an error comment.

It is a standalone operator/automation entry point -- NOT part of the GitHub Action and
not a CI gate. It reuses the existing clone + scan helpers and the public `jira` PyPI
library (jira-python) for Jira access. It deliberately depends on NO internal/shared
Qualcomm package (e.g. qnaro's `jira_client`) -- only public PyPI.

Auth is HTTP Basic (username + password/token), the same mechanism as
common_utils/djira.py -- but this tool depends only on the public `jira` lib, not on
that module. Credentials/config come from a .env file, the real environment, or a
~/.jira-creds file (the Qualcomm-standard creds file djira uses: line 1 = username,
line 2 = password). The env-var names match qnaro's (so the same .env values can be
reused):
    JIRA_API_SERVER  Jira REST base, INCLUDING any context path. Defaults to
                     https://jira-dc-tools.qualcomm.com/jira (a missing /jira 404s).
                     JIRA_BASE_URL is accepted as an alias.
    JIRA_USER        Jira username     (falls back to line 1 of ~/.jira-creds)
    JIRA_PASSWORD    Jira password/token (falls back to line 2 of ~/.jira-creds)
    MAX_COMMENT_LENGTH  (optional) comment-body cap; default 16384 (see --comment-limit)
    JIRA_COMMENT_VISIBILITY_GROUP  (optional) restrict the posted comment to this
                     Jira group (e.g. 'developers'); default public. See
                     --comment-visibility-group.
    GITHUB_TOKEN     (optional) clone auth for private / Enterprise repos
    REQUESTS_CA_BUNDLE  (optional) CA bundle for corporate SSL (Jira API + git)

Use --insecure to skip TLS verification (as djira does) when the corporate CA bundle
is not configured.

Usage:
    python scripts/jira_scan.py <ISSUE-KEY> [--url-field NAME_OR_ID] [--env-file PATH]
                                [--jira-url URL] [--ref BRANCH] [--include-untracked]
                                [--include-licenseignore] [--comment-limit N]
                                [--comment-visibility-group GROUP]
                                [--dry-run] [--fail-on-findings]

Dependencies: `git` and `scancode` on PATH (the scan shells out to them), and the `jira`
package (`pip install -r full-scan/scripts/requirements.txt`).
"""

import os
import re
import sys
import logging
import tempfile
import urllib.parse

import click
from jira import JIRA, JIRAError

# This file lives in <action-repo>/full-scan/scripts/. Put full-scan/ on sys.path
# (so the reused helpers can import the `scanner` package) and this scripts/ dir
# (so we can import the sibling diagnostic modules) regardless of how we're invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# pylint: disable=wrong-import-position
from compare_tools import run_full_scan
from compare_tools_remote import clone_repo

LOG_PREFIX = "< jira full-repo scan >"

# Default Jira REST base (context path included). Same instance used by qnaro and
# common_utils/djira; overridable via JIRA_API_SERVER / --jira-url.
DEFAULT_JIRA_API_SERVER = "https://jira-dc-tools.qualcomm.com/jira"

# Qualcomm-standard local credentials file (line 1 = username, line 2 = password),
# as used by common_utils/djira. Consulted only when JIRA_USER/JIRA_PASSWORD are unset.
DEFAULT_CREDS_FILE = "~/.jira-creds"

# Default comment-body cap. Matches qnaro's MAX_COMMENT_LENGTH: the OSSOPS Jira
# instance rejects comments longer than this (well under Jira's own 32,767 text-field
# limit). Override per-run with --comment-limit, or set MAX_COMMENT_LENGTH in .env.
DEFAULT_COMMENT_LIMIT = 16384

# Chars reserved when deciding how much per-file detail fits before the truncation
# note is appended (the note itself is well under this).
_TRUNCATE_RESERVE = 220


class JiraError(Exception):
    """A Jira operation failed (connection, auth, or API error)."""


class JiraConfig:
    """Resolved Jira connection settings (Basic auth)."""

    def __init__(self, base_url: str, user: str, password: str, ca_bundle: str,
                 insecure: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.ca_bundle = ca_bundle
        self.insecure = insecure


# --- .env loading ------------------------------------------------------------

def parse_env_file(path: str) -> dict:
    """
    Parse a .env file into a dict WITHOUT touching the environment.

    Skips blank lines and `#` comments, tolerates a leading `export `, and strips a
    single pair of matching surrounding quotes from the value. A missing file yields
    an empty dict (it is optional). Kept dependency-free (no python-dotenv).

    Args:
        path (str): Path to the .env file.

    Returns:
        dict: The parsed KEY -> VALUE pairs.
    """
    values = {}
    if not path or not os.path.isfile(path):
        return values
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            if key:
                values[key] = val
    return values


def load_env_file(path: str) -> dict:
    """
    Load a .env file into os.environ, NOT overriding already-set variables.

    Real environment variables win over the file, so an operator can override any
    .env value inline (e.g. `JIRA_PASSWORD=... python scripts/jira_scan.py ...`).

    Args:
        path (str): Path to the .env file (optional; missing file is a no-op).

    Returns:
        dict: The parsed pairs (the same as parse_env_file), for logging/tests.
    """
    values = parse_env_file(path)
    for key, val in values.items():
        os.environ.setdefault(key, val)
    return values


# --- Jira client (jira-python, Basic auth) -----------------------------------

def _scrub(text: str, secret: str) -> str:
    """Redact `secret` from text so a password never leaks into an error message."""
    if not text:
        return ""
    if secret:
        return text.replace(secret, "***")
    return text


def build_client(cfg: "JiraConfig") -> JIRA:
    """
    Connect to Jira with Basic auth and return a jira.JIRA client.

    jira-python uses `requests` underneath, so a corporate CA bundle
    (REQUESTS_CA_BUNDLE) is honored; we also pass it explicitly as options["verify"].
    When cfg.insecure is set, TLS verification is disabled (verify=False), matching
    common_utils/djira -- use only when the corporate CA is not configured.
    `validate=True` makes the constructor verify auth up front so a bad
    URL/credential fails here rather than on the first call.

    Args:
        cfg (JiraConfig): Connection settings.

    Returns:
        jira.JIRA: A ready client.

    Raises:
        JiraError: If the connection or authentication fails (password scrubbed).
    """
    if cfg.insecure:
        options = {"verify": False}
        try:
            import urllib3  # pylint: disable=import-outside-toplevel
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:  # pylint: disable=broad-except
            pass
    else:
        options = {"verify": cfg.ca_bundle} if cfg.ca_bundle else {}
    try:
        return JIRA(server=cfg.base_url, basic_auth=(cfg.user, cfg.password),
                    options=options, max_retries=1, validate=True)
    except JIRAError as exc:
        detail = f"HTTP {exc.status_code} {exc.text}".strip()
        raise JiraError(_scrub(
            f"Could not connect to Jira at {cfg.base_url}: {detail}", cfg.password)) from None
    except Exception as exc:  # pylint: disable=broad-except
        raise JiraError(_scrub(
            f"Could not connect to Jira at {cfg.base_url}: {exc}", cfg.password)) from None


def post_comment(client: JIRA, issue_key: str, body: str,
                 visibility: dict = None) -> None:
    """
    Add a comment to a Jira issue.

    Args:
        client (jira.JIRA): A connected client.
        issue_key (str): The issue key.
        body (str): The wiki-markup comment body.
        visibility (dict): Optional comment-visibility restriction passed straight
            to the Jira REST `visibility` field, e.g.
            {"type": "group", "value": "developers"} to make the comment visible
            only to members of the 'developers' group (or {"type": "role", ...}).
            When None, the comment is posted with default (unrestricted) visibility.

    Raises:
        JiraError: If the comment cannot be added.
    """
    try:
        # jira-python's add_comment maps `visibility` onto the REST visibility
        # field; passing None is a no-op (unrestricted), so this covers both cases.
        client.add_comment(issue_key, body, visibility=visibility)
    except Exception as exc:  # pylint: disable=broad-except
        raise JiraError(f"Could not add comment to {issue_key}: {exc}") from None


def read_jira_creds_file(path: str = DEFAULT_CREDS_FILE):
    """
    Read (user, password) from a ~/.jira-creds file.

    Matches common_utils/djira's convention: the first two non-empty lines are the
    username and password. Returns (None, None) if the file is absent/unreadable or
    has fewer than two lines. Nothing here depends on that module -- only the format.

    Args:
        path (str): Path to the creds file (``~`` is expanded).

    Returns:
        tuple: (user, password) or (None, None).
    """
    expanded = os.path.expanduser(path)
    if not os.path.isfile(expanded):
        return None, None
    try:
        with open(expanded, "r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return None, None
    if len(lines) < 2:
        return None, None
    return lines[0], lines[1]


def read_jira_config(jira_url_override: str = None, insecure: bool = False,
                     creds_file: str = DEFAULT_CREDS_FILE) -> JiraConfig:
    """
    Build a JiraConfig from the environment (after .env has been loaded), falling
    back to ~/.jira-creds for the username/password.

    Args:
        jira_url_override (str): Value for --jira-url; overrides JIRA_API_SERVER.
        insecure (bool): Skip TLS verification (see build_client).
        creds_file (str): Path to the ~/.jira-creds fallback.

    Returns:
        JiraConfig: The resolved settings.

    Raises:
        ValueError: If the username or password cannot be resolved.
    """
    # JIRA_API_SERVER is qnaro's name for the REST base (it must include any context
    # path, e.g. .../jira); JIRA_BASE_URL is an alias; else default to the known server.
    base_url = (jira_url_override
                or os.environ.get("JIRA_API_SERVER")
                or os.environ.get("JIRA_BASE_URL")
                or DEFAULT_JIRA_API_SERVER).strip()
    user = (os.environ.get("JIRA_USER") or "").strip()
    password = os.environ.get("JIRA_PASSWORD") or ""
    # Fall back to ~/.jira-creds (djira's file) for whichever of user/password is unset.
    if not (user and password):
        file_user, file_password = read_jira_creds_file(creds_file)
        user = user or (file_user or "").strip()
        password = password or (file_password or "")
    ca_bundle = (os.environ.get("REQUESTS_CA_BUNDLE") or "").strip()
    missing = [name for name, value in (
        ("JIRA_USER", user), ("JIRA_PASSWORD", password),
    ) if not value]
    if missing:
        raise ValueError(
            "Missing Jira credentials: " + ", ".join(missing)
            + f" (set them in .env / the environment, or in {creds_file}).")
    return JiraConfig(base_url, user, password, ca_bundle, insecure)


# --- Ticket -> repo URL ------------------------------------------------------

def resolve_url_field_id(client: JIRA, field_name_or_id: str) -> str:
    """
    Resolve a Jira field name to its id (e.g. "URL" -> "customfield_10001").

    An explicit id (anything starting with "customfield_") is returned unchanged.
    Otherwise client.fields() is queried and matched by name (case-insensitive),
    preferring a custom field on a tie.

    Args:
        client (jira.JIRA): A connected client.
        field_name_or_id (str): The field name (default "URL") or an explicit id.

    Returns:
        str: The field id.

    Raises:
        JiraError: If the field list cannot be read or no field with that name exists.
    """
    if field_name_or_id.startswith("customfield_"):
        return field_name_or_id
    wanted = field_name_or_id.strip().lower()
    try:
        fields = client.fields()
    except Exception as exc:  # pylint: disable=broad-except
        raise JiraError(f"Could not list Jira fields: {exc}") from None
    matches = [f for f in fields if str(f.get("name", "")).strip().lower() == wanted]
    if not matches:
        raise JiraError(
            f"No Jira field named '{field_name_or_id}' was found "
            "(check the field name, or pass its customfield id via --url-field).")
    for field in matches:
        if field.get("custom"):
            return field["id"]
    return matches[0]["id"]


def _coerce_field_value(value) -> str:
    """Reduce a Jira field value (str / list / object) to a plain string, or ''."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return _coerce_field_value(value[0]) if value else ""
    if isinstance(value, dict):
        for key in ("value", "url", "content", "name"):
            if value.get(key):
                return str(value[key]).strip()
    return str(value).strip()


def fetch_repo_url(client: JIRA, issue_key: str, field_id: str) -> str:
    """
    Read the repository URL from a Jira issue's field.

    Args:
        client (jira.JIRA): A connected client.
        issue_key (str): The Jira issue key.
        field_id (str): The field id to read (from resolve_url_field_id).

    Returns:
        str: The field's string value, or "" if empty/absent.

    Raises:
        JiraError: If the issue cannot be fetched.
    """
    try:
        issue = client.issue(issue_key, fields=field_id)
    except Exception as exc:  # pylint: disable=broad-except
        raise JiraError(f"Could not fetch issue {issue_key}: {exc}") from None
    return _coerce_field_value(getattr(issue.fields, field_id, None))


def parse_repo_url(url: str):
    """
    Normalize a repository URL into (host, owner, repo, clone_url).

    Handles https(s) URLs (github.com and Enterprise hosts), scp-style SSH
    (git@host:owner/repo), a trailing .git, trailing slashes, and extra path
    segments (e.g. /tree/<branch>) -- the first two path segments are owner/repo.

    Args:
        url (str): The raw URL string from the ticket.

    Returns:
        tuple | None: (host, owner, repo, clone_url), or None if unparseable.
    """
    if not url:
        return None
    url = url.strip()
    # scp-style SSH (git@host:owner/repo) and ssh:// -> normalize to https for parsing.
    url = re.sub(r"^git@([^:/]+):", r"https://\1/", url)
    url = re.sub(r"^ssh://git@", "https://", url)
    if "://" not in url:
        url = "https://" + url
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = parsed.netloc.split("@")[-1]  # drop any user:pass@ prefix
    segments = [seg for seg in parsed.path.split("/") if seg]
    if not host or len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    return host, owner, repo, f"https://{host}/{owner}/{repo}.git"


# --- Comment rendering -------------------------------------------------------

def _file_block(path: str, entry: dict) -> str:
    """Render one file's issues as Jira wiki-markup bullets (monospace filename)."""
    lines = ["* {{" + path + "}}"]
    for issue in entry.get("license_issues", []):
        lines.append("** " + issue)
    for issue in entry.get("copyright_issues", []):
        lines.append("** " + issue)
    return "\n".join(lines)


def build_comment(repo_name: str, license_id: str, flagged: dict, warning: dict,
                  scanned, ignored, limit: int = DEFAULT_COMMENT_LIMIT) -> str:
    """
    Render the scan result as a Jira comment body, capped to `limit` characters.

    The summary (counts) is always kept intact. Per-file detail is appended greedily
    and truncated -- with an explicit note of how many files were omitted -- if it
    would exceed the limit, so the body never exceeds Jira's comment size cap.

    Args:
        repo_name (str): "owner/repo".
        license_id (str): The resolved repository license.
        flagged (dict): Blocking findings, keyed by path -> {license_issues,
            copyright_issues}.
        warning (dict): Non-blocking findings, same shape.
        scanned: The set/list of scanned file paths.
        ignored: The set/list of files skipped by .licenseignore.
        limit (int): Maximum comment length.

    Returns:
        str: The Jira wiki-markup comment body (len <= limit).
    """
    blocking_n = len(flagged)
    warning_n = len(warning)
    summary_lines = [
        "h3. Full-repository license/copyright scan",
        "",
        "*Repository:* {{" + repo_name + "}}",
        f"*Resolved license:* {license_id}",
        f"*Files scanned:* {len(scanned)}",
        f"*Blocking files:* {blocking_n}",
        f"*Warning files:* {warning_n}",
    ]
    if ignored:
        summary_lines.append(f"*Skipped by .licenseignore:* {len(ignored)}")
    summary = "\n".join(summary_lines)

    if not flagged and not warning:
        return summary + "\n\nNo license or copyright issues found. (OK)"

    sections = []
    if flagged:
        sections.append((f"h4. Blocking issues ({blocking_n})", flagged))
    if warning:
        sections.append((f"h4. Warnings ({warning_n})", warning))

    body = summary
    included = 0
    truncated = False
    for title, files in sections:
        if truncated:
            break
        candidate = body + "\n\n" + title
        if len(candidate) + _TRUNCATE_RESERVE > limit:
            truncated = True
            break
        body = candidate
        for path in sorted(files):
            block = "\n" + _file_block(path, files[path])
            if len(body) + len(block) + _TRUNCATE_RESERVE > limit:
                truncated = True
                break
            body += block
            included += 1

    if truncated:
        omitted = (blocking_n + warning_n) - included
        body += (f"\n\n(Detail truncated to fit Jira's comment size limit; "
                 f"{omitted} more file(s) omitted. See the counts above.)")
    return body


def build_error_comment(category: str, message: str) -> str:
    """
    Render an error as a Jira comment body.

    Args:
        category (str): One of missing_url / url_unparseable / clone_failed /
            scan_failed (anything else falls back to a generic title).
        message (str): Human-readable detail.

    Returns:
        str: The Jira wiki-markup comment body.
    """
    titles = {
        "missing_url": "Repository URL is missing on the ticket",
        "url_unparseable": "Repository URL could not be parsed",
        "clone_failed": "Repository could not be cloned (missing, private, or bad URL)",
        "scan_failed": "The scan failed to run",
    }
    title = titles.get(category, "Scan error")
    return "\n".join([
        "h3. Full-repository license/copyright scan - ERROR",
        "",
        f"*Problem:* {title}",
        f"*Detail:* {message}",
    ])


# --- Orchestration -----------------------------------------------------------

def _post_or_print(client: JIRA, issue_key: str, body: str, dry_run: bool,
                   visibility: dict = None) -> None:
    """Post the comment to the ticket, or (under --dry-run) print it and post nothing."""
    if dry_run:
        vis = (f" [visibility: {visibility['type']}={visibility['value']}]"
               if visibility else "")
        click.echo(f"--- DRY RUN: comment for {issue_key}{vis} (not posted) ---")
        click.echo(body)
        return
    try:
        post_comment(client, issue_key, body, visibility)
    except JiraError as exc:
        click.echo(f"ERROR: failed to post comment to {issue_key}: {exc}", err=True)
        click.echo(body, err=True)
        sys.exit(3)
    where = (f" (restricted to group '{visibility['value']}')"
             if visibility and visibility.get("type") == "group" else "")
    click.echo(f"Posted scan results to {issue_key}{where}.")


def _finish_error(client: JIRA, issue_key: str, category: str, message: str,
                  dry_run: bool, visibility: dict = None) -> None:
    """Report a validation/clone/scan failure back onto the ticket."""
    click.echo(f"ERROR ({category}): {message}", err=True)
    _post_or_print(client, issue_key, build_error_comment(category, message),
                   dry_run, visibility)


@click.command()
@click.argument("issue_key")
@click.option("--url-field", default="URL", show_default=True,
              help="Name of the ticket field holding the repo URL, or an explicit "
                   "customfield id.")
@click.option("--env-file", default=".env", show_default=True, type=click.Path(),
              help="Path to a .env file with Jira/GitHub settings (optional).")
@click.option("--jira-url", default=None,
              help="Jira REST base URL (include any context path, e.g. .../jira); "
                   "overrides JIRA_API_SERVER. Defaults to the known OSSOPS server.")
@click.option("--creds-file", default=DEFAULT_CREDS_FILE, show_default=True,
              type=click.Path(),
              help="Fallback credentials file (line 1 = username, line 2 = password) "
                   "used when JIRA_USER/JIRA_PASSWORD are unset, like djira.")
@click.option("--insecure", is_flag=True, default=False, show_default=True,
              help="Skip TLS certificate verification (as djira does). Use only when "
                   "the corporate CA bundle is not configured.")
@click.option("--ref", default=None,
              help="Branch/tag to clone (default: the repo's default branch).")
@click.option("--include-untracked", is_flag=True, default=False, show_default=True,
              help="Also scan untracked-but-not-.gitignore'd files.")
@click.option("--include-licenseignore", is_flag=True, default=False, show_default=True,
              help="Also scan files matched by the repo's .licenseignore.")
@click.option("--comment-limit", default=0, type=int,
              help="Maximum Jira comment length before detail is truncated. 0 (the "
                   "default) uses MAX_COMMENT_LENGTH from the environment, else "
                   f"{DEFAULT_COMMENT_LIMIT}.")
@click.option("--comment-visibility-group", default=None,
              help="Restrict the posted comment so only members of this Jira group "
                   "can see it (e.g. 'developers'), via the comment's visibility "
                   "field. Defaults to JIRA_COMMENT_VISIBILITY_GROUP from the "
                   "environment, else the comment is public (unrestricted).")
@click.option("--dry-run", is_flag=True, default=False, show_default=True,
              help="Do everything except post; print the comment to stdout.")
@click.option("--fail-on-findings", is_flag=True, default=False, show_default=True,
              help="Exit non-zero when blocking findings exist (default: exit 0).")
def main(issue_key: str, url_field: str, env_file: str, jira_url: str, creds_file: str,
         insecure: bool, ref: str, include_untracked: bool, include_licenseignore: bool,
         comment_limit: int, comment_visibility_group: str, dry_run: bool,
         fail_on_findings: bool) -> None:
    """Scan the repository referenced by a Jira ticket and comment the results back."""
    logging.basicConfig(level=logging.WARNING)
    load_env_file(env_file)

    # Optional comment-visibility restriction. --comment-visibility-group wins, else
    # JIRA_COMMENT_VISIBILITY_GROUP from the (now-loaded) .env/environment. When set,
    # EVERY comment this run posts -- results and error reports -- is restricted to
    # that group so scan output is not exposed to ticket reporters/watchers outside it.
    vis_group = (comment_visibility_group
                 or os.environ.get("JIRA_COMMENT_VISIBILITY_GROUP") or "").strip()
    visibility = {"type": "group", "value": vis_group} if vis_group else None

    try:
        cfg = read_jira_config(jira_url, insecure=insecure, creds_file=creds_file)
    except ValueError as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(2)

    # Connect, resolve the field, and read the repo URL. A Jira failure here is fatal and
    # cannot be reported onto the ticket (we may not even have a working connection).
    try:
        client = build_client(cfg)
        field_id = resolve_url_field_id(client, url_field)
        raw_url = fetch_repo_url(client, issue_key, field_id)
    except JiraError as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(2)

    if not raw_url:
        _finish_error(client, issue_key, "missing_url",
                      f"The '{url_field}' field on {issue_key} is empty or absent.",
                      dry_run, visibility)
        sys.exit(1)

    parsed = parse_repo_url(raw_url)
    if not parsed:
        _finish_error(client, issue_key, "url_unparseable",
                      f"Could not parse a repository from: {raw_url}", dry_run,
                      visibility)
        sys.exit(1)
    _host, owner, repo, clone_url = parsed
    repo_name = f"{owner}/{repo}"

    token = os.environ.get("GITHUB_TOKEN") or ""
    with tempfile.TemporaryDirectory(prefix="jira-scan-") as tmp:
        dest = os.path.join(tmp, "repo")
        ok, clone_err = clone_repo(clone_url, dest, token, cfg.ca_bundle, ref)
        if not ok:
            _finish_error(client, issue_key, "clone_failed",
                          f"git clone {clone_url} failed: {clone_err}", dry_run,
                          visibility)
            sys.exit(1)

        try:
            license_id, flagged, warning, scanned, ignored = run_full_scan(
                repo_name, dest, include_untracked, False, include_licenseignore)
        except Exception as exc:  # pylint: disable=broad-except
            _finish_error(client, issue_key, "scan_failed",
                          f"full_scan failed for {repo_name}: {exc}", dry_run,
                          visibility)
            sys.exit(1)

        # 0 means "auto": prefer MAX_COMMENT_LENGTH from the (now-loaded) .env, matching
        # qnaro; fall back to the built-in default.
        limit = comment_limit or int(
            os.environ.get("MAX_COMMENT_LENGTH", DEFAULT_COMMENT_LIMIT))
        comment = build_comment(repo_name, license_id, flagged, warning,
                                scanned, ignored, limit)
        _post_or_print(client, issue_key, comment, dry_run, visibility)

    if fail_on_findings and flagged:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter

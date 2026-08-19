# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import types

import pytest
from click.testing import CliRunner

import jira_scan as js


# --- .env loading ------------------------------------------------------------

def test_parse_env_file_basic(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "JIRA_API_SERVER=https://jira.example.com/jira\n"
        "export JIRA_USER=alice\n"
        'JIRA_PASSWORD="p@ss=word"\n'          # quotes stripped; '=' in value kept
        "BLANK=\n"
        "no_equals_line\n",
        encoding="utf-8",
    )
    values = js.parse_env_file(str(env))
    assert values["JIRA_API_SERVER"] == "https://jira.example.com/jira"
    assert values["JIRA_USER"] == "alice"          # leading `export ` stripped
    assert values["JIRA_PASSWORD"] == "p@ss=word"  # quotes gone, inner '=' preserved
    assert values["BLANK"] == ""
    assert "no_equals_line" not in values


def test_parse_env_file_missing_is_empty(tmp_path):
    assert js.parse_env_file(str(tmp_path / "nope.env")) == {}


def test_load_env_file_does_not_override_real_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JIRA_USER", "real-user")
    env = tmp_path / ".env"
    env.write_text("JIRA_USER=file-user\nJIRA_PASSWORD=file-pass\n", encoding="utf-8")
    js.load_env_file(str(env))
    # Pre-set env wins; a not-yet-set key is filled from the file.
    assert js.os.environ["JIRA_USER"] == "real-user"
    assert js.os.environ["JIRA_PASSWORD"] == "file-pass"


# --- read_jira_config --------------------------------------------------------

_NO_CREDS_FILE = "/nonexistent/.jira-creds"   # keep tests off any real ~/.jira-creds


def test_read_jira_creds_file(tmp_path):
    creds = tmp_path / ".jira-creds"
    creds.write_text("myuser\nmypass\nignored-extra\n", encoding="utf-8")
    assert js.read_jira_creds_file(str(creds)) == ("myuser", "mypass")


def test_read_jira_creds_file_missing(tmp_path):
    assert js.read_jira_creds_file(str(tmp_path / "nope")) == (None, None)


def test_read_jira_creds_file_too_few_lines(tmp_path):
    creds = tmp_path / ".jira-creds"
    creds.write_text("onlyuser\n", encoding="utf-8")
    assert js.read_jira_creds_file(str(creds)) == (None, None)


def test_read_jira_config_missing_raises(monkeypatch):
    for var in ("JIRA_API_SERVER", "JIRA_BASE_URL", "JIRA_USER", "JIRA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError) as excinfo:
        js.read_jira_config(creds_file=_NO_CREDS_FILE)
    assert "JIRA_USER" in str(excinfo.value)


def test_read_jira_config_defaults_server(monkeypatch):
    monkeypatch.delenv("JIRA_API_SERVER", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.setenv("JIRA_USER", "u")
    monkeypatch.setenv("JIRA_PASSWORD", "p")
    assert js.read_jira_config(creds_file=_NO_CREDS_FILE).base_url \
        == js.DEFAULT_JIRA_API_SERVER


def test_read_jira_config_creds_file_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("JIRA_USER", raising=False)
    monkeypatch.delenv("JIRA_PASSWORD", raising=False)
    monkeypatch.setenv("JIRA_API_SERVER", "https://h/jira")
    creds = tmp_path / ".jira-creds"
    creds.write_text("fileuser\nfilepass\n", encoding="utf-8")
    cfg = js.read_jira_config(creds_file=str(creds))
    assert cfg.user == "fileuser"
    assert cfg.password == "filepass"


def test_read_jira_config_api_server_primary(monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.setenv("JIRA_API_SERVER", "https://jira-dc-tools.qualcomm.com/jira")
    monkeypatch.setenv("JIRA_USER", "u")
    monkeypatch.setenv("JIRA_PASSWORD", "p")
    cfg = js.read_jira_config()
    assert cfg.base_url == "https://jira-dc-tools.qualcomm.com/jira"   # context path kept


def test_read_jira_config_base_url_alias(monkeypatch):
    monkeypatch.delenv("JIRA_API_SERVER", raising=False)
    monkeypatch.setenv("JIRA_BASE_URL", "https://alias-host/jira")
    monkeypatch.setenv("JIRA_USER", "u")
    monkeypatch.setenv("JIRA_PASSWORD", "p")
    assert js.read_jira_config().base_url == "https://alias-host/jira"


def test_read_jira_config_url_override(monkeypatch):
    monkeypatch.setenv("JIRA_API_SERVER", "https://env-host/jira")
    monkeypatch.setenv("JIRA_USER", "u")
    monkeypatch.setenv("JIRA_PASSWORD", "p")
    cfg = js.read_jira_config("https://override-host/")
    assert cfg.base_url == "https://override-host"   # override wins, slash trimmed


# --- Jira client (jira-python) stand-in --------------------------------------

class _FakeClient:
    """Minimal jira.JIRA stand-in: fields()/issue()/add_comment() with no network."""

    def __init__(self, fields=None, issue_value=None):
        self._fields = fields if fields is not None else []
        self._issue_value = issue_value
        self.comments = []

    def fields(self):
        return self._fields

    def issue(self, key, fields=None):
        holder = types.SimpleNamespace()
        setattr(holder, fields, self._issue_value)          # fields == the field id
        return types.SimpleNamespace(key=key, fields=holder)

    def add_comment(self, key, body, visibility=None):
        self.comments.append((key, body, visibility))


def test_build_client_passes_basic_auth_and_verify(monkeypatch):
    captured = {}
    monkeypatch.setattr(js, "JIRA", lambda **kwargs: captured.update(kwargs) or "CLIENT")
    cfg = js.JiraConfig("https://host/jira/", "u", "p", "/etc/ca.pem")
    assert js.build_client(cfg) == "CLIENT"
    assert captured["server"] == "https://host/jira"       # trailing slash trimmed by cfg
    assert captured["basic_auth"] == ("u", "p")
    assert captured["options"] == {"verify": "/etc/ca.pem"}


def test_build_client_no_ca_bundle_empty_options(monkeypatch):
    captured = {}
    monkeypatch.setattr(js, "JIRA", lambda **kwargs: captured.update(kwargs) or "C")
    js.build_client(js.JiraConfig("https://h/jira", "u", "p", ""))
    assert captured["options"] == {}


def test_build_client_insecure_disables_verify(monkeypatch):
    captured = {}
    monkeypatch.setattr(js, "JIRA", lambda **kwargs: captured.update(kwargs) or "C")
    # insecure wins even if a CA bundle is set (matches djira's verify=False).
    js.build_client(js.JiraConfig("https://h/jira", "u", "p", "/ca.pem", insecure=True))
    assert captured["options"] == {"verify": False}


def test_build_client_wraps_error_and_scrubs_password(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("connection refused for pass=SEKRET")
    monkeypatch.setattr(js, "JIRA", boom)
    with pytest.raises(js.JiraError) as excinfo:
        js.build_client(js.JiraConfig("https://h/jira", "u", "SEKRET", ""))
    assert "SEKRET" not in str(excinfo.value)
    assert "***" in str(excinfo.value)


# --- resolve_url_field_id ----------------------------------------------------

def test_resolve_url_field_id_explicit_id_no_call():
    class _Boom:
        def fields(self):
            raise AssertionError("should not query fields for an explicit id")
    assert js.resolve_url_field_id(_Boom(), "customfield_9999") == "customfield_9999"


def test_resolve_url_field_id_by_name_prefers_custom():
    client = _FakeClient(fields=[
        {"id": "summary", "name": "url", "custom": False},   # system, same name
        {"id": "customfield_101", "name": "URL", "custom": True},
    ])
    # Case-insensitive match, custom field preferred over the system one.
    assert js.resolve_url_field_id(client, "URL") == "customfield_101"


def test_resolve_url_field_id_missing_raises():
    client = _FakeClient(fields=[{"id": "summary", "name": "Summary", "custom": False}])
    with pytest.raises(js.JiraError):
        js.resolve_url_field_id(client, "URL")


# --- fetch_repo_url ----------------------------------------------------------

def test_fetch_repo_url_string():
    client = _FakeClient(issue_value="https://github.com/q/a")
    assert js.fetch_repo_url(client, "OSSOPS-1", "customfield_101") \
        == "https://github.com/q/a"


def test_fetch_repo_url_empty():
    client = _FakeClient(issue_value=None)
    assert js.fetch_repo_url(client, "OSSOPS-1", "customfield_101") == ""


# --- post_comment ------------------------------------------------------------

def test_post_comment_calls_add_comment():
    client = _FakeClient()
    js.post_comment(client, "OSSOPS-1", "hi")
    assert client.comments == [("OSSOPS-1", "hi", None)]


def test_post_comment_passes_group_visibility():
    client = _FakeClient()
    vis = {"type": "group", "value": "developers"}
    js.post_comment(client, "OSSOPS-1", "hi", vis)
    assert client.comments == [("OSSOPS-1", "hi", vis)]


# --- parse_repo_url ----------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://github.com/qualcomm/time-services",
     ("github.com", "qualcomm", "time-services",
      "https://github.com/qualcomm/time-services.git")),
    ("https://github.com/qualcomm/time-services.git",
     ("github.com", "qualcomm", "time-services",
      "https://github.com/qualcomm/time-services.git")),
    ("https://github.com/qualcomm/time-services/",
     ("github.com", "qualcomm", "time-services",
      "https://github.com/qualcomm/time-services.git")),
    ("https://github.com/qualcomm/time-services/tree/dev",
     ("github.com", "qualcomm", "time-services",
      "https://github.com/qualcomm/time-services.git")),
    ("https://github.qualcomm.com/qosp/qnaro",
     ("github.qualcomm.com", "qosp", "qnaro",
      "https://github.qualcomm.com/qosp/qnaro.git")),
    ("git@github.com:qualcomm/time-services.git",
     ("github.com", "qualcomm", "time-services",
      "https://github.com/qualcomm/time-services.git")),
    ("github.com/qualcomm/time-services",
     ("github.com", "qualcomm", "time-services",
      "https://github.com/qualcomm/time-services.git")),
])
def test_parse_repo_url_valid(url, expected):
    assert js.parse_repo_url(url) == expected


@pytest.mark.parametrize("url", ["", "   ", "https://github.com/onlyowner", "not a url"])
def test_parse_repo_url_invalid(url):
    assert js.parse_repo_url(url) is None


# --- build_comment -----------------------------------------------------------

def test_build_comment_clean():
    body = js.build_comment("q/a", "BSD-3-Clause-Clear", {}, {},
                            scanned={"a.c", "b.py"}, ignored=set())
    assert "*Blocking files:* 0" in body
    assert "*Files scanned:* 2" in body
    assert "No license or copyright issues found." in body


def test_build_comment_sections_and_counts():
    flagged = {"src/x.c": {"license_issues": ["Incompatible license: GPL-2.0"],
                           "copyright_issues": ["No copyright statement found"]}}
    warning = {"src/y.c": {"license_issues": [
        "Uncertain license, review manually: LicenseRef-scancode-unknown"],
        "copyright_issues": []}}
    body = js.build_comment("q/a", "BSD-3-Clause-Clear", flagged, warning,
                            scanned={"src/x.c", "src/y.c"}, ignored={"vendor/z.c"})
    assert "*Blocking files:* 1" in body
    assert "*Warning files:* 1" in body
    assert "*Skipped by .licenseignore:* 1" in body
    assert "h4. Blocking issues (1)" in body
    assert "h4. Warnings (1)" in body
    assert "{{src/x.c}}" in body
    assert "Incompatible license: GPL-2.0" in body
    assert "No copyright statement found" in body


def test_build_comment_truncates_to_limit():
    # Many blocking files, each with an issue, forced under a tiny limit.
    flagged = {f"dir/file_{i:03d}.c": {
        "license_issues": ["Incompatible license: GPL-2.0"],
        "copyright_issues": ["No copyright statement found"]} for i in range(200)}
    limit = 1500
    body = js.build_comment("q/a", "BSD-3-Clause-Clear", flagged, {},
                            scanned=set(flagged), ignored=set(), limit=limit)
    assert len(body) <= limit
    assert "truncated to fit Jira's comment size limit" in body
    assert "*Blocking files:* 200" in body          # summary count stays intact
    assert "more file(s) omitted" in body


# --- build_error_comment -----------------------------------------------------

@pytest.mark.parametrize("category,needle", [
    ("missing_url", "missing on the ticket"),
    ("url_unparseable", "could not be parsed"),
    ("clone_failed", "could not be cloned"),
    ("scan_failed", "failed to run"),
    ("something_else", "Scan error"),
])
def test_build_error_comment(category, needle):
    body = js.build_error_comment(category, "detail here")
    assert "ERROR" in body
    assert needle in body
    assert "detail here" in body


# --- main() orchestration (CliRunner, all network/scan stubbed) --------------

@pytest.fixture
def jira_env(monkeypatch):
    monkeypatch.setenv("JIRA_API_SERVER", "https://jira-dc-tools.qualcomm.com/jira")
    monkeypatch.setenv("JIRA_USER", "u")
    monkeypatch.setenv("JIRA_PASSWORD", "p")
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("MAX_COMMENT_LENGTH", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    # No real connection / field lookup.
    monkeypatch.setattr(js, "build_client", lambda cfg: _FakeClient())
    monkeypatch.setattr(js, "resolve_url_field_id", lambda client, name: "customfield_101")


def _stub_scan(monkeypatch, flagged, warning):
    monkeypatch.setattr(js, "clone_repo", lambda *a, **k: (True, ""))
    monkeypatch.setattr(
        js, "run_full_scan",
        lambda *a, **k: ("BSD-3-Clause-Clear", flagged, warning, {"a.c"}, set()))


def _capture_posts(monkeypatch):
    posts = []
    monkeypatch.setattr(
        js, "post_comment",
        lambda client, key, body, visibility=None: posts.append((key, body, visibility)))
    return posts


# Point --env-file and --creds-file at paths that do not exist, so tests never read a
# real .env or ~/.jira-creds off the machine running them.
_NO_ENV = ["--env-file", "/nonexistent/.env", "--creds-file", "/nonexistent/.jira-creds"]


def test_main_dry_run_clean_posts_nothing(jira_env, monkeypatch):
    monkeypatch.setattr(js, "fetch_repo_url",
                        lambda client, key, fid: "https://github.com/q/a")
    _stub_scan(monkeypatch, flagged={}, warning={})
    posts = _capture_posts(monkeypatch)
    result = CliRunner().invoke(js.main, ["OSSOPS-1", "--dry-run"] + _NO_ENV)
    assert result.exit_code == 0
    assert posts == []                              # dry-run posts nothing
    assert "DRY RUN" in result.output
    assert "No license or copyright issues found." in result.output


def test_main_missing_url_posts_error_and_exits_1(jira_env, monkeypatch):
    monkeypatch.setattr(js, "fetch_repo_url", lambda client, key, fid: "")
    posts = _capture_posts(monkeypatch)
    result = CliRunner().invoke(js.main, ["OSSOPS-1"] + _NO_ENV)
    assert result.exit_code == 1
    assert len(posts) == 1
    assert "missing on the ticket" in posts[0][1]


def test_main_findings_posts_comment_exit_0(jira_env, monkeypatch):
    monkeypatch.setattr(js, "fetch_repo_url",
                        lambda client, key, fid: "https://github.com/q/a")
    flagged = {"x.c": {"license_issues": ["Incompatible license: GPL-2.0"],
                       "copyright_issues": []}}
    _stub_scan(monkeypatch, flagged=flagged, warning={})
    posts = _capture_posts(monkeypatch)
    result = CliRunner().invoke(js.main, ["OSSOPS-1"] + _NO_ENV)
    # Blocking findings, but default is report-only -> exit 0, comment posted.
    assert result.exit_code == 0
    assert len(posts) == 1
    assert "Incompatible license: GPL-2.0" in posts[0][1]
    assert posts[0][2] is None                      # public by default


def test_main_comment_visibility_group_flag_threads_through(jira_env, monkeypatch):
    monkeypatch.setattr(js, "fetch_repo_url",
                        lambda client, key, fid: "https://github.com/q/a")
    _stub_scan(monkeypatch, flagged={}, warning={})
    posts = _capture_posts(monkeypatch)
    result = CliRunner().invoke(
        js.main, ["OSSOPS-1", "--comment-visibility-group", "developers"] + _NO_ENV)
    assert result.exit_code == 0
    assert posts[0][2] == {"type": "group", "value": "developers"}


def test_main_comment_visibility_group_from_env(jira_env, monkeypatch):
    monkeypatch.setenv("JIRA_COMMENT_VISIBILITY_GROUP", "developers")
    monkeypatch.setattr(js, "fetch_repo_url",
                        lambda client, key, fid: "https://github.com/q/a")
    _stub_scan(monkeypatch, flagged={}, warning={})
    posts = _capture_posts(monkeypatch)
    result = CliRunner().invoke(js.main, ["OSSOPS-1"] + _NO_ENV)
    assert result.exit_code == 0
    assert posts[0][2] == {"type": "group", "value": "developers"}


def test_main_visibility_error_comment_is_also_restricted(jira_env, monkeypatch):
    # An error comment (e.g. missing URL) must carry the same restriction, so a
    # failure report is not exposed more widely than a results comment.
    monkeypatch.setattr(js, "fetch_repo_url", lambda client, key, fid: "")
    posts = _capture_posts(monkeypatch)
    result = CliRunner().invoke(
        js.main, ["OSSOPS-1", "--comment-visibility-group", "developers"] + _NO_ENV)
    assert result.exit_code == 1
    assert posts[0][2] == {"type": "group", "value": "developers"}


def test_main_fail_on_findings_exits_1(jira_env, monkeypatch):
    monkeypatch.setattr(js, "fetch_repo_url",
                        lambda client, key, fid: "https://github.com/q/a")
    flagged = {"x.c": {"license_issues": ["Incompatible license: GPL-2.0"],
                       "copyright_issues": []}}
    _stub_scan(monkeypatch, flagged=flagged, warning={})
    _capture_posts(monkeypatch)
    result = CliRunner().invoke(
        js.main, ["OSSOPS-1", "--fail-on-findings"] + _NO_ENV)
    assert result.exit_code == 1


def test_main_clone_failure_posts_error_exit_1(jira_env, monkeypatch):
    monkeypatch.setattr(js, "fetch_repo_url",
                        lambda client, key, fid: "https://github.com/q/a")
    monkeypatch.setattr(js, "clone_repo", lambda *a, **k: (False, "not found"))
    posts = _capture_posts(monkeypatch)
    result = CliRunner().invoke(js.main, ["OSSOPS-1"] + _NO_ENV)
    assert result.exit_code == 1
    assert len(posts) == 1
    assert "could not be cloned" in posts[0][1]


def test_main_missing_jira_config_exits_2(monkeypatch):
    for var in ("JIRA_API_SERVER", "JIRA_BASE_URL", "JIRA_USER", "JIRA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    result = CliRunner().invoke(js.main, ["OSSOPS-1"] + _NO_ENV)
    assert result.exit_code == 2
    assert "Missing Jira credentials" in result.output


def test_main_connect_failure_exits_2(jira_env, monkeypatch):
    def boom(cfg):
        raise js.JiraError("Could not connect to Jira at https://host: HTTP 401")
    monkeypatch.setattr(js, "build_client", boom)
    result = CliRunner().invoke(js.main, ["OSSOPS-1"] + _NO_ENV)
    assert result.exit_code == 2
    assert "Could not connect to Jira" in result.output

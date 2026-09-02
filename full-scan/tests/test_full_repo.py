# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import shutil
import subprocess

import pytest

from scanner import full_repo
from scanner.full_repo import RepoScan

# pytest passes a fixture into a test as a same-named parameter, which pylint reads
# as shadowing the module-level fixture (redefined-outer-name); the tiny IgnoreConfig
# stub below trips too-few-public-methods. Both are idiomatic in a test module.
# pylint: disable=redefined-outer-name,too-few-public-methods


class _FakeIgnore:
    """IgnoreConfig stand-in that never excludes -- keeps these tests independent
    of any .licenseignore that happens to be in the working directory."""

    def is_excluded(self, _file_path):
        return False


def _nul_stdout(paths):
    """Encode paths the way `git ls-files -z` does: NUL-TERMINATED (not separated),
    so the payload ends with a trailing NUL and no newlines anywhere."""
    return "".join(path_name + "\0" for path_name in paths)


@pytest.fixture
def fake_ls_files(monkeypatch):
    """Return a factory that stubs `git ls-files -z` to a canned list of paths and
    records the argv/kwargs it was called with. Takes a LIST because the code reads
    NUL-separated output; the fixture owns that encoding so no test has to.
    Also neutralizes .licenseignore loading."""
    captured = {}

    def _factory(paths):
        def _run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(
                cmd, 0, stdout=_nul_stdout(paths), stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        monkeypatch.setattr(full_repo, "IgnoreConfig", lambda *a, **k: _FakeIgnore())
        return captured

    return _factory


def test_default_uses_ls_files_tracked_only(fake_ls_files):
    captured = fake_ls_files(["a.c", "b.py"])
    repo = RepoScan()
    assert repo.get_files() == ["a.c", "b.py"]
    # Default scan is tracked-only: `git ls-files -z`, run against root with
    # check=True, and WITHOUT the untracked-widening flags. -z is required (see
    # test_quoted_path_would_be_dropped_without_z) and read with surrogateescape
    # so a non-UTF-8 filename cannot abort the scan with UnicodeDecodeError.
    assert captured["cmd"] == ["git", "ls-files", "-z"]
    assert captured["kwargs"].get("check") is True
    assert captured["kwargs"].get("cwd") == "."
    assert captured["kwargs"].get("errors") == "surrogateescape"
    assert "--others" not in captured["cmd"]


def test_include_untracked_adds_others_flags(fake_ls_files):
    captured = fake_ls_files(["a.c"])
    RepoScan(include_untracked=True)
    # -z applies to the widened form too, so untracked paths are unquoted as well.
    assert captured["cmd"] == [
        "git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]


def test_dot_S_included_dot_s_excluded(fake_ls_files):
    fake_ls_files(["a.S", "b.s"])
    files = RepoScan().get_files()
    # endswith is case-sensitive: .S (assembly) is a source extension, .s is not.
    assert "a.S" in files
    assert "b.s" not in files


def test_bb_is_excluded_not_license_optional(fake_ls_files):
    fake_ls_files(["recipe.bb", "class.bbclass", "app.bbappend", "keep.c"])
    repo = RepoScan()
    # .bb/.bbclass/.bbappend are EXCLUDED entirely (BitBake files are not scanned),
    # so only the real source file survives enumeration.
    assert repo.get_files() == ["keep.c"]
    # ...and they are NOT license-optional; only .mk/.bp are.
    assert repo.is_license_optional("recipe.bb") is False
    assert repo.is_license_optional("class.bbclass") is False
    assert repo.is_license_optional("app.bbappend") is False
    assert repo.is_license_optional("Android.mk") is True
    assert repo.is_license_optional("Android.bp") is True


def test_license_optional_files_by_basename(fake_ls_files):
    fake_ls_files([])
    repo = RepoScan()
    # __init__.py is license-optional at any depth (matched by basename), while a
    # normal source .py is fully checked.
    assert repo.is_license_optional("__init__.py") is True
    assert repo.is_license_optional("pkg/sub/__init__.py") is True
    assert repo.is_license_optional("full_scan.py") is False
    assert repo.is_license_optional("scanner/config.py") is False


def test_init_py_is_enumerated(fake_ls_files):
    fake_ls_files(["pkg/__init__.py", "pkg/mod.py", "README.md"])
    # __init__.py is still scanned (it is .py); README.md is excluded. The
    # license-optional tier only relaxes findings, it does not drop the file.
    assert RepoScan().get_files() == ["pkg/__init__.py", "pkg/mod.py"]


def test_licenseignore_read_from_root_not_cwd(tmp_path, monkeypatch):
    # .licenseignore must be resolved relative to the scanned tree (root=), NOT the
    # process cwd. Put the .licenseignore in root_dir, run from a DIFFERENT cwd that
    # has none, and confirm the pattern still excludes. (This uses the REAL
    # IgnoreConfig -- it does not stub it -- so it exercises the file lookup.)
    root_dir = tmp_path / "root"
    other_dir = tmp_path / "other"
    root_dir.mkdir()
    other_dir.mkdir()
    (root_dir / ".licenseignore").write_text("vendor/*\n", encoding="utf-8")
    monkeypatch.chdir(other_dir)          # cwd has NO .licenseignore

    # Stub only `git ls-files` (avoid needing a real repo); leave IgnoreConfig real.
    def _run(cmd, **_kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_nul_stdout(["vendor/skip.c", "src/keep.c"]), stderr="")
    monkeypatch.setattr(subprocess, "run", _run)

    repo = RepoScan(root=str(root_dir))
    # vendor/skip.c is excluded by root's .licenseignore (found via root, not cwd);
    # without the root-scoping fix, cwd has no .licenseignore so nothing is excluded.
    assert repo.get_files() == ["src/keep.c"]
    assert repo.get_ignored_files() == ["vendor/skip.c"]


def test_non_ascii_paths_are_enumerated(fake_ls_files):
    # -z hands back the real bytes, so a non-ASCII basename AND a plain basename
    # under a non-ASCII directory both reach the scan. .licenseignore matching also
    # sees the true path now, not a quoted one.
    fake_ls_files(["naïve.c", "café/plain.py", "plain.c"])
    assert RepoScan().get_files() == ["naïve.c", "café/plain.py", "plain.c"]


def test_quoted_path_would_be_dropped_without_z(fake_ls_files):
    # Documents the bug -z prevents, so nobody removes the flag: this is exactly what
    # git returns WITHOUT -z under the default core.quotePath=true. The wrapping quote
    # becomes part of the path, every endswith(SOURCE_FILE_EXTENSIONS) test fails, and
    # the file vanishes from the scan with no warning -- a silent false negative.
    fake_ls_files(['"caf\\303\\251/sub/main.c"', "plain.c"])
    assert RepoScan().get_files() == ["plain.c"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_non_ascii_path_enumerated_with_real_git(tmp_path, monkeypatch):
    # The one test that talks to real git. A stub can only replay the format its
    # author assumed, which is how a quoted path went unnoticed in the first place
    # (same lesson as the scancode per-match SPDX field rename): only real git
    # proves the -z contract.
    repo_dir = tmp_path / "repo"
    (repo_dir / "café").mkdir(parents=True)
    (repo_dir / "café" / "plain.c").write_text("int a;\n", encoding="utf-8")
    (repo_dir / "naïve.c").write_text("int b;\n", encoding="utf-8")
    (repo_dir / "plain.c").write_text("int c;\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    # Pin quotePath ON: it is git's default, but a developer or CI runner with it
    # disabled globally would hide the very behavior this test exists to cover.
    subprocess.run(["git", "config", "core.quotePath", "true"],
                   cwd=repo_dir, check=True)
    # git ls-files reads the index, so staging is enough -- no commit (and no
    # user.name/user.email config) needed.
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)

    monkeypatch.setattr(full_repo, "IgnoreConfig", lambda *a, **k: _FakeIgnore())
    files = RepoScan(root=str(repo_dir)).get_files()
    assert sorted(files) == ["café/plain.c", "naïve.c", "plain.c"]

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

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


@pytest.fixture
def fake_ls_files(monkeypatch):
    """Return a factory that stubs `git ls-files` to a canned stdout and records
    the argv/kwargs it was called with. Also neutralizes .licenseignore loading."""
    captured = {}

    def _factory(stdout):
        def _run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        monkeypatch.setattr(full_repo, "IgnoreConfig", lambda *a, **k: _FakeIgnore())
        return captured

    return _factory


def test_default_uses_ls_files_tracked_only(fake_ls_files):
    captured = fake_ls_files("a.c\nb.py\n")
    repo = RepoScan()
    assert repo.get_files() == ["a.c", "b.py"]
    # Default scan is tracked-only: plain `git ls-files`, run against root with
    # check=True, and WITHOUT the untracked-widening flags.
    assert captured["cmd"] == ["git", "ls-files"]
    assert captured["kwargs"].get("check") is True
    assert captured["kwargs"].get("cwd") == "."
    assert "--others" not in captured["cmd"]


def test_include_untracked_adds_others_flags(fake_ls_files):
    captured = fake_ls_files("a.c\n")
    RepoScan(include_untracked=True)
    assert captured["cmd"] == [
        "git", "ls-files", "--cached", "--others", "--exclude-standard"]


def test_dot_S_included_dot_s_excluded(fake_ls_files):
    fake_ls_files("a.S\nb.s\n")
    files = RepoScan().get_files()
    # endswith is case-sensitive: .S (assembly) is a source extension, .s is not.
    assert "a.S" in files
    assert "b.s" not in files


def test_bb_is_excluded_not_license_optional(fake_ls_files):
    fake_ls_files("recipe.bb\nclass.bbclass\napp.bbappend\nkeep.c\n")
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
    fake_ls_files("")
    repo = RepoScan()
    # __init__.py is license-optional at any depth (matched by basename), while a
    # normal source .py is fully checked.
    assert repo.is_license_optional("__init__.py") is True
    assert repo.is_license_optional("pkg/sub/__init__.py") is True
    assert repo.is_license_optional("full_scan.py") is False
    assert repo.is_license_optional("scanner/config.py") is False


def test_init_py_is_enumerated(fake_ls_files):
    fake_ls_files("pkg/__init__.py\npkg/mod.py\nREADME.md\n")
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
            cmd, 0, stdout="vendor/skip.c\nsrc/keep.c\n", stderr="")
    monkeypatch.setattr(subprocess, "run", _run)

    repo = RepoScan(root=str(root_dir))
    # vendor/skip.c is excluded by root's .licenseignore (found via root, not cwd);
    # without the root-scoping fix, cwd has no .licenseignore so nothing is excluded.
    assert repo.get_files() == ["src/keep.c"]
    assert repo.get_ignored_files() == ["vendor/skip.c"]

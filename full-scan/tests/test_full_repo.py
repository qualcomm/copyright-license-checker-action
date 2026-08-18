# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import subprocess

import pytest

import scanner.full_repo as full_repo
from scanner.full_repo import RepoScan


class _FakeIgnore:
    """IgnoreConfig stand-in that never excludes -- keeps these tests independent
    of any .licenseignore that happens to be in the working directory."""

    def is_excluded(self, file_path):
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

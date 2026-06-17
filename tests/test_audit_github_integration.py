"""Tests for github_integration audit hardening (batches 5, 9).

Covers:
  - _validate_gh_repo regex check (OWNER/NAME shape)                 (§3.12)
  - create_pr rejects --base value starting with '-'                 (§3.12)
  - create_pr pipes large bodies via --body-file - / stdin           (§2.16)
"""

from __future__ import annotations


import pytest

from harness import github_integration as ghi


# ---------------------------------------------------------------------------
# _validate_gh_repo (audit §3.12)
# ---------------------------------------------------------------------------


class TestValidateGhRepo:
    @pytest.mark.parametrize("repo", [
        "octocat/Hello-World", "a/b", "foo.bar/baz-qux", "user_name/repo_name",
    ])
    def test_accepts_well_formed(self, repo):
        ghi._validate_gh_repo(repo)  # no raise

    @pytest.mark.parametrize("bad", [
        "-flagy/repo",                # starts with -
        "--draft",                    # looks like a flag
        "no-slash",                   # missing /
        "/missing-owner",             # empty owner
        "owner/",                     # empty name
        ".hidden/repo",               # owner can't start with . per regex
    ])
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValueError):
            ghi._validate_gh_repo(bad)

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            ghi._validate_gh_repo(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# create_pr base-flag-injection refusal (audit §3.12)
# ---------------------------------------------------------------------------


def test_create_pr_rejects_base_starting_with_dash(monkeypatch):
    monkeypatch.setattr(ghi, "gh_path", lambda config=None: "/usr/bin/gh")
    with pytest.raises(ValueError, match="--base"):
        ghi.create_pr(
            workspace_path="/tmp",
            title="T", body="B", base="--draft",
        )


# ---------------------------------------------------------------------------
# Large body pipe via stdin (audit §2.16)
# ---------------------------------------------------------------------------


def test_create_pr_small_body_via_argv(monkeypatch):
    """Bodies under 64 KB go via --body argv as before — keeps existing
    test stubs working without mocking stdin."""

    monkeypatch.setattr(ghi, "gh_path", lambda config=None: "/usr/bin/gh")
    captured = {}

    class _Result:
        returncode = 0
        stdout = "https://github.com/o/r/pull/42\n"
        stderr = ""

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")
        return _Result()

    monkeypatch.setattr(ghi.subprocess, "run", _fake_run)
    ghi.create_pr(
        workspace_path="/tmp", title="t",
        body="short body", base="main",
    )
    assert "--body" in captured["cmd"]
    assert "--body-file" not in captured["cmd"]
    # Argv path passes nothing on stdin.
    assert captured["input"] is None


def test_create_pr_large_body_via_stdin_pipe(monkeypatch):
    """Bodies > 64 KB must use --body-file - + stdin so the call doesn't
    hit ARG_MAX on Linux (~128 KB)."""

    monkeypatch.setattr(ghi, "gh_path", lambda config=None: "/usr/bin/gh")
    captured = {}

    class _Result:
        returncode = 0
        stdout = "https://github.com/o/r/pull/99\n"
        stderr = ""

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")
        return _Result()

    monkeypatch.setattr(ghi.subprocess, "run", _fake_run)
    big_body = "x" * 100_000  # > 64 KB
    ghi.create_pr(
        workspace_path="/tmp", title="t",
        body=big_body, base="main",
    )
    assert "--body-file" in captured["cmd"]
    assert "-" in captured["cmd"]
    # The body went through stdin, NOT argv.
    assert captured["input"] == big_body
    assert big_body not in " ".join(captured["cmd"])

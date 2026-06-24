"""Tests for the per-batch git commit helper in harness/story_loop.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness.story_loop import _commit_for_batch, _commit_for_story


def _init_repo(path: str) -> None:
    """Bootstrap an empty git repo with author identity configured so
    the commit-creating tests don't fail on a clean container."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, check=True, capture_output=True,
    )
    # Quieten the "default branch" hint on newer gits.
    subprocess.run(
        ["git", "config", "init.defaultBranch", "main"],
        cwd=path, check=True, capture_output=True,
    )


def _git_log_messages(path: str) -> list[str]:
    # ``git log`` exits 128 on a fresh repo with no commits — that's not
    # an error for our purposes, it just means no messages yet.
    res = subprocess.run(
        ["git", "log", "--pretty=%s"],
        cwd=path, capture_output=True, text=True,
    )
    if res.returncode != 0:
        return []
    return [line for line in res.stdout.splitlines() if line]


@pytest.fixture
def workspace(tmp_path: Path) -> str:
    return str(tmp_path)


@pytest.fixture
def repo(workspace: str) -> str:
    _init_repo(workspace)
    return workspace


class TestNoGitRepo:
    def test_no_git_dir_returns_none(self, workspace: str):
        # workspace has no .git — must not raise, must return None.
        assert _commit_for_batch(workspace, 1, [("STORY-1", "Add auth")]) is None


class TestEmptyWorkingTree:
    def test_nothing_to_commit_returns_none(self, repo: str):
        # Fresh repo, no files added → status --porcelain is empty → no-op.
        assert _commit_for_batch(repo, 1, [("STORY-1", "noop")]) is None
        assert _git_log_messages(repo) == []


class TestSuccessfulCommit:
    def test_commits_with_batch_message_listing_stories(self, repo: str):
        (Path(repo) / "auth.py").write_text("def login(): ...\n")
        (Path(repo) / "users.py").write_text("def signup(): ...\n")
        sha = _commit_for_batch(
            repo, 3,
            [("STORY-1", "Add login"), ("STORY-2", "Add signup")],
        )
        assert sha is not None
        assert len(sha) == 40  # full SHA from rev-parse
        messages = _git_log_messages(repo)
        assert messages == [
            "BATCH-3: STORY-1: Add login; STORY-2: Add signup"
        ]

    def test_handles_empty_titles_gracefully(self, repo: str):
        (Path(repo) / "x.py").write_text("x\n")
        sha = _commit_for_batch(repo, 1, [("STORY-1", "")])
        assert sha is not None
        messages = _git_log_messages(repo)
        assert messages == ["BATCH-1: STORY-1"]

    def test_handles_empty_stories_list(self, repo: str):
        (Path(repo) / "x.py").write_text("x\n")
        sha = _commit_for_batch(repo, 5, [])
        assert sha is not None
        messages = _git_log_messages(repo)
        assert messages == ["BATCH-5: complete"]

    def test_per_story_commit_still_works(self, repo: str):
        """Sanity check: the original _commit_for_story path is intact
        during the transition (Phase F removes it)."""
        (Path(repo) / "y.py").write_text("y\n")
        sha = _commit_for_story(repo, "STORY-9", "Some title")
        assert sha is not None
        messages = _git_log_messages(repo)
        assert messages == ["STORY-9: Some title"]


class TestSequentialBatchCommits:
    def test_two_batches_make_two_commits(self, repo: str):
        (Path(repo) / "a.py").write_text("a\n")
        sha1 = _commit_for_batch(repo, 1, [("STORY-1", "first")])
        assert sha1 is not None
        (Path(repo) / "b.py").write_text("b\n")
        sha2 = _commit_for_batch(repo, 2, [("STORY-2", "second")])
        assert sha2 is not None
        assert sha1 != sha2
        messages = _git_log_messages(repo)
        # git log is newest-first.
        assert messages == [
            "BATCH-2: STORY-2: second",
            "BATCH-1: STORY-1: first",
        ]

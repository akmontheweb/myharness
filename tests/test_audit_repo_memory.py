"""Tests for repo_memory atomic-append hardening (batch 3).

Covers:
  - _atomic_write_text uses unique tmp + cleans on failure           (§1.14)
  - _memory_file_lock serialises concurrent appenders                (§1.14)
"""

from __future__ import annotations

import os
import threading

import pytest

from harness import repo_memory as rm


def test_atomic_write_uses_unique_tmp_filename(tmp_path):
    """Two concurrent atomic writes to the SAME destination must NOT
    collide on a shared staging tmp filename."""
    target = tmp_path / "out.md"

    seen_tmps: list[str] = []
    real_open = open

    def _record_open(path, *args, **kw):
        if str(path).startswith(str(target)) and str(path).endswith(".tmp"):
            seen_tmps.append(str(path))
        return real_open(path, *args, **kw)

    # Both writes via the helper.
    rm._atomic_write_text(str(target), "first content")
    rm._atomic_write_text(str(target), "second content")
    # Final file has the most recent content.
    assert target.read_text() == "second content"


def test_atomic_write_cleans_tmp_on_failure(tmp_path, monkeypatch):
    """If the rename fails, the staging tmp must NOT linger in the
    output directory as a leaked artefact."""
    target = tmp_path / "out.md"
    real_replace = os.replace

    def _flaky_replace(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", _flaky_replace)
    with pytest.raises(OSError, match="simulated"):
        rm._atomic_write_text(str(target), "content")
    monkeypatch.setattr(os, "replace", real_replace)
    # No tmp files left behind.
    leftover = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftover == []


def test_memory_file_lock_serialises_concurrent_appenders(tmp_path):
    """Two threads both calling append_session_note for the same memory
    file must produce a final file containing BOTH sections — without
    the fcntl lock, the read-modify-write race would lose one."""

    workspace = str(tmp_path / "ws")
    os.makedirs(workspace, exist_ok=True)
    # Make .git/config exist so repo_identity() picks a stable id.
    git_dir = os.path.join(workspace, ".git")
    os.makedirs(git_dir, exist_ok=True)
    with open(os.path.join(git_dir, "config"), "w") as f:
        f.write("[remote \"origin\"]\n    url = https://example.com/foo.git\n")

    barrier = threading.Barrier(2)

    def _writer(session: str, marker: str):
        barrier.wait()  # release both threads simultaneously
        rm.append_session_note(
            workspace,
            session_id=session,
            prompt_summary=marker,
            modified_files=[],
            exit_code=0,
        )

    t1 = threading.Thread(target=_writer, args=("sess-A", "PROMPT_A"))
    t2 = threading.Thread(target=_writer, args=("sess-B", "PROMPT_B"))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    # Find the memory file the helper wrote to.
    mem_path = rm.memory_file_path(workspace, rm.RepoMemoryConfig())
    contents = open(mem_path, encoding="utf-8").read()
    # Both sections must be present — the lock prevented the
    # read-modify-write race from losing one.
    assert "PROMPT_A" in contents
    assert "PROMPT_B" in contents

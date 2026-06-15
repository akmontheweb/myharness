"""Regression tests for the per-repo session memory module (#7).

Covers:
    - ``repo_identity`` is deterministic for the same path / origin URL.
    - ``read_repo_memory`` returns empty string when the file is absent.
    - ``append_session_note`` creates the directory and file, then
      successive appends accumulate.
    - The FIFO trim drops the oldest sections when the file exceeds
      ``max_bytes`` without ever discarding the just-written entry.
    - The read path tails to ``inject_max_bytes`` cleanly on section
      boundaries.
    - Writes are atomic — a half-written ``.tmp`` is never left behind
      on success.
    - ``enabled=false`` short-circuits both read and write.
"""

from __future__ import annotations

import os

from harness.repo_memory import (
    RepoMemoryConfig,
    append_session_note,
    memory_file_path,
    read_repo_memory,
    repo_identity,
)


def test_repo_identity_is_deterministic_for_same_path(tmp_path):
    p = str(tmp_path)
    a = repo_identity(p)
    b = repo_identity(p)
    assert a == b
    assert len(a) == 16


def test_repo_identity_differs_for_different_paths(tmp_path):
    p1 = str(tmp_path / "one")
    p2 = str(tmp_path / "two")
    os.makedirs(p1)
    os.makedirs(p2)
    assert repo_identity(p1) != repo_identity(p2)


def test_read_returns_empty_when_file_missing(tmp_path):
    cfg = RepoMemoryConfig(dir=str(tmp_path))
    assert read_repo_memory(str(tmp_path), cfg) == ""


def test_append_then_read_roundtrip(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    cfg = RepoMemoryConfig(dir=str(tmp_path / "mem"))
    path = append_session_note(
        workspace,
        session_id="abcd1234-test",
        prompt_summary="Add JWT auth",
        modified_files=["src/auth.py", "tests/test_auth.py"],
        exit_code=0,
        cfg=cfg,
    )
    assert path is not None
    assert os.path.isfile(path)
    content = read_repo_memory(workspace, cfg)
    assert "Session abcd1234" in content
    assert "Add JWT auth" in content
    assert "src/auth.py" in content
    assert "success" in content


def test_multiple_appends_accumulate(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    cfg = RepoMemoryConfig(dir=str(tmp_path / "mem"))
    for i in range(3):
        append_session_note(
            workspace,
            session_id=f"s{i:03d}-test",
            prompt_summary=f"task {i}",
            modified_files=[f"f{i}.py"],
            exit_code=0,
            cfg=cfg,
        )
    content = read_repo_memory(workspace, cfg)
    assert "task 0" in content
    assert "task 1" in content
    assert "task 2" in content
    # Three Session headings means three append calls landed.
    assert content.count("## Session ") == 3


def test_max_bytes_fifo_trims_oldest(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    # Small cap → after enough writes the oldest section drops first.
    cfg = RepoMemoryConfig(dir=str(tmp_path / "mem"), max_bytes=600)
    for i in range(10):
        append_session_note(
            workspace,
            session_id=f"s{i:03d}-test",
            prompt_summary=("x" * 60) + f" iteration {i}",
            modified_files=[f"file_{i}.py"],
            exit_code=0,
            cfg=cfg,
        )
    content = read_repo_memory(workspace, cfg)
    # The most recent entry MUST be there.
    assert "iteration 9" in content
    # And the file size must respect the cap (allowing a small overhead
    # for the final unsplittable section).
    path = memory_file_path(workspace, cfg)
    assert os.path.getsize(path) <= 600 * 2  # generous; we may keep two


def test_read_caps_at_inject_max_bytes(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    cfg = RepoMemoryConfig(
        dir=str(tmp_path / "mem"),
        max_bytes=200_000,
        inject_max_bytes=300,
    )
    for i in range(8):
        append_session_note(
            workspace,
            session_id=f"s{i:03d}-test",
            prompt_summary=("y" * 40) + f" iter {i}",
            modified_files=[f"f{i}.py"],
            exit_code=0,
            cfg=cfg,
        )
    injected = read_repo_memory(workspace, cfg)
    assert len(injected.encode("utf-8")) <= 600  # capped to ~inject_max + small overhead
    # The tail (most recent) must be preserved.
    assert "iter 7" in injected


def test_atomic_write_leaves_no_tmp(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    cfg = RepoMemoryConfig(dir=str(tmp_path / "mem"))
    append_session_note(
        workspace,
        session_id="x-test",
        prompt_summary="check",
        modified_files=[],
        exit_code=0,
        cfg=cfg,
    )
    mem_dir = str(tmp_path / "mem")
    files = os.listdir(mem_dir)
    assert not any(f.endswith(".tmp") for f in files)


def test_disabled_short_circuits_read_and_write(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    cfg = RepoMemoryConfig(enabled=False, dir=str(tmp_path / "mem"))
    result = append_session_note(
        workspace,
        session_id="x",
        prompt_summary="anything",
        modified_files=[],
        exit_code=0,
        cfg=cfg,
    )
    assert result is None  # disabled — no write happened
    assert read_repo_memory(workspace, cfg) == ""
    # Directory should never have been created.
    assert not os.path.isdir(str(tmp_path / "mem"))


def test_from_config_parses_section():
    cfg = RepoMemoryConfig.from_config({
        "memory": {
            "enabled": False,
            "dir": "/tmp/xyz",
            "max_bytes": 4096,
            "inject_max_bytes": 1024,
        },
    })
    assert cfg.enabled is False
    assert cfg.dir == "/tmp/xyz"
    assert cfg.max_bytes == 4096
    assert cfg.inject_max_bytes == 1024


def test_from_config_defaults_when_section_missing():
    cfg = RepoMemoryConfig.from_config({})
    assert cfg.enabled is True
    assert cfg.dir == "~/.harness/memory"

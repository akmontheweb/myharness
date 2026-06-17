"""Tests for observability + metrics audit hardening (batches 5, 7).

Covers:
  - configure_logging suffixes filename with PID on collision        (§5.12)
  - metrics.write_atomic uses unique tmp + fsyncs parent dir         (§5.11)
  - _sorted_session_log_files picks up <id>.<pid>.jsonl variants     (§5.12)
  - list_sessions deduplicates per-PID variants                      (§5.12)
"""

from __future__ import annotations

import os
import time

import pytest

from harness import metrics, observability


# ---------------------------------------------------------------------------
# observability: PID-in-filename when canonical log is active (§5.12)
# ---------------------------------------------------------------------------


def test_configure_logging_appends_pid_when_canonical_actively_written(tmp_path):
    """If <id>.jsonl exists AND its mtime is fresh (active writer), the
    second process gets <id>.<pid>.jsonl instead of racing on rotate."""
    log_dir = tmp_path
    sid = "shared-sess"
    canonical = log_dir / f"{sid}.jsonl"
    canonical.write_text("")  # mtime now ⇒ "active writer"
    # Touch with a fresh mtime to ensure age < 30s.
    os.utime(str(canonical), None)

    observability.configure_logging(
        session_id=sid,
        log_dir=str(log_dir),
        langsmith_enabled=False,
    )
    # Some file with our pid suffix should now exist in the log dir.
    pid = os.getpid()
    pid_variant = log_dir / f"{sid}.{pid}.jsonl"
    assert pid_variant.exists()


def test_configure_logging_reuses_canonical_when_stale(tmp_path):
    """When the canonical file is older than 30s (no live writer), the
    new process reuses it rather than creating a per-PID variant."""
    log_dir = tmp_path
    sid = "lonely-sess"
    canonical = log_dir / f"{sid}.jsonl"
    canonical.write_text("")
    # Backdate mtime well beyond the 30-second freshness window.
    past = time.time() - 600.0
    os.utime(str(canonical), (past, past))

    observability.configure_logging(
        session_id=sid,
        log_dir=str(log_dir),
        langsmith_enabled=False,
    )
    # No per-PID variant created.
    pid_variant = log_dir / f"{sid}.{os.getpid()}.jsonl"
    assert not pid_variant.exists()


# ---------------------------------------------------------------------------
# metrics.write_atomic unique tmp + dir fsync (audit §5.11)
# ---------------------------------------------------------------------------


def test_write_atomic_succeeds_and_leaves_no_tmp(tmp_path):
    dest = tmp_path / "out.txt"
    metrics.write_atomic(str(dest), "hello\n")
    assert dest.read_text() == "hello\n"
    # No tmp files leaked.
    leftover = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftover == []


def test_write_atomic_cleans_tmp_on_failure(tmp_path, monkeypatch):
    dest = tmp_path / "out.txt"
    real_replace = os.replace

    def _fail(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", _fail)
    with pytest.raises(OSError, match="simulated"):
        metrics.write_atomic(str(dest), "content")
    monkeypatch.setattr(os, "replace", real_replace)
    leftover = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftover == []


def test_write_atomic_unique_tmp_per_pid(tmp_path, monkeypatch):
    """Two concurrent writers must not collide on a shared <dest>.tmp."""
    dest = tmp_path / "out.txt"
    seen_tmps: list[str] = []
    real_open = open

    def _spy_open(path, *a, **kw):
        if str(path).startswith(str(dest)) and str(path).endswith(".tmp"):
            seen_tmps.append(str(path))
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", _spy_open)
    metrics.write_atomic(str(dest), "a")
    metrics.write_atomic(str(dest), "b")
    # Each invocation chose a uuid-distinguished tmp filename.
    assert len(seen_tmps) == 2
    assert seen_tmps[0] != seen_tmps[1]


# ---------------------------------------------------------------------------
# _sorted_session_log_files / list_sessions discover PID variants (§5.12)
# ---------------------------------------------------------------------------


def test_sorted_session_log_files_picks_up_pid_variants(tmp_path):
    sid = "ssn"
    # Canonical + rotated.
    (tmp_path / f"{sid}.jsonl").write_text("primary\n")
    (tmp_path / f"{sid}.jsonl.1").write_text("rotated1\n")
    # PID variants.
    (tmp_path / f"{sid}.12345.jsonl").write_text("pid_12345\n")
    (tmp_path / f"{sid}.67890.jsonl").write_text("pid_67890\n")
    files = metrics._sorted_session_log_files(sid, str(tmp_path))
    basenames = [os.path.basename(f) for f in files]
    assert f"{sid}.jsonl" in basenames
    assert f"{sid}.jsonl.1" in basenames
    assert f"{sid}.12345.jsonl" in basenames
    assert f"{sid}.67890.jsonl" in basenames


def test_list_sessions_dedupes_pid_variants(tmp_path):
    """A single session that produced both <sid>.jsonl and
    <sid>.<pid>.jsonl files must appear exactly ONCE in list_sessions."""
    (tmp_path / "myrun.jsonl").write_text("")
    (tmp_path / "myrun.555.jsonl").write_text("")
    (tmp_path / "myrun.jsonl.1").write_text("")
    sessions = metrics.list_sessions(str(tmp_path))
    assert sessions == ["myrun"]


def test_list_sessions_distinct_session_ids(tmp_path):
    (tmp_path / "alpha.jsonl").write_text("")
    (tmp_path / "beta.111.jsonl").write_text("")
    (tmp_path / "gamma.jsonl.2").write_text("")
    sessions = metrics.list_sessions(str(tmp_path))
    assert sorted(sessions) == ["alpha", "beta", "gamma"]

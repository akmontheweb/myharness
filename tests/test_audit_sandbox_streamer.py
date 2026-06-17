"""Tests for DiskLogStreamer aging janitor (batch 3).

Covers:
  - _janitor_sweep_old_logs deletes harness_* files older than retention  (§2.9)
  - keeps recent files untouched
  - non-harness files in temp_dir are NOT touched
"""

from __future__ import annotations

import os
import time

import pytest

from harness.sandbox import DiskLogStreamer


def _touch_old(path, age_days: float) -> None:
    """Create ``path`` and rewind its mtime by ``age_days``."""
    with open(path, "wb") as f:
        f.write(b"")
    past = time.time() - (age_days * 86400.0)
    os.utime(path, (past, past))


def test_janitor_removes_old_harness_logs(tmp_path):
    streamer = DiskLogStreamer(temp_dir=str(tmp_path))
    # Old harness_*.std{out,err}.log files.
    old_a = tmp_path / "harness_abc.stdout.log"
    old_b = tmp_path / "harness_xyz.stderr.log"
    _touch_old(str(old_a), age_days=14.0)
    _touch_old(str(old_b), age_days=10.0)

    streamer._janitor_sweep_old_logs()
    assert not old_a.exists()
    assert not old_b.exists()


def test_janitor_preserves_recent_harness_logs(tmp_path):
    streamer = DiskLogStreamer(temp_dir=str(tmp_path))
    fresh = tmp_path / "harness_fresh.stdout.log"
    _touch_old(str(fresh), age_days=0.1)  # ~2.4h old

    streamer._janitor_sweep_old_logs()
    assert fresh.exists()


def test_janitor_ignores_unrelated_files(tmp_path):
    streamer = DiskLogStreamer(temp_dir=str(tmp_path))
    # File that doesn't match the harness_* prefix and shouldn't be touched.
    foreign = tmp_path / "operator_logs.txt"
    _touch_old(str(foreign), age_days=30.0)
    # File that matches harness_* but doesn't have the .std{out,err}.log suffix.
    weird = tmp_path / "harness_other.log"
    _touch_old(str(weird), age_days=30.0)

    streamer._janitor_sweep_old_logs()
    # Both survive — the janitor is conservative.
    assert foreign.exists()
    assert weird.exists()


def test_janitor_zero_retention_skips_sweep(tmp_path, monkeypatch):
    """Setting _LOG_RETENTION_DAYS = 0 disables the janitor entirely."""
    monkeypatch.setattr(DiskLogStreamer, "_LOG_RETENTION_DAYS", 0)
    streamer = DiskLogStreamer(temp_dir=str(tmp_path))
    old = tmp_path / "harness_abc.stdout.log"
    _touch_old(str(old), age_days=365.0)
    streamer._janitor_sweep_old_logs()
    assert old.exists()


@pytest.mark.asyncio
async def test_streamer_open_invokes_janitor(tmp_path, monkeypatch):
    """The streamer's open() should call the janitor as a side-effect."""
    called = {"n": 0}

    real = DiskLogStreamer._janitor_sweep_old_logs

    def _wrapped(self):
        called["n"] += 1
        return real(self)

    monkeypatch.setattr(DiskLogStreamer, "_janitor_sweep_old_logs", _wrapped)
    streamer = DiskLogStreamer(temp_dir=str(tmp_path))
    await streamer.open()
    try:
        assert called["n"] == 1
    finally:
        await streamer.close()

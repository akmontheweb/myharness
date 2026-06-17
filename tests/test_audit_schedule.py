"""Tests for the schedule-layer audit hardening (batches 2, 3, 9).

Covers:
  - _ensure_schedule_pid_column migration                            (§1.5)
  - record_run_started PK collision bump                             (§1.16)
  - find_inflight_runs / reap_orphan_run                             (§1.5)
  - ScheduleDaemon._reconcile_inflight_history                       (§1.5)
  - ScheduleDaemon._drain_inflight_subprocesses                      (§2.1)
  - _fire_oneshot uses claim_oneshot_job atomically                  (§1.1)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from harness import schedule as sched


def _cfg(tmp_path) -> sched.ScheduleConfig:
    """Build a minimal ScheduleConfig pointing at a per-test temp dir."""
    return sched.ScheduleConfig(
        jobs=[],
        history_db=str(tmp_path / "schedule.db"),
        log_dir=str(tmp_path / "logs"),
    )


# ---------------------------------------------------------------------------
# Schema migration — pid column added on existing DB (audit §1.5)
# ---------------------------------------------------------------------------


def test_ensure_schedule_pid_column_adds_missing_column(tmp_path):
    """Older schedule_runs tables lacked the `pid` column; opening the
    DB must alter-table-add-column so daemon reconciliation works."""
    db = tmp_path / "schedule.db"
    # Seed an older-shape table without `pid`.
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE schedule_runs (
            job_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            exit_code INTEGER,
            duration_sec REAL,
            log_path TEXT,
            PRIMARY KEY (job_name, started_at)
        )
    """)
    conn.execute("INSERT INTO schedule_runs (job_name, started_at) VALUES ('legacy', '2024-01-01T00:00:00+00:00')")
    conn.commit()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(schedule_runs)")}
    assert "pid" not in cols
    conn.close()

    cfg = sched.ScheduleConfig(jobs=[], history_db=str(db), log_dir=str(tmp_path / "logs"))
    conn = sched._open_history(cfg)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(schedule_runs)")}
        assert "pid" in cols
        # Legacy row preserved.
        rows = conn.execute("SELECT job_name, pid FROM schedule_runs").fetchall()
        assert rows == [("legacy", None)]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# record_run_started PK collision bump (audit §1.16)
# ---------------------------------------------------------------------------


class TestRecordRunStartedCollision:
    def test_two_runs_same_second_both_persist(self, tmp_path):
        cfg = _cfg(tmp_path)
        now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
        sched.record_run_started(
            cfg, job_name="job-A", started_at=now, log_path="/tmp/a.log",
        )
        # Different job_name, SAME second — no collision (different PK).
        sched.record_run_started(
            cfg, job_name="job-B", started_at=now, log_path="/tmp/b.log",
        )
        # Same job_name, same second — collision should bump by 1µs.
        sched.record_run_started(
            cfg, job_name="job-A", started_at=now, log_path="/tmp/a2.log",
        )
        conn = sched._open_history(cfg)
        try:
            rows = conn.execute(
                "SELECT job_name, started_at, log_path FROM schedule_runs "
                "WHERE job_name = 'job-A' ORDER BY started_at"
            ).fetchall()
        finally:
            conn.close()
        # Both job-A runs persisted with distinct timestamps.
        assert len(rows) == 2
        assert rows[0][2] == "/tmp/a.log"
        assert rows[1][2] == "/tmp/a2.log"

    def test_re_stamp_with_pid_replaces_first_row(self, tmp_path):
        """The same logical run stamps started_at first without pid, then
        again with pid. The second call should REPLACE (not bump) so the
        single run gets its pid recorded, not duplicated."""
        cfg = _cfg(tmp_path)
        now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
        sched.record_run_started(cfg, job_name="J", started_at=now, log_path="/tmp/j.log")
        # Re-stamp with pid (same run learning its own pid post-spawn).
        sched.record_run_started(
            cfg, job_name="J", started_at=now, log_path="/tmp/j.log", pid=12345,
        )
        conn = sched._open_history(cfg)
        try:
            rows = conn.execute(
                "SELECT pid FROM schedule_runs WHERE job_name = 'J'"
            ).fetchall()
        finally:
            conn.close()
        # Exactly ONE row, with the pid set.
        assert rows == [(12345,)]


# ---------------------------------------------------------------------------
# find_inflight_runs / reap_orphan_run (audit §1.5)
# ---------------------------------------------------------------------------


def test_find_inflight_runs_returns_only_unfinished(tmp_path):
    cfg = _cfg(tmp_path)
    t1 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 17, 12, 1, 0, tzinfo=timezone.utc)
    sched.record_run_started(cfg, job_name="A", started_at=t1, log_path="/a", pid=111)
    sched.record_run_started(cfg, job_name="B", started_at=t2, log_path="/b", pid=222)
    sched.record_run_finished(
        cfg, job_name="A", started_at=t1, exit_code=0, duration_sec=1.0,
    )
    inflight = sched.find_inflight_runs(cfg)
    assert len(inflight) == 1
    assert inflight[0]["job_name"] == "B"
    assert inflight[0]["pid"] == 222


def test_reap_orphan_run_marks_terminated(tmp_path):
    cfg = _cfg(tmp_path)
    t = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    sched.record_run_started(cfg, job_name="X", started_at=t, log_path="/x", pid=99)
    sched.reap_orphan_run(cfg, job_name="X", started_at=t.isoformat())
    inflight = sched.find_inflight_runs(cfg)
    # After reap, no in-flight rows remain (ended_at is set, pid NULLed).
    assert inflight == []


# ---------------------------------------------------------------------------
# ScheduleDaemon._reconcile_inflight_history (audit §1.5)
# ---------------------------------------------------------------------------


def test_reconcile_inflight_history_reaps_dead_pid(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    t = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    sched.record_run_started(
        cfg, job_name="dead-pid-job", started_at=t, log_path="/x", pid=987654321,
    )
    # Force _pid_alive_int to report all pids as dead.
    monkeypatch.setattr(sched, "_pid_alive_int", lambda _pid: False)
    daemon = sched.ScheduleDaemon(cfg)
    daemon._reconcile_inflight_history()
    # The reaped row should no longer be in flight, and the job is NOT
    # in _in_flight (so the daemon won't think it's still running).
    assert "dead-pid-job" not in daemon._in_flight
    inflight = sched.find_inflight_runs(cfg)
    assert inflight == []


def test_reconcile_inflight_history_adopts_alive_pid(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    t = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    sched.record_run_started(
        cfg, job_name="alive-job", started_at=t, log_path="/x", pid=12345,
    )
    monkeypatch.setattr(sched, "_pid_alive_int", lambda _pid: True)
    daemon = sched.ScheduleDaemon(cfg)
    daemon._reconcile_inflight_history()
    # Daemon adopts the still-running job: marked in_flight so the next
    # tick won't fire a duplicate.
    assert "alive-job" in daemon._in_flight


# ---------------------------------------------------------------------------
# _drain_inflight_subprocesses (audit §2.1)
# ---------------------------------------------------------------------------


def test_drain_inflight_signals_living_pids(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    t = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    sched.record_run_started(
        cfg, job_name="live", started_at=t, log_path="/x", pid=11111,
    )

    alive_states = {11111: True}
    signals: list[tuple[int, int]] = []

    def _pid_alive(pid: int) -> bool:
        return alive_states.get(pid, False)

    def _fake_kill(pid, sig):
        signals.append((pid, sig))
        # After SIGTERM, mark the pid dead so the SIGKILL loop doesn't fire.
        alive_states[pid] = False

    def _fake_killpg(pgid, sig):
        signals.append((pgid, sig))
        alive_states[pgid] = False

    monkeypatch.setattr(sched, "_pid_alive_int", _pid_alive)
    monkeypatch.setattr(sched.os, "kill", _fake_kill)
    monkeypatch.setattr(sched.os, "killpg", _fake_killpg)
    # Force the kill path to NOT find a pgid → falls back to os.kill.
    monkeypatch.setattr(sched.os, "getpgid", lambda _pid: (_ for _ in ()).throw(ProcessLookupError()))

    daemon = sched.ScheduleDaemon(cfg)
    daemon._drain_inflight_subprocesses()
    # At least one SIGTERM was sent.
    import signal as _signal
    sigs_sent = {sig for _pid, sig in signals}
    assert _signal.SIGTERM in sigs_sent


# ---------------------------------------------------------------------------
# _fire_oneshot atomic claim (audit §1.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_oneshot_skips_when_claim_lost(tmp_path, monkeypatch):
    """Two daemons reading the same web_oneshot_jobs row — only one
    should fire; the other gets ``claimed=False`` and returns
    skipped=already_consumed."""
    cfg = sched.ScheduleConfig(
        jobs=[], history_db=str(tmp_path / "schedule.db"),
        log_dir=str(tmp_path / "logs"),
        web_db_path=str(tmp_path / "web.db"),
    )
    daemon = sched.ScheduleDaemon(cfg)

    # Patch claim_oneshot_job to always return False (simulating loser).
    from harness import web_state as ws_mod
    monkeypatch.setattr(ws_mod, "claim_oneshot_job", lambda **kw: False)
    # execute_job_once must NOT be called.
    called = {"n": 0}

    async def _no_run(*a, **kw):
        called["n"] += 1
        return {}

    monkeypatch.setattr(sched, "execute_job_once", _no_run)

    row = {"id": 1, "name": "x", "workspace": "/tmp", "prompt": "", "harness_args": []}
    result = await daemon._fire_oneshot(row)
    assert result.get("skipped") == "already_consumed"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_fire_oneshot_executes_when_claim_won(tmp_path, monkeypatch):
    cfg = sched.ScheduleConfig(
        jobs=[], history_db=str(tmp_path / "schedule.db"),
        log_dir=str(tmp_path / "logs"),
        web_db_path=str(tmp_path / "web.db"),
    )
    daemon = sched.ScheduleDaemon(cfg)
    from harness import web_state as ws_mod
    monkeypatch.setattr(ws_mod, "claim_oneshot_job", lambda **kw: True)
    called = {"n": 0}

    async def _ran(_cfg, _job, now=None):
        called["n"] += 1
        return {"exit_code": 0}

    monkeypatch.setattr(sched, "execute_job_once", _ran)

    row = {"id": 7, "name": "x", "workspace": "/tmp", "prompt": "", "harness_args": []}
    result = await daemon._fire_oneshot(row)
    assert called["n"] == 1
    assert result["oneshot_id"] == 7

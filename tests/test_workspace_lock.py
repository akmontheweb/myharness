"""P1.7 regression: workspace-level advisory lock prevents two concurrent
`teane run` invocations against the same workspace from clobbering each
other's patches.

The lock is process-scoped via fcntl. We exercise it across two
subprocesses because two flock acquisitions inside the same process from
the same fd succeed (the second one is a no-op on the same lock owner),
which would hide the regression.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest


@pytest.fixture(autouse=True)
def _release_module_lock():
    """Some tests above might have left a lock on the module-level slot
    (e.g. when running the suite in the same interpreter). Clear it so
    each test starts fresh. The lock is keyed per-workspace path now
    (see audit §1.9) so we drop every entry rather than overwriting a
    single singleton."""
    import harness.cli as cli_mod
    if hasattr(cli_mod, "_WORKSPACE_LOCK_HANDLES"):
        cli_mod._WORKSPACE_LOCK_HANDLES.clear()
    yield
    if hasattr(cli_mod, "_WORKSPACE_LOCK_HANDLES"):
        cli_mod._WORKSPACE_LOCK_HANDLES.clear()


def _spawn_lock_holder(workspace: str, sleep_seconds: float = 3.0) -> subprocess.Popen:
    """Spawn a tiny Python subprocess that acquires the lock and then sleeps.

    Returns the Popen handle; the caller is responsible for killing it.
    """
    script = (
        "import sys, time, os;"
        "sys.path.insert(0, os.environ['HARNESS_REPO']);"
        "from harness.cli import _acquire_workspace_lock;"
        f"h = _acquire_workspace_lock({workspace!r}, force=False);"
        "assert h not in (False, None), 'first holder should succeed';"
        "print('LOCKED', flush=True);"
        f"time.sleep({sleep_seconds})"
    )
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "HARNESS_REPO": repo},
        text=True,
    )
    # Wait for the holder to confirm it acquired the lock.
    for _ in range(30):
        line = (proc.stdout.readline() or "").strip()
        if line == "LOCKED":
            return proc
        time.sleep(0.1)
    proc.kill()
    raise RuntimeError("lock holder subprocess never reported LOCKED")


def test_second_session_refused_without_force(tmp_path):
    """A second acquire on a workspace already held by another process
    must return False (operator-facing 'lock held' refusal)."""
    if sys.platform == "win32":
        pytest.skip("fcntl unavailable on Windows native")

    workspace = str(tmp_path)
    holder = _spawn_lock_holder(workspace, sleep_seconds=3.0)
    try:
        from harness.cli import _acquire_workspace_lock
        result = _acquire_workspace_lock(workspace, force=False)
        assert result is False, "second session must be refused when lock held"
    finally:
        holder.kill()
        holder.wait(timeout=5)


def test_force_lock_succeeds_even_when_held(tmp_path):
    """--force-lock takes the lock anyway. Operator owns the resulting
    risk; this exists for the recovery case where a crash stranded the
    lock. We don't test the recovery path itself here — just that the
    bypass actually works."""
    if sys.platform == "win32":
        pytest.skip("fcntl unavailable on Windows native")

    workspace = str(tmp_path)
    holder = _spawn_lock_holder(workspace, sleep_seconds=2.0)
    try:
        # The holder will release in ~2s; force_lock waits for the lock and
        # should succeed once the holder exits, but we kill the holder so
        # we're not racing the timing.
        time.sleep(0.2)
        holder.kill()
        holder.wait(timeout=5)
        from harness.cli import _acquire_workspace_lock
        result = _acquire_workspace_lock(workspace, force=True)
        assert result is not False
        assert result is not None
    finally:
        if holder.poll() is None:
            holder.kill()


def test_unlocked_workspace_lock_succeeds(tmp_path):
    """Sanity: no holder → first acquire returns the handle."""
    if sys.platform == "win32":
        pytest.skip("fcntl unavailable on Windows native")

    workspace = str(tmp_path)
    from harness.cli import _acquire_workspace_lock
    result = _acquire_workspace_lock(workspace, force=False)
    assert result not in (False, None)
    # Lock file actually exists.
    assert os.path.isfile(os.path.join(workspace, ".harness_session.lock"))


# ---------------------------------------------------------------------------
# Audit §1.9 — truncate-AFTER-flock + per-workspace handle dict
# ---------------------------------------------------------------------------


def test_pid_written_after_lock_acquired(tmp_path):
    """The lock file should carry the holder's pid, written AFTER flock —
    the earlier 'open mode=w' truncated BEFORE flock, so concurrent
    acquirers could wipe the holder's diagnostic line."""
    if sys.platform == "win32":
        pytest.skip("fcntl unavailable on Windows native")
    import harness.cli as cli_mod
    workspace = str(tmp_path)
    fh = cli_mod._acquire_workspace_lock(workspace, force=False)
    assert fh not in (False, None)
    contents = open(os.path.join(workspace, ".harness_session.lock"), encoding="utf-8").read()
    assert f"pid={os.getpid()}" in contents


def test_two_distinct_workspaces_share_module_slot_dict(tmp_path, monkeypatch):
    """The handle dict is keyed by workspace realpath so acquiring two
    different workspaces in the same process doesn't overwrite the first
    lock's handle (which would let GC release the fcntl lock)."""
    if sys.platform == "win32":
        pytest.skip("fcntl unavailable on Windows native")
    import harness.cli as cli_mod
    ws_a = tmp_path / "wa"
    ws_b = tmp_path / "wb"
    ws_a.mkdir()
    ws_b.mkdir()
    fh_a = cli_mod._acquire_workspace_lock(str(ws_a), force=False)
    fh_b = cli_mod._acquire_workspace_lock(str(ws_b), force=False)
    assert fh_a not in (False, None)
    assert fh_b not in (False, None)
    # Per-path dict: both handles must survive in the module slot.
    assert os.path.realpath(str(ws_a)) in cli_mod._WORKSPACE_LOCK_HANDLES
    assert os.path.realpath(str(ws_b)) in cli_mod._WORKSPACE_LOCK_HANDLES


def test_open_does_not_truncate_lock_file_before_flock(tmp_path):
    """Regression: pre-write a sentinel line into the lock file and verify
    a successful acquire still ends with our pid line (the pid is written
    AFTER flock; the seek+truncate explicitly happens under the lock)."""
    if sys.platform == "win32":
        pytest.skip("fcntl unavailable on Windows native")
    import harness.cli as cli_mod
    workspace = str(tmp_path)
    lock_path = os.path.join(workspace, ".harness_session.lock")
    open(lock_path, "w", encoding="utf-8").write("sentinel-before-acquire\n")
    fh = cli_mod._acquire_workspace_lock(workspace, force=False)
    assert fh not in (False, None)
    contents = open(lock_path, encoding="utf-8").read()
    # Sentinel is gone (we truncated AFTER flock); our pid line is present.
    assert "sentinel-before-acquire" not in contents
    assert f"pid={os.getpid()}" in contents

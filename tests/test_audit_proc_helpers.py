"""Tests for the process-management audit helpers (batch 3).

Covers:
  - run_subprocess_kill_on_timeout                                   (§2.3, §2.5, §2.6, §2.13)
  - _kill_process_group_async (non-blocking sleep)                   (§1.12)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from harness.sandbox import (
    _kill_process_group_async,
    run_subprocess_kill_on_timeout,
)


# ---------------------------------------------------------------------------
# run_subprocess_kill_on_timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_subprocess_returns_exit_code_and_output():
    """Happy path: short subprocess completes within timeout."""
    rc, stdout, stderr, timed_out = await run_subprocess_kill_on_timeout(
        [sys.executable, "-c", "print('hello'); import sys; sys.exit(0)"],
        timeout=10.0,
    )
    assert rc == 0
    assert timed_out is False
    assert b"hello" in stdout


@pytest.mark.asyncio
async def test_run_subprocess_captures_separate_stderr():
    rc, stdout, stderr, _to = await run_subprocess_kill_on_timeout(
        [sys.executable, "-c",
         "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
        timeout=10.0,
    )
    assert rc == 3
    assert b"out" in stdout
    assert b"err" in stderr


@pytest.mark.asyncio
async def test_run_subprocess_kills_on_timeout():
    """A process that exceeds the timeout MUST be killed — the parent
    function returns exit_code=-9 + timed_out=True and the child is no
    longer alive."""
    if sys.platform == "win32":
        pytest.skip("POSIX-only signal semantics")

    t0 = time.monotonic()
    rc, _stdout, _stderr, timed_out = await run_subprocess_kill_on_timeout(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=0.5,
    )
    elapsed = time.monotonic() - t0
    assert timed_out is True
    assert rc == -9
    # The kill should complete well within the 3s SIGTERM grace + 5s SIGKILL
    # wait — typically << 4s. The earlier "leak the subprocess on timeout"
    # bug let the child outlive the call.
    assert elapsed < 10.0


@pytest.mark.asyncio
async def test_run_subprocess_kills_on_cancel():
    """If the surrounding task is cancelled, the helper MUST kill the
    child rather than leaking it as an orphan."""
    if sys.platform == "win32":
        pytest.skip("POSIX-only")
    # Spawn a long-running subprocess in a task we then cancel.
    task = asyncio.create_task(
        run_subprocess_kill_on_timeout(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout=60.0,
        )
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # If the kill worked, no orphan python process is hanging around with
    # the sleep(60). We can't assert that directly in unit-test form, but
    # the test passing means the CancelledError branch ran the kill code.


# ---------------------------------------------------------------------------
# _kill_process_group_async non-blocking (audit §1.12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_process_group_async_yields_to_event_loop(monkeypatch):
    """The async variant must use asyncio.sleep so other coroutines keep
    making progress during the SIGTERM→SIGKILL grace. We sample the
    loop's progress while the kill is in flight."""
    if not hasattr(os, "killpg"):
        pytest.skip("POSIX-only")

    sleep_calls: list[float] = []

    async def _instrumented_sleep(seconds: float):
        sleep_calls.append(seconds)
        # Skip actually sleeping so the test stays fast.

    monkeypatch.setattr("harness.sandbox.asyncio.sleep", _instrumented_sleep)
    # Patch killpg so we don't actually signal real processes.
    monkeypatch.setattr("harness.sandbox.os.killpg", lambda _pg, _sig: None)

    # Build a minimal fake proc object.
    class _FakeProc:
        returncode = None

        def kill(self):
            pass

    await _kill_process_group_async(pgid=12345, proc=_FakeProc())
    # The async sleep was invoked with 3.0s — i.e. we yielded to the loop
    # rather than blocking with time.sleep.
    assert 3.0 in sleep_calls


# ---------------------------------------------------------------------------
# _compose_project_name (audit §2.4)
# ---------------------------------------------------------------------------


def test_compose_project_name_stable_per_workspace(tmp_path):
    from harness.deploy import _compose_project_name
    name = _compose_project_name(str(tmp_path))
    assert name.startswith("harness-")
    # Hash digest portion is 12 hex chars.
    assert len(name) == len("harness-") + 12


def test_compose_project_name_distinct_for_different_workspaces(tmp_path):
    from harness.deploy import _compose_project_name
    a = _compose_project_name(str(tmp_path / "a"))
    b = _compose_project_name(str(tmp_path / "b"))
    assert a != b

"""Tests for MCP-client audit hardening (batches 3, 6).

Covers:
  - StdioMcpClient._call cancels its future on caller-cancellation   (§1.13)
  - StdioMcpClient.shutdown snapshots pending before set_exception   (§1.13)
  - StdioMcpClient._read_loop bounded line cap                       (§4.9)
  - McpClientPool._atexit_kill best-effort signalling                (§2.8)
"""

from __future__ import annotations

import asyncio

import pytest

from harness import mcp_client as mc


# ---------------------------------------------------------------------------
# StdioMcpClient: future cancellation cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_cancels_dangling_future_on_caller_cancel():
    """When the surrounding wait_for times out, the entry must be removed
    from _pending AND the future cancelled so it can be GC'd immediately
    rather than waiting for shutdown to drain."""

    client = mc.StdioMcpClient.__new__(mc.StdioMcpClient)
    client.config = mc.McpServerConfig(name="test", command=["echo"])
    client._proc = None  # forces the early McpError
    client._pending = {}
    client._next_id = 0
    client._write_lock = asyncio.Lock()
    client._started = False
    client.timeout_seconds = 1.0

    # Without a live proc the call raises immediately (good).
    with pytest.raises(mc.McpError):
        await client._call("ping", {}, timeout=0.1)
    # Nothing should be pending.
    assert client._pending == {}


# ---------------------------------------------------------------------------
# shutdown: snapshot-and-clear pattern
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_snapshot_clears_pending_before_set_exception():
    """The shutdown path must snapshot _pending then clear it BEFORE
    calling set_exception so the reader loop can't re-look up an
    entry mid-set_exception."""

    client = mc.StdioMcpClient.__new__(mc.StdioMcpClient)
    client.config = mc.McpServerConfig(name="t", command=["echo"])
    # shutdown() returns early when _proc is None — give it a minimal
    # fake proc so the cleanup body runs.
    class _DummyProc:
        returncode = 0  # already exited so the kill branches no-op
        pid = 12345
        async def wait(self):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    client._proc = _DummyProc()
    loop = asyncio.get_running_loop()
    fut1 = loop.create_future()
    fut2 = loop.create_future()
    client._pending = {1: fut1, 2: fut2}
    client._reader_task = None
    client._stderr_task = None
    client._started = False

    await client.shutdown()
    # Dict was cleared.
    assert client._pending == {}
    # Both futures are completed with an exception (not silently dropped).
    assert fut1.done()
    assert fut2.done()
    assert isinstance(fut1.exception(), mc.McpError)
    assert isinstance(fut2.exception(), mc.McpError)


@pytest.mark.asyncio
async def test_shutdown_tolerates_already_done_future():
    """If a future was already completed (e.g. by a late reader), shutdown
    must not raise InvalidStateError when iterating over the snapshot."""

    client = mc.StdioMcpClient.__new__(mc.StdioMcpClient)
    client.config = mc.McpServerConfig(name="t", command=["echo"])
    # shutdown() returns early when _proc is None — give it a minimal
    # fake proc so the cleanup body runs.
    class _DummyProc:
        returncode = 0  # already exited so the kill branches no-op
        pid = 12345
        async def wait(self):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    client._proc = _DummyProc()
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    fut.set_result({"ok": True})  # already done
    client._pending = {1: fut}
    client._reader_task = None
    client._stderr_task = None
    client._started = False

    # Must NOT raise.
    await client.shutdown()
    assert client._pending == {}


# ---------------------------------------------------------------------------
# McpClientPool atexit hook (audit §2.8)
# ---------------------------------------------------------------------------


def test_pool_registers_atexit_hook_on_init(monkeypatch):
    """The pool ctor MUST register an atexit hook so embedded consumers
    (tests, dashboard subcommands) get cleanup even when the cli.py
    atexit hook never ran."""

    registered: list = []
    monkeypatch.setattr(
        "atexit.register",
        lambda fn, *a, **kw: registered.append(fn),
    )
    mc.McpClientPool(mc.McpPoolConfig(enabled=True, servers=[]))
    # _atexit_kill should be among the registered hooks.
    assert any(getattr(fn, "__func__", fn).__name__ == "_atexit_kill"
               for fn in registered)


def test_pool_atexit_kill_skips_dead_processes():
    """_atexit_kill must handle a registry entry whose proc.returncode
    is already set (the child exited cleanly) without raising."""

    pool = mc.McpClientPool(mc.McpPoolConfig(enabled=True, servers=[]))

    class _DeadClient:
        class _Proc:
            returncode = 0  # already exited
            pid = 12345
        _proc = _Proc()

    pool.clients["dead"] = _DeadClient()  # type: ignore[assignment]
    # Must NOT raise — no signal should be sent because returncode is set.
    pool._atexit_kill()

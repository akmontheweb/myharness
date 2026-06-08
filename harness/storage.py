"""
Pluggable persistence layer with AsyncSqliteSaver as the canonical backend.

This module implements:
    - Re-exports the official LangGraph AsyncSqliteSaver from langgraph-checkpoint-sqlite
      so that `isinstance(saver, BaseCheckpointSaver)` passes LangGraph's internal validation.
    - 30-day TTL automatic garbage collection — fired on every harness run/status init.
    - Session ID management: accepts user-provided --session-id, falls back to UUIDv4.
    - `harness status` read-only inspector: queries the SQLite DB and prints a
      clean text snapshot of any checkpointed session without executing graph nodes.
    - `harness purge --all` command integration: wipes all checkpoint data.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Types
# ---------------------------------------------------------------------------

@dataclass
class CheckpointSummary:
    """
    Read-only snapshot of a checkpointed session, as returned by `harness status`.
    """
    thread_id: str
    session_id: str = ""
    current_node: str = ""
    exit_code: int = -1
    budget_remaining_usd: float = 0.0
    total_cost_usd: float = 0.0
    modified_files: list[str] = field(default_factory=list)
    loop_counters: dict[str, int] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    is_active: bool = False
    workspace_path: str = ""


# ---------------------------------------------------------------------------
# 2. Re-export the official LangGraph AsyncSqliteSaver
# ---------------------------------------------------------------------------

# The official langgraph-checkpoint-sqlite AsyncSqliteSaver is a fully-compliant
# BaseCheckpointSaver subclass. We re-export it so that graph.compile(checkpointer=...)
# passes ensure_valid_checkpointer() with zero friction.
#
# Our "HarnessAsyncSqliteSaver" thin wrapper adds:
#   - The `from_db_path` classmethod (SQLite path-based constructor)
#   - TTL-based automatic garbage collection on initialisation
#   - The same interface the CLI expects

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver as _OfficialAsyncSqliteSaver  # noqa: E402


class HarnessAsyncSqliteSaver(_OfficialAsyncSqliteSaver):
    """
    Thin wrapper around the official langgraph-checkpoint-sqlite AsyncSqliteSaver
    that adds TTL garbage collection and a path-based constructor.

    Usage:
        async with HarnessAsyncSqliteSaver.from_db_path("~/.harness/checkpoints.db") as saver:
            compiled = graph.compile(checkpointer=saver)
    """

    _db_path: str
    _ttl_days: int

    @classmethod
    async def from_db_path(
        cls,
        db_path: str = "~/.harness/checkpoints.db",
        ttl_days: int = 30,
    ) -> "HarnessAsyncSqliteSaver":
        """
        Create a HarnessAsyncSqliteSaver from a filesystem path.

        Manages connection lifecycle internally. Runs schema initialization
        and TTL garbage collection before returning.
        """
        import aiosqlite

        expanded_path = os.path.expanduser(db_path)
        db_dir = os.path.dirname(expanded_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        conn = await aiosqlite.connect(expanded_path)
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA busy_timeout=5000;")

        instance = cls(conn)
        await instance.setup()

        # Attach metadata for GC & inspection
        instance._db_path = expanded_path
        instance._ttl_days = ttl_days

        # Run 30-day TTL garbage collection
        await instance._run_gc()

        logger.info(
            "[storage] HarnessAsyncSqliteSaver initialised at %s (WAL mode, TTL=%d days).",
            expanded_path,
            ttl_days,
        )
        return instance

    async def _run_gc(self) -> int:
        """
        Delete checkpoint rows for threads whose latest checkpoint is older
        than ``self._ttl_days``.

        LangGraph stores an ISO 8601 ``ts`` field inside the msgpack-encoded
        checkpoint blob; we deserialize the latest row per thread, compare
        its timestamp to ``now - ttl_days``, and bulk-delete expired threads
        from both ``checkpoints`` and ``writes`` in a single transaction.

        Returns the total number of rows deleted across both tables.
        Setting ``ttl_days <= 0`` disables GC.
        """
        ttl_days = getattr(self, "_ttl_days", 0)
        if ttl_days is None or ttl_days <= 0:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)

        try:
            cursor = await self.conn.execute(
                """SELECT c.thread_id, c.checkpoint
                   FROM checkpoints c
                   INNER JOIN (
                       SELECT thread_id, MAX(checkpoint_id) AS max_cp_id
                       FROM checkpoints
                       GROUP BY thread_id
                   ) AS latest ON c.thread_id = latest.thread_id
                              AND c.checkpoint_id = latest.max_cp_id"""
            )
            rows = await cursor.fetchall()
        except Exception as e:  # noqa: BLE001
            logger.warning("[storage] GC scan failed (%s); skipping.", e)
            return 0

        expired_threads: list[str] = []
        for thread_id, blob in rows:
            cp = _deserialize_checkpoint_blob(blob)
            ts_value = cp.get("ts", "") if isinstance(cp, dict) else ""
            if not ts_value or not isinstance(ts_value, str):
                continue
            try:
                dt = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if dt < cutoff:
                expired_threads.append(thread_id)

        if not expired_threads:
            logger.debug("[storage] GC: no expired threads (TTL=%d days).", ttl_days)
            return 0

        placeholders = ",".join("?" * len(expired_threads))
        try:
            cursor = await self.conn.execute(
                f"DELETE FROM writes WHERE thread_id IN ({placeholders})",
                expired_threads,
            )
            deleted = cursor.rowcount or 0
            cursor = await self.conn.execute(
                f"DELETE FROM checkpoints WHERE thread_id IN ({placeholders})",
                expired_threads,
            )
            deleted += cursor.rowcount or 0
            await self.conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[storage] GC delete failed (%s); rolling back.", e)
            try:
                await self.conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            return 0

        logger.info(
            "[storage] TTL GC: removed %d rows across %d expired threads (TTL=%d days).",
            deleted,
            len(expired_threads),
            ttl_days,
        )
        return deleted

    @property
    def db_path(self) -> str:
        """Return the filesystem path of the backing SQLite database."""
        return getattr(self, "_db_path", "")

    @classmethod
    async def from_conn_string_with_gc(
        cls,
        conn_string: str,
        ttl_days: int = 30,
    ) -> "HarnessAsyncSqliteSaver":
        """
        Create from a SQLite connection string, then run GC.
        Use when you need the official constructor semantics + GC.
        """
        import aiosqlite

        expanded_path = os.path.expanduser(conn_string)
        db_dir = os.path.dirname(expanded_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        conn = await aiosqlite.connect(expanded_path)
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA busy_timeout=5000;")

        instance = cls(conn)
        await instance.setup()
        instance._db_path = expanded_path
        instance._ttl_days = ttl_days
        await instance._run_gc()
        return instance


# Backwards-compatible alias — code that imports AsyncSqliteSaver from storage
# will get the Harness wrapper which IS a valid BaseCheckpointSaver.
AsyncSqliteSaver = HarnessAsyncSqliteSaver

# ---------------------------------------------------------------------------
# 2b. Direct BaseCheckpointSaver alias (for isinstance checks)
# ---------------------------------------------------------------------------
from langgraph.checkpoint.base import BaseCheckpointSaver  # noqa: E402

BaseCheckpointer = BaseCheckpointSaver  # alias for backwards-compat


# ---------------------------------------------------------------------------
# 3. Session ID Management
# ---------------------------------------------------------------------------

def generate_session_id(user_provided: Optional[str] = None) -> str:
    """
    Generate a session ID. Returns the user-provided value if given,
    otherwise falls back to a random UUIDv4.

    Args:
        user_provided: Optional user-supplied session ID string.

    Returns:
        A session ID string.
    """
    if user_provided and user_provided.strip():
        session_id = user_provided.strip()
        logger.info("[storage] Using user-provided session ID: %s", session_id)
        return session_id

    session_id = str(uuid.uuid4())
    logger.info("[storage] Auto-generated session ID (UUIDv4): %s", session_id)
    return session_id


# ---------------------------------------------------------------------------
# 4. Status Inspector — Read-Only Session Snapshot
# ---------------------------------------------------------------------------

def _deserialize_checkpoint_blob(blob: Any) -> dict[str, Any]:
    """
    Deserialize a checkpoint column BLOB from the SQLite store.

    LangGraph's AsyncSqliteSaver stores checkpoints as msgpack-encoded
    binary blobs (via JsonPlusSerializer). Falls back to JSON for
    backwards compatibility with any legacy text-based rows.

    Returns an empty dict on failure.
    """
    if blob is None:
        return {}

    # msgpack binary path (LangGraph canonical format)
    if isinstance(blob, (bytes, bytearray)):
        try:
            import msgpack
        except ImportError:
            msgpack = None  # type: ignore[assignment]

        if msgpack is not None:
            try:
                return msgpack.unpackb(blob, raw=False)
            except Exception as e:  # noqa: BLE001
                # msgpack.exceptions.UnpackException or any decode failure —
                # don't crash on a single corrupt row; fall through to JSON.
                logger.debug("[storage] msgpack unpack failed (%s); trying JSON.", e)

        # Fallback: try decoding as UTF-8 JSON text (legacy format)
        try:
            return json.loads(blob.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # Plain text JSON path (legacy / backwards-compat)
    if isinstance(blob, str):
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            return {}

    return {}


def _format_checkpoint_ts(ts_value: Any) -> str:
    """
    Convert a LangGraph checkpoint 'ts' value into a human-readable local
    datetime string.

    The ``ts`` field is an ISO 8601 UTC string (e.g. "2026-06-08T14:30:00.000000Z").
    Returns a string like "2026-06-08 10:30:00" in the local timezone.
    Falls back to "(unknown)" if the value cannot be parsed.
    """
    if not ts_value or not isinstance(ts_value, str):
        return "(unknown)"

    try:
        from datetime import datetime, timezone as dt_timezone
        # Strip trailing 'Z' and parse ISO 8601 UTC
        cleaned = ts_value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        # If the parsed datetime is timezone-aware, convert to local
        if dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return "(unknown)"


async def inspect_session(
    db_path: str,
    thread_id: str,
) -> Optional[CheckpointSummary]:
    """
    Read a checkpoint from the SQLite database and return a human-readable
    summary without triggering any graph execution.

    Used by `harness status --session-id <uuid>`.

    Args:
        db_path: Path to the checkpoints SQLite database.
        thread_id: The thread/session ID to inspect.

    Returns:
        CheckpointSummary if found, None otherwise.
    """
    expanded_path = os.path.expanduser(db_path)
    if not os.path.isfile(expanded_path):
        logger.warning("[storage] Database not found at %s.", expanded_path)
        return None

    import aiosqlite

    async with aiosqlite.connect(expanded_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT thread_id, checkpoint, metadata
               FROM checkpoints
               WHERE thread_id = ?
               ORDER BY checkpoint_id DESC
               LIMIT 1""",
            (thread_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            logger.warning("[storage] No checkpoint found for thread '%s'.", thread_id)
            return None

        checkpoint = _deserialize_checkpoint_blob(row["checkpoint"])

        # Extract state fields from the checkpoint blob
        channel_values = checkpoint.get("channel_values", {})
        state = channel_values if isinstance(channel_values, dict) else {}

        exit_code = state.get("exit_code", -1)
        if isinstance(exit_code, dict):
            exit_code = exit_code.get("value", -1)

        budget_remaining = state.get("budget_remaining_usd", 0.0)
        if isinstance(budget_remaining, dict):
            budget_remaining = budget_remaining.get("value", 0.0)

        token_tracker = state.get("token_tracker", {})
        if isinstance(token_tracker, dict) and "total_cost_usd" in token_tracker:
            total_cost = token_tracker["total_cost_usd"]
        elif isinstance(token_tracker, dict) and "value" in token_tracker:
            total_cost = token_tracker.get("value", {}).get("total_cost_usd", 0.0)
        else:
            total_cost = 0.0

        modified_files = state.get("modified_files", [])
        if isinstance(modified_files, dict):
            modified_files = modified_files.get("value", [])

        loop_counters = state.get("loop_counter", {})
        if isinstance(loop_counters, dict) and not any(isinstance(v, dict) for v in loop_counters.values()):
            pass
        elif isinstance(loop_counters, dict):
            loop_counters = loop_counters.get("value", {})

        node_state = state.get("node_state", {})
        current_node = ""
        if isinstance(node_state, dict):
            current_node = node_state.get("current_node", "")
        elif isinstance(node_state, str):
            current_node = node_state

        # Extract timestamps from the LangGraph checkpoint "ts" field (ISO 8601)
        ts_value = checkpoint.get("ts", "")
        created_fmt = _format_checkpoint_ts(ts_value)
        # The latest checkpoint's ts is both created and updated time
        updated_fmt = created_fmt

        # Extract workspace_path from channel_values
        workspace_path = state.get("workspace_path", "")
        if isinstance(workspace_path, dict):
            workspace_path = workspace_path.get("value", "")
        workspace_path = str(workspace_path) if workspace_path else ""

        return CheckpointSummary(
            thread_id=row["thread_id"],
            session_id=thread_id,
            current_node=current_node,
            exit_code=int(exit_code) if exit_code is not None else -1,
            budget_remaining_usd=float(budget_remaining) if budget_remaining is not None else 0.0,
            total_cost_usd=float(total_cost) if total_cost is not None else 0.0,
            modified_files=list(modified_files) if modified_files else [],
            loop_counters=dict(loop_counters) if loop_counters else {},
            created_at=created_fmt,
            updated_at=updated_fmt,
            is_active=exit_code not in (0, -1) and exit_code != 0,
            workspace_path=workspace_path,
        )


async def list_all_sessions(db_path: str, limit: int = 50) -> list[CheckpointSummary]:
    """
    List summaries of all checkpointed sessions, ordered by most recently updated.

    Reads the latest checkpoint JSON blob for each thread to extract
    created/updated timestamps and workspace path.

    Args:
        db_path: Path to the checkpoints SQLite database.
        limit: Maximum number of sessions to return.

    Returns:
        List of CheckpointSummary objects.
    """
    expanded_path = os.path.expanduser(db_path)
    if not os.path.isfile(expanded_path):
        return []

    import aiosqlite

    summaries: list[CheckpointSummary] = []
    async with aiosqlite.connect(expanded_path) as db:
        db.row_factory = aiosqlite.Row
        # Subquery: for each thread_id, get the row with the largest checkpoint_id
        cursor = await db.execute(
            """SELECT c.thread_id, c.checkpoint_id, c.checkpoint
               FROM checkpoints c
               INNER JOIN (
                   SELECT thread_id, MAX(checkpoint_id) AS max_cp_id
                   FROM checkpoints
                   GROUP BY thread_id
               ) AS latest ON c.thread_id = latest.thread_id
                          AND c.checkpoint_id = latest.max_cp_id
               ORDER BY c.checkpoint_id DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            created_at = "(unknown)"
            updated_at = "(unknown)"
            workspace_path = ""

            try:
                cp = _deserialize_checkpoint_blob(row["checkpoint"])
                # Extract timestamp from the LangGraph "ts" field
                ts_value = cp.get("ts", "")
                created_at = _format_checkpoint_ts(ts_value)
                updated_at = created_at  # same for latest checkpoint

                # Extract workspace_path from channel_values
                channel_values = cp.get("channel_values", {})
                if isinstance(channel_values, dict):
                    wp = channel_values.get("workspace_path", "")
                    if isinstance(wp, dict):
                        wp = wp.get("value", "")
                    workspace_path = str(wp) if wp else ""
            except Exception:
                pass  # use fallback values

            summaries.append(CheckpointSummary(
                thread_id=row["thread_id"],
                session_id=row["thread_id"],
                created_at=created_at,
                updated_at=updated_at,
                workspace_path=workspace_path,
            ))
    return summaries


# ---------------------------------------------------------------------------
# 5. Checkpointer Factory
# ---------------------------------------------------------------------------

async def create_checkpointer(
    backend: str = "sqlite",
    db_path: str = "~/.harness/checkpoints.db",
    ttl_days: int = 30,
) -> BaseCheckpointSaver:
    """
    Factory: create the appropriate checkpointer backend.

    Args:
        backend: One of 'sqlite', 'memory', 'redis', 'postgres'.
                 Currently only 'sqlite' and 'memory' are implemented.
        db_path: Path to the SQLite database (for 'sqlite' backend).
        ttl_days: TTL for automatic garbage collection.

    Returns:
        A BaseCheckpointSaver instance (LangGraph-compliant).

    Raises:
        ValueError: If the backend is not recognized.
    """
    if backend == "sqlite":
        return await HarnessAsyncSqliteSaver.from_db_path(db_path=db_path, ttl_days=ttl_days)
    elif backend == "memory":
        try:
            from langgraph.checkpoint.memory import MemorySaver
            logger.info("[storage] Using in-memory MemorySaver (ephemeral).")
            return MemorySaver()
        except ImportError:
            logger.warning("[storage] MemorySaver not available. Falling back to AsyncSqliteSaver (:memory:).")
            return await HarnessAsyncSqliteSaver.from_db_path(db_path=":memory:", ttl_days=ttl_days)
    elif backend in ("redis", "postgres"):
        raise NotImplementedError(
            f"Backend '{backend}' is not yet implemented. "
            f"Use 'sqlite' for local development or 'memory' for ephemeral runs."
        )
    else:
        raise ValueError(
            f"Unknown backend: '{backend}'. Supported: 'sqlite', 'memory'."
        )


async def purge_checkpoints(db_path: str) -> int:
    """
    Delete ALL checkpoint data from the database. Returns row count deleted.

    Args:
        db_path: Path to the checkpoints SQLite database.

    Returns:
        Total number of rows deleted.
    """
    expanded_path = os.path.expanduser(db_path)
    if not os.path.isfile(expanded_path):
        logger.warning("[storage] No database at %s — nothing to purge.", expanded_path)
        return 0

    import aiosqlite

    async with aiosqlite.connect(expanded_path) as db:
        # Writes must be deleted first due to FK-like dependency
        cursor = await db.execute("DELETE FROM writes")
        deleted = cursor.rowcount
        cursor = await db.execute("DELETE FROM checkpoints")
        deleted += cursor.rowcount
        await db.commit()

    logger.info("[storage] Purged all data: %d rows deleted.", deleted)
    return deleted
"""
Pluggable persistence layer with AsyncSqliteSaver as the canonical backend.

This module implements:
    - BaseCheckpointer abstract interface for pluggable storage backends.
    - AsyncSqliteSaver: full disk-backed LangGraph checkpointer using aiosqlite.
      Survives process crashes, machine reboots, and enables cross-process resume.
    - Schema initialization for LangGraph checkpoint tables (checkpoints, writes, blobs).
    - 30-day TTL automatic garbage collection — fired on every harness run/status init.
    - Session ID management: accepts user-provided --session-id, falls back to UUIDv4.
    - `harness status` read-only inspector: queries the SQLite DB and prints a
      clean text snapshot of any checkpointed session without executing graph nodes.
    - `harness purge --all` command integration: wipes all checkpoint data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Sequence

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


# ---------------------------------------------------------------------------
# 2. BaseCheckpointer Abstract Interface
# ---------------------------------------------------------------------------

class BaseCheckpointer(ABC):
    """
    Pluggable interface for LangGraph checkpoint persistence.

    Implementations can target:
        - AsyncSqliteSaver (default local disk backend)
        - MemorySaver (ephemeral, for CI/one-shot jobs)
        - Redis/Postgres (future distributed worker backends)
    """

    @abstractmethod
    async def put(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        """Store a checkpoint."""
        ...

    @abstractmethod
    async def get(self, config: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Retrieve the latest checkpoint for a given thread."""
        ...

    @abstractmethod
    async def list(
        self,
        config: Optional[dict[str, Any]],
        *,
        limit: int = 10,
        before: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """List checkpoints, optionally filtered by config/thread."""
        ...

    @abstractmethod
    async def delete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints for a given thread."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources."""
        ...


# ---------------------------------------------------------------------------
# 3. AsyncSqliteSaver Implementation
# ---------------------------------------------------------------------------

# SQL schema for LangGraph checkpoint storage.
# Mirrors the schema used by langgraph-checkpoint-sqlite but is self-contained
# so this module has zero dependency on langgraph internals beyond the interface.

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint BLOB NOT NULL,
    metadata BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    value BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE TABLE IF NOT EXISTS blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT,
    blob BLOB,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_thread ON checkpoints(thread_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_updated ON checkpoints(updated_at);
CREATE INDEX IF NOT EXISTS idx_writes_thread ON writes(thread_id, checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_blobs_thread ON blobs(thread_id);
"""

# Trigger to auto-update `updated_at` on checkpoint modification
_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_checkpoints_updated
AFTER UPDATE ON checkpoints
FOR EACH ROW
BEGIN
    UPDATE checkpoints SET updated_at = datetime('now')
    WHERE thread_id = NEW.thread_id
      AND checkpoint_ns = NEW.checkpoint_ns
      AND checkpoint_id = NEW.checkpoint_id;
END;
"""


class AsyncSqliteSaver(BaseCheckpointer):
    """
    Disk-backed LangGraph checkpointer using aiosqlite.

    Features:
        - Full crash recovery: sessions survive process termination and machine reboots.
        - Automatic schema initialization on first connect.
        - 30-day TTL garbage collection on startup.
        - Thread-safe write operations via WAL journal mode.
    """

    def __init__(self, db_path: str = "~/.harness/checkpoints.db", ttl_days: int = 30):
        self.db_path = os.path.expanduser(db_path)
        self.ttl_days = ttl_days
        self._db: Optional[Any] = None  # aiosqlite.Connection
        self._initialized = False

    async def _connect(self) -> Any:
        """Lazily connect to the SQLite database with WAL mode."""
        if self._db is not None:
            return self._db

        import aiosqlite

        # Ensure the directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.execute("PRAGMA busy_timeout=5000;")

        logger.info("[storage] Connected to SQLite at %s (WAL mode).", self.db_path)
        return self._db

    async def initialize(self) -> None:
        """Run schema initialization and garbage collection."""
        if self._initialized:
            return

        db = await self._connect()

        # Create tables
        await db.executescript(_CREATE_TABLES_SQL)
        await db.executescript(_UPDATE_TRIGGER_SQL)
        await db.commit()

        # Run 30-day TTL garbage collection
        await self._run_gc(db)

        self._initialized = True
        logger.info("[storage] Schema initialized and GC complete.")

    async def _run_gc(self, db: Any) -> int:
        """
        Delete checkpoint blobs, writes, and checkpoints where updated_at
        is older than `self.ttl_days` days. Returns count of deleted rows.
        """
        cutoff = f"-{self.ttl_days} days"
        deleted = 0

        # Delete old blobs
        cursor = await db.execute(
            "DELETE FROM blobs WHERE created_at < datetime('now', ?)",
            (cutoff,),
        )
        deleted += cursor.rowcount

        # Delete old writes
        cursor = await db.execute(
            "DELETE FROM writes WHERE created_at < datetime('now', ?)",
            (cutoff,),
        )
        deleted += cursor.rowcount

        # Delete old checkpoints
        cursor = await db.execute(
            "DELETE FROM checkpoints WHERE updated_at < datetime('now', ?)",
            (cutoff,),
        )
        deleted += cursor.rowcount

        await db.commit()

        if deleted > 0:
            logger.info("[storage] GC cleanup: %d row(s) deleted (TTL=%d days).", deleted, self.ttl_days)
        else:
            logger.debug("[storage] GC cleanup: nothing to purge.")
        return deleted

    async def put(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        db = await self._connect()
        await self.initialize()

        thread_id = config.get("configurable", {}).get("thread_id", "default")
        checkpoint_ns = config.get("configurable", {}).get("checkpoint_ns", "")
        checkpoint_id = checkpoint.get("id", str(uuid.uuid4()))
        parent_checkpoint_id = checkpoint.get("parent_checkpoint_id")
        checkpoint_type = checkpoint.get("type", "state")

        checkpoint_blob = json.dumps(checkpoint).encode("utf-8")
        metadata_blob = json.dumps(metadata).encode("utf-8")

        await db.execute(
            """INSERT OR REPLACE INTO checkpoints
               (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint_type, checkpoint_blob, metadata_blob),
        )
        await db.commit()

        logger.debug("[storage] Checkpoint saved: thread=%s id=%s", thread_id, checkpoint_id)
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint_id}}

    async def get(self, config: dict[str, Any]) -> Optional[dict[str, Any]]:
        db = await self._connect()
        await self.initialize()

        thread_id = config.get("configurable", {}).get("thread_id", "default")
        checkpoint_ns = config.get("configurable", {}).get("checkpoint_ns", "")

        cursor = await db.execute(
            """SELECT checkpoint, metadata
               FROM checkpoints
               WHERE thread_id = ? AND checkpoint_ns = ?
               ORDER BY updated_at DESC
               LIMIT 1""",
            (thread_id, checkpoint_ns),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        checkpoint = json.loads(row[0])
        metadata = json.loads(row[1])
        checkpoint["metadata"] = metadata
        return checkpoint

    async def list(
        self,
        config: Optional[dict[str, Any]] = None,
        *,
        limit: int = 10,
        before: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        db = await self._connect()
        await self.initialize()

        thread_id = None
        if config:
            thread_id = config.get("configurable", {}).get("thread_id")

        if thread_id:
            cursor = await db.execute(
                """SELECT checkpoint, metadata
                   FROM checkpoints
                   WHERE thread_id = ?
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                (thread_id, limit),
            )
        else:
            cursor = await db.execute(
                """SELECT checkpoint, metadata
                   FROM checkpoints
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                (limit,),
            )

        rows = await cursor.fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            checkpoint = json.loads(row[0])
            metadata = json.loads(row[1])
            checkpoint["metadata"] = metadata
            results.append(checkpoint)

        return results

    async def delete_thread(self, thread_id: str) -> None:
        db = await self._connect()
        await db.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
        await db.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        await db.execute("DELETE FROM blobs WHERE thread_id = ?", (thread_id,))
        await db.commit()
        logger.info("[storage] Deleted all data for thread '%s'.", thread_id)

    async def purge_all(self) -> int:
        """Delete all checkpoint data from the database. Returns row count deleted."""
        db = await self._connect()
        cursor = await db.execute("DELETE FROM checkpoints")
        deleted = cursor.rowcount
        cursor = await db.execute("DELETE FROM writes")
        deleted += cursor.rowcount
        cursor = await db.execute("DELETE FROM blobs")
        deleted += cursor.rowcount
        await db.commit()
        logger.info("[storage] Purged all data: %d rows deleted.", deleted)
        return deleted

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            self._initialized = False
            logger.info("[storage] Database connection closed.")


# ---------------------------------------------------------------------------
# 4. Session ID Management
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
# 5. Status Inspector — Read-Only Session Snapshot
# ---------------------------------------------------------------------------

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
            """SELECT thread_id, checkpoint, metadata, created_at, updated_at
               FROM checkpoints
               WHERE thread_id = ?
               ORDER BY updated_at DESC
               LIMIT 1""",
            (thread_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            logger.warning("[storage] No checkpoint found for thread '%s'.", thread_id)
            return None

        checkpoint = json.loads(row["checkpoint"])
        metadata = json.loads(row["metadata"])

        # Extract state fields from the checkpoint blob
        # The checkpoint contains channel_values which hold the AgentState fields
        channel_values = checkpoint.get("channel_values", {})
        state = channel_values if isinstance(channel_values, dict) else {}

        # Navigate LangGraph's internal structure to extract our state fields
        # AgentState fields are typically nested under channel_values or the root
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

        return CheckpointSummary(
            thread_id=row["thread_id"],
            session_id=thread_id,
            current_node=current_node,
            exit_code=int(exit_code) if exit_code is not None else -1,
            budget_remaining_usd=float(budget_remaining) if budget_remaining is not None else 0.0,
            total_cost_usd=float(total_cost) if total_cost is not None else 0.0,
            modified_files=list(modified_files) if modified_files else [],
            loop_counters=dict(loop_counters) if loop_counters else {},
            created_at=row["created_at"] or "unknown",
            updated_at=row["updated_at"] or "unknown",
            is_active=exit_code not in (0, -1) and exit_code != 0,
        )


async def list_all_sessions(db_path: str, limit: int = 50) -> list[CheckpointSummary]:
    """
    List summaries of all checkpointed sessions, ordered by most recently updated.

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
        cursor = await db.execute(
            """SELECT thread_id, created_at, updated_at
               FROM checkpoints
               GROUP BY thread_id
               ORDER BY updated_at DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            summaries.append(CheckpointSummary(
                thread_id=row["thread_id"],
                session_id=row["thread_id"],
                created_at=row["created_at"] or "unknown",
                updated_at=row["updated_at"] or "unknown",
            ))
    return summaries


# ---------------------------------------------------------------------------
# 6. Checkpointer Factory
# ---------------------------------------------------------------------------

def create_checkpointer(
    backend: str = "sqlite",
    db_path: str = "~/.harness/checkpoints.db",
    ttl_days: int = 30,
) -> BaseCheckpointer:
    """
    Factory: create the appropriate checkpointer backend.

    Args:
        backend: One of 'sqlite', 'memory', 'redis', 'postgres'.
                 Currently only 'sqlite' and 'memory' are implemented.
        db_path: Path to the SQLite database (for 'sqlite' backend).
        ttl_days: TTL for automatic garbage collection.

    Returns:
        A BaseCheckpointer instance.

    Raises:
        ValueError: If the backend is not recognized.
    """
    if backend == "sqlite":
        return AsyncSqliteSaver(db_path=db_path, ttl_days=ttl_days)
    elif backend == "memory":
        # Use langgraph's built-in MemorySaver if available, otherwise stub
        try:
            from langgraph.checkpoint.memory import MemorySaver
            logger.info("[storage] Using in-memory MemorySaver (ephemeral).")
            return MemorySaver()  # type: ignore[return-value]
        except ImportError:
            logger.warning("[storage] MemorySaver not available. Falling back to AsyncSqliteSaver.")
            return AsyncSqliteSaver(db_path=":memory:", ttl_days=ttl_days)
    elif backend in ("redis", "postgres"):
        raise NotImplementedError(
            f"Backend '{backend}' is not yet implemented. "
            f"Use 'sqlite' for local development or 'memory' for ephemeral runs."
        )
    else:
        raise ValueError(
            f"Unknown backend: '{backend}'. Supported: 'sqlite', 'memory'."
        )
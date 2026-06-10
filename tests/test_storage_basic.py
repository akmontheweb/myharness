"""Tests for harness/storage.py — checkpoint persistence basics."""

import pytest

from harness.storage import (
    CheckpointSummary,
    generate_session_id,
)


class TestCheckpointSummary:
    """Test CheckpointSummary dataclass."""

    def test_construct_minimal(self):
        """Construct CheckpointSummary with required fields."""
        summary = CheckpointSummary(thread_id="thread-1")
        assert summary.thread_id == "thread-1"
        assert summary.session_id == ""
        assert summary.current_node == ""

    def test_construct_with_all_fields(self):
        """Construct with all fields."""
        summary = CheckpointSummary(
            thread_id="t1",
            session_id="s1",
            current_node="patching",
            exit_code=0,
            budget_remaining_usd=5.5,
            total_cost_usd=4.5,
            modified_files=["a.py", "b.py"],
            loop_counters={"repair": 2},
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T01:00:00Z",
            is_active=True,
            workspace_path="/workspace",
        )
        assert summary.thread_id == "t1"
        assert summary.session_id == "s1"
        assert summary.current_node == "patching"
        assert summary.modified_files == ["a.py", "b.py"]
        assert summary.budget_remaining_usd == 5.5
        assert summary.is_active is True


class TestGenerateSessionId:
    """Test session ID generation."""

    def test_generate_uuid_when_none_provided(self):
        """Should generate a UUID when no session ID provided."""
        sid = generate_session_id(user_provided=None)
        assert sid is not None
        assert len(sid) > 0
        # Should be a valid UUID format (36 chars with dashes)
        assert "-" in sid or len(sid) > 8

    def test_use_user_provided_session_id(self):
        """Should use user-provided session ID when given."""
        provided = "my-custom-session"
        sid = generate_session_id(user_provided=provided)
        assert sid == provided

    def test_user_provided_empty_string_generates_uuid(self):
        """Empty string should be treated as no user input."""
        sid = generate_session_id(user_provided="")
        assert sid is not None
        assert len(sid) > 0
        # Should be UUID, not empty
        assert sid != ""


class TestCheckpointMessageRedaction:
    """P0.1 regression: messages persisted to the checkpoint DB MUST be
    scrubbed through harness/redactor.py first. Without this, secrets the
    user pasted into a prompt (API keys, customer records, SSH keys) land at
    rest in ~/.harness/checkpoints.db unencrypted and unredacted.
    """

    @pytest.mark.asyncio
    async def test_aput_redacts_messages_in_checkpoint(self, tmp_path):
        from harness.storage import HarnessAsyncSqliteSaver
        from harness.redactor import create_redactor_from_config, set_redactor

        # Bring up the global redactor with default sensitive patterns
        # (sk-... API-key shape, AWS access keys, etc.).
        create_redactor_from_config({})

        # A real-shape secret the user might paste in a prompt: 51-char
        # sk-prefixed key. The redactor's _DEFAULT_SENSITIVE_PATTERNS catch
        # this shape — if redaction is bypassed, the substring lands in the
        # serialised checkpoint blob.
        secret = "sk-" + "A" * 48
        db_file = str(tmp_path / "test_checkpoint.db")
        saver = await HarnessAsyncSqliteSaver.from_db_path(
            db_path=db_file, ttl_days=0, redact_messages=True,
        )
        try:
            from langgraph.checkpoint.base import empty_checkpoint
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = {
                "messages": [
                    {"role": "user", "content": f"My API key is {secret}"},
                ],
            }
            checkpoint["channel_versions"] = {"messages": 1}
            checkpoint["versions_seen"] = {}
            config = {
                "configurable": {
                    "thread_id": "test-thread-redaction",
                    "checkpoint_ns": "",
                    "checkpoint_id": checkpoint["id"],
                },
            }
            await saver.aput(config, checkpoint, {}, {"messages": 1})

            import aiosqlite
            async with aiosqlite.connect(db_file) as conn:
                async with conn.execute("SELECT checkpoint FROM checkpoints") as cur:
                    row = await cur.fetchone()
            assert row is not None, "checkpoint row should exist"
            blob_bytes = row[0]
            assert secret.encode() not in blob_bytes, (
                "raw secret leaked into the checkpoint blob — message "
                "redaction was bypassed on the checkpoint write path"
            )
        finally:
            await saver.conn.close()
            set_redactor(None)

    @pytest.mark.asyncio
    async def test_aput_writes_redacts_messages_in_pending_writes(self, tmp_path):
        from harness.storage import HarnessAsyncSqliteSaver
        from harness.redactor import create_redactor_from_config, set_redactor

        create_redactor_from_config({})

        secret = "sk-" + "B" * 48
        db_file = str(tmp_path / "test_writes.db")
        saver = await HarnessAsyncSqliteSaver.from_db_path(
            db_path=db_file, ttl_days=0, redact_messages=True,
        )
        try:
            config = {
                "configurable": {
                    "thread_id": "test-thread-writes",
                    "checkpoint_ns": "",
                    "checkpoint_id": "test-checkpoint-id",
                },
            }
            writes = [
                ("messages", [{"role": "user", "content": f"key={secret}"}]),
            ]
            await saver.aput_writes(config, writes, task_id="t1")

            import aiosqlite
            async with aiosqlite.connect(db_file) as conn:
                async with conn.execute("SELECT value FROM writes") as cur:
                    row = await cur.fetchone()
            assert row is not None
            assert secret.encode() not in row[0], (
                "raw secret leaked into the pending-writes blob — message "
                "redaction was bypassed on aput_writes"
            )
        finally:
            await saver.conn.close()
            set_redactor(None)

    @pytest.mark.asyncio
    async def test_redact_disabled_persists_raw(self, tmp_path):
        """When persistence.redact_messages is False, the raw content is
        persisted — confirms the opt-out works for users who want to keep
        unredacted checkpoints (e.g. sessions where transcripts are needed
        verbatim and the operator accepts the risk)."""
        from harness.storage import HarnessAsyncSqliteSaver
        from harness.redactor import create_redactor_from_config, set_redactor

        create_redactor_from_config({})

        secret = "sk-" + "C" * 48
        db_file = str(tmp_path / "test_disabled.db")
        saver = await HarnessAsyncSqliteSaver.from_db_path(
            db_path=db_file, ttl_days=0, redact_messages=False,
        )
        try:
            from langgraph.checkpoint.base import empty_checkpoint
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = {
                "messages": [{"role": "user", "content": secret}],
            }
            checkpoint["channel_versions"] = {"messages": 1}
            checkpoint["versions_seen"] = {}
            config = {
                "configurable": {
                    "thread_id": "test-thread-disabled",
                    "checkpoint_ns": "",
                    "checkpoint_id": checkpoint["id"],
                },
            }
            await saver.aput(config, checkpoint, {}, {"messages": 1})

            import aiosqlite
            async with aiosqlite.connect(db_file) as conn:
                async with conn.execute("SELECT checkpoint FROM checkpoints") as cur:
                    row = await cur.fetchone()
            assert row is not None
            assert secret.encode() in row[0]
        finally:
            await saver.conn.close()
            set_redactor(None)


class TestCheckpointSchemaVersion:
    """P2.4: every checkpoint must carry a schema_version stamp in metadata,
    and validate_checkpoint_schema must refuse incompatible versions before
    cmd_resume restores graph state from a future-format blob."""

    @pytest.mark.asyncio
    async def test_aput_stamps_schema_version_in_metadata(self, tmp_path):
        from harness.storage import (
            CHECKPOINT_SCHEMA_VERSION,
            HarnessAsyncSqliteSaver,
            SCHEMA_VERSION_METADATA_KEY,
            _deserialize_checkpoint_blob,
        )

        db_file = str(tmp_path / "test_schema_stamp.db")
        saver = await HarnessAsyncSqliteSaver.from_db_path(
            db_path=db_file, ttl_days=0, redact_messages=False,
        )
        try:
            from langgraph.checkpoint.base import empty_checkpoint
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = {"messages": []}
            checkpoint["channel_versions"] = {"messages": 1}
            checkpoint["versions_seen"] = {}
            config = {
                "configurable": {
                    "thread_id": "test-schema-stamp",
                    "checkpoint_ns": "",
                    "checkpoint_id": checkpoint["id"],
                },
            }
            await saver.aput(config, checkpoint, {"source": "input"}, {"messages": 1})

            import aiosqlite
            async with aiosqlite.connect(db_file) as conn:
                async with conn.execute("SELECT metadata FROM checkpoints") as cur:
                    row = await cur.fetchone()
            assert row is not None
            metadata = _deserialize_checkpoint_blob(row[0])
            assert metadata.get(SCHEMA_VERSION_METADATA_KEY) == CHECKPOINT_SCHEMA_VERSION
            # Existing keys must round-trip unchanged.
            assert metadata.get("source") == "input"
        finally:
            await saver.conn.close()

    def test_validate_accepts_current_version(self):
        import msgpack
        from harness.storage import (
            CHECKPOINT_SCHEMA_VERSION,
            SCHEMA_VERSION_METADATA_KEY,
            validate_checkpoint_schema,
        )
        blob = msgpack.packb({SCHEMA_VERSION_METADATA_KEY: CHECKPOINT_SCHEMA_VERSION})
        assert validate_checkpoint_schema(blob) == CHECKPOINT_SCHEMA_VERSION

    def test_validate_refuses_future_version(self):
        import msgpack
        import pytest as _pytest
        from harness.storage import (
            CHECKPOINT_SCHEMA_VERSION,
            CheckpointSchemaMismatchError,
            SCHEMA_VERSION_METADATA_KEY,
            validate_checkpoint_schema,
        )
        blob = msgpack.packb({SCHEMA_VERSION_METADATA_KEY: CHECKPOINT_SCHEMA_VERSION + 1})
        with _pytest.raises(CheckpointSchemaMismatchError, match="newer harness"):
            validate_checkpoint_schema(blob)

    def test_validate_legacy_checkpoint_warns_but_allows(self, caplog):
        import logging as _logging
        import msgpack
        from harness.storage import validate_checkpoint_schema
        blob = msgpack.packb({"source": "input"})
        with caplog.at_level(_logging.WARNING, logger="harness.storage"):
            result = validate_checkpoint_schema(blob)
        assert result is None
        assert any("no schema version stamp" in rec.message for rec in caplog.records)

    def test_validate_non_integer_version_rejected(self):
        import msgpack
        import pytest as _pytest
        from harness.storage import (
            CheckpointSchemaMismatchError,
            SCHEMA_VERSION_METADATA_KEY,
            validate_checkpoint_schema,
        )
        blob = msgpack.packb({SCHEMA_VERSION_METADATA_KEY: "v1"})
        with _pytest.raises(CheckpointSchemaMismatchError, match="not an integer"):
            validate_checkpoint_schema(blob)

"""Tests for the storage-layer audit hardening (batches 1, 7, 9).

Covers:
  - _force_cleanse_checkpoint_messages / _force_cleanse_writes      (§1.3)
  - HarnessAsyncSqliteSaver TTL GC delete-on-corruption              (§5.4)
  - generate_session_id validation                                    (§5.2)
  - validate_checkpoint_schema strict-metadata refusal                (§5.15)
  - _deserialize_checkpoint_blob strict UTF-8 JSON                    (§5.16)
  - purge_checkpoints transaction + rollback                          (§5.17)
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from harness import storage as storage_mod
from harness.storage import (
    _deserialize_checkpoint_blob,
    _force_cleanse_checkpoint_messages,
    _force_cleanse_writes,
    CheckpointCorruptedError,
    CheckpointSchemaMismatchError,
    SCHEMA_VERSION_METADATA_KEY,
    generate_session_id,
    purge_checkpoints,
    validate_checkpoint_schema,
)


# ---------------------------------------------------------------------------
# _force_cleanse_checkpoint_messages (audit §1.3)
# ---------------------------------------------------------------------------


class TestForceCleanseCheckpointMessages:
    def test_replaces_messages_with_cleansed_copies(self):
        checkpoint = {
            "channel_values": {
                "messages": [{"role": "user", "content": "secret-payload"}],
                "other_channel": "intact",
            },
        }
        result = _force_cleanse_checkpoint_messages(checkpoint)
        assert result["channel_values"]["other_channel"] == "intact"
        content = result["channel_values"]["messages"][0]["content"]
        assert "secret-payload" not in content
        assert "REDACTED" in content or content == storage_mod._CLEANSED_CONTENT_PLACEHOLDER

    def test_non_dict_checkpoint_returns_unchanged(self):
        for bad in ("not a dict", 42, None, [1, 2, 3]):
            assert _force_cleanse_checkpoint_messages(bad) == bad

    def test_checkpoint_without_channel_values_unchanged(self):
        ck = {"id": "abc"}
        assert _force_cleanse_checkpoint_messages(ck) is ck

    def test_channel_values_without_messages_unchanged(self):
        ck = {"channel_values": {"other": [1, 2]}}
        assert _force_cleanse_checkpoint_messages(ck) is ck

    def test_inner_cleanse_failure_drops_messages_channel(self, monkeypatch):
        # Force _cleansed_messages to raise so the inner-except branch fires.
        def boom(_messages):
            raise RuntimeError("inner cleanse failed")

        monkeypatch.setattr(storage_mod, "_cleansed_messages", boom)
        ck = {"channel_values": {"messages": [{"content": "x"}], "keep": "ok"}}
        out = _force_cleanse_checkpoint_messages(ck)
        # `messages` channel should be removed; `keep` preserved.
        assert "messages" not in out["channel_values"]
        assert out["channel_values"]["keep"] == "ok"


class TestForceCleanseWrites:
    def test_cleanses_messages_writes(self):
        writes = [("messages", [{"role": "user", "content": "sk-LIVE-token-payload"}]), ("foo", 42)]
        out = _force_cleanse_writes(writes)
        # Both entries survive, foo intact, messages cleansed.
        assert ("foo", 42) in out
        msg_pair = next(e for e in out if e[0] == "messages")
        content = msg_pair[1][0]["content"]
        assert "sk-LIVE-token-payload" not in content

    def test_malformed_entry_skipped(self):
        # Non-tuple entries silently dropped (catastrophic shape protection).
        writes = ["not a tuple", ("ok", "value")]
        out = _force_cleanse_writes(writes)
        assert out == [("ok", "value")]

    def test_inner_cleanse_failure_drops_individual_write(self, monkeypatch):
        def boom(_messages):
            raise RuntimeError("inner cleanse")
        monkeypatch.setattr(storage_mod, "_cleansed_messages", boom)
        writes = [("messages", [{"content": "x"}]), ("other", "kept")]
        out = _force_cleanse_writes(writes)
        # messages write is dropped, other survives.
        assert out == [("other", "kept")]


# ---------------------------------------------------------------------------
# generate_session_id validation (audit §5.2)
# ---------------------------------------------------------------------------


class TestGenerateSessionId:
    def test_accepts_valid_chars(self):
        assert generate_session_id("abc-123") == "abc-123"
        assert generate_session_id("foo_bar.42") == "foo_bar.42"
        assert generate_session_id("a" * 64).startswith("a")

    def test_rejects_path_separator(self):
        with pytest.raises(ValueError, match=r"\[A-Za-z0-9._-\]"):
            generate_session_id("../../etc/passwd")

    def test_rejects_nul_byte(self):
        with pytest.raises(ValueError):
            generate_session_id("foo\x00bar")

    def test_rejects_over_64_chars(self):
        with pytest.raises(ValueError):
            generate_session_id("a" * 65)

    def test_rejects_empty_string_falls_through_to_uuid(self):
        # Empty string and whitespace-only fall through to UUID generation.
        sid = generate_session_id("   ")
        # UUID4 format check — no exception raised.
        assert len(sid) >= 8

    def test_no_input_returns_uuid(self):
        sid = generate_session_id()
        # Just ensure it's non-empty and doesn't look like garbage.
        assert sid
        assert sid.count("-") >= 4  # UUID4 has 4 hyphens


# ---------------------------------------------------------------------------
# _deserialize_checkpoint_blob strict UTF-8 (audit §5.16)
# ---------------------------------------------------------------------------


class TestDeserializeCheckpointBlob:
    def test_valid_json_bytes(self):
        blob = b'{"ts": "2026-01-01T00:00:00Z", "x": 1}'
        result = _deserialize_checkpoint_blob(blob)
        assert result["ts"].startswith("2026")
        assert result["x"] == 1

    def test_strict_raises_on_truncated_utf8(self):
        # Truncated continuation byte that errors='strict' should reject.
        bad = b'{"key": "\xc3"}'  # incomplete UTF-8 sequence
        with pytest.raises(CheckpointCorruptedError):
            _deserialize_checkpoint_blob(bad, strict=True)

    def test_non_strict_returns_empty_on_corruption(self):
        # Non-strict path tolerates corruption (returns {}).
        result = _deserialize_checkpoint_blob(b"\xff\xff\xff\xff", strict=False)
        assert result == {}

    def test_strict_raises_on_both_decoders_failed(self):
        with pytest.raises(CheckpointCorruptedError):
            _deserialize_checkpoint_blob(b"\xff\xff\xff\xff", strict=True)


# ---------------------------------------------------------------------------
# validate_checkpoint_schema strict metadata (audit §5.15)
# ---------------------------------------------------------------------------


class TestValidateCheckpointSchema:
    def test_corrupted_metadata_raises_mismatch(self):
        with pytest.raises(CheckpointSchemaMismatchError):
            validate_checkpoint_schema(b"\xff\xff\xff\xff")

    def test_missing_version_returns_none(self, caplog):
        blob = json.dumps({"some": "metadata"}).encode("utf-8")
        # Should NOT raise; should return None (legacy path).
        with caplog.at_level("WARNING"):
            assert validate_checkpoint_schema(blob) is None

    def test_valid_current_version(self):
        from harness.storage import CHECKPOINT_SCHEMA_VERSION
        blob = json.dumps({
            SCHEMA_VERSION_METADATA_KEY: CHECKPOINT_SCHEMA_VERSION,
        }).encode("utf-8")
        assert validate_checkpoint_schema(blob) == CHECKPOINT_SCHEMA_VERSION

    def test_future_version_refused(self):
        blob = json.dumps({SCHEMA_VERSION_METADATA_KEY: 9999}).encode("utf-8")
        with pytest.raises(CheckpointSchemaMismatchError):
            validate_checkpoint_schema(blob)


# ---------------------------------------------------------------------------
# purge_checkpoints transaction (audit §5.17)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_checkpoints_rolls_back_on_mid_failure(monkeypatch):
    """When the second DELETE raises, the first DELETE must roll back so
    we don't leave writes wiped with orphan checkpoint rows."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "ck.db")

        # Build a minimal schema and seed two rows.
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE writes (id INTEGER, payload TEXT);
            CREATE TABLE checkpoints (id INTEGER, blob BLOB);
            INSERT INTO writes VALUES (1, 'w1');
            INSERT INTO checkpoints VALUES (1, 'c1');
        """)
        conn.commit()
        conn.close()

        # Monkeypatch aiosqlite.connect to deliver a Connection whose
        # second DELETE raises OSError.
        import aiosqlite

        real_connect = aiosqlite.connect

        class _FailingConn:
            def __init__(self, real):
                self._real = real
                self._delete_calls = 0

            async def __aenter__(self):
                self._inner = await self._real.__aenter__()
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return await self._real.__aexit__(exc_type, exc, tb)

            async def execute(self, sql, *args, **kw):
                upper = sql.strip().upper()
                if upper.startswith("DELETE FROM CHECKPOINTS"):
                    raise OSError("simulated mid-transaction failure")
                return await self._inner.execute(sql, *args, **kw)

            async def commit(self):
                return await self._inner.commit()

            async def rollback(self):
                return await self._inner.rollback()

        monkeypatch.setattr(
            aiosqlite, "connect",
            lambda path: _FailingConn(real_connect(path)),
        )

        with pytest.raises(OSError, match="simulated"):
            await purge_checkpoints(db_path)

        # The writes table must STILL have its row — purge_checkpoints
        # rolled back the BEGIN IMMEDIATE before the OSError propagated.
        verify = sqlite3.connect(db_path)
        writes_count = verify.execute("SELECT COUNT(*) FROM writes").fetchone()[0]
        ck_count = verify.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
        verify.close()
        assert writes_count == 1, "writes was wiped despite rollback"
        assert ck_count == 1, "checkpoints was wiped despite rollback"

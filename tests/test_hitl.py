"""Tests for harness/hitl.py — pluggable HITL transport."""
import json
import os
import tempfile

import pytest

from harness.hitl import (
    FileChannel,
    StdinChannel,
    get_channel,
    reset_channel,
    set_channel,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Reset channel singleton and env vars before each test."""
    reset_channel()
    monkeypatch.delenv("HARNESS_HITL_FILE", raising=False)
    monkeypatch.delenv("HARNESS_AUTO_APPROVE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    yield
    reset_channel()


# ---------------------------------------------------------------------------
# StdinChannel — auto-approve path (no actual stdin needed)
# ---------------------------------------------------------------------------

class TestStdinChannelAutoApprove:

    def test_prompt_returns_default_in_ci(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        ch = StdinChannel()
        assert ch.prompt("Choose action", ["a", "b", "c"], default="b") == "b"

    def test_prompt_returns_first_option_when_no_default(self, monkeypatch):
        monkeypatch.setenv("HARNESS_AUTO_APPROVE", "true")
        ch = StdinChannel()
        assert ch.prompt("Choose action", ["x", "y"]) == "x"

    def test_confirm_returns_default_in_ci(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        ch = StdinChannel()
        assert ch.confirm("Proceed?", default=True) is True
        assert ch.confirm("Proceed?", default=False) is False

    def test_notes_returns_empty_in_ci(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        ch = StdinChannel()
        assert ch.notes("Enter hint") == ""

    def test_wait_for_manual_edit_returns_immediately_in_ci(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        ch = StdinChannel()
        ch.wait_for_manual_edit("/any/path")  # must not block

    def test_is_interactive_false_in_ci(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        ch = StdinChannel()
        assert ch.is_interactive() is False


# ---------------------------------------------------------------------------
# FileChannel
# ---------------------------------------------------------------------------

class TestFileChannel:

    def _write_answers(self, tmp_dir: str, entries: list[dict]) -> str:
        path = os.path.join(tmp_dir, "answers.json")
        with open(path, "w") as f:
            json.dump(entries, f)
        return path

    def test_prompt_matches_by_substring(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write_answers(td, [
                {"prompt": "REQUIREMENTS", "answer": "a"},
                {"prompt": "deploy preview", "answer": "y"},
            ])
            ch = FileChannel(path)
            assert ch.prompt("[HITL:REQUIREMENTS] Select action", ["a", "e"]) == "a"

    def test_confirm_parses_yes_variants(self):
        with tempfile.TemporaryDirectory() as td:
            for answer in ("y", "yes", "YES", "true", "1"):
                path = self._write_answers(td, [{"prompt": "Proceed", "answer": answer}])
                ch = FileChannel(path)
                assert ch.confirm("Proceed?") is True

    def test_confirm_parses_no_variants(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write_answers(td, [{"prompt": "Proceed", "answer": "n"}])
            ch = FileChannel(path)
            assert ch.confirm("Proceed with changes?") is False

    def test_notes_returns_recorded_text(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write_answers(td, [
                {"prompt": "Enter hint", "answer": "add retry logic"}
            ])
            ch = FileChannel(path)
            assert ch.notes("[HITL] Enter hint/instruction") == "add retry logic"

    def test_wait_for_manual_edit_returns_immediately(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write_answers(td, [])
            ch = FileChannel(path)
            ch.wait_for_manual_edit("/some/file.md")  # must not block

    def test_is_interactive_false(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write_answers(td, [])
            ch = FileChannel(path)
            assert ch.is_interactive() is False

    def test_unmatched_prompt_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write_answers(td, [
                {"prompt": "REQUIREMENTS", "answer": "a"}
            ])
            ch = FileChannel(path)
            with pytest.raises(RuntimeError, match="No pre-recorded answer"):
                ch.prompt("[HITL:DEPLOYMENT] Select action", ["a", "b"])

    def test_all_entries_consumed_independently(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write_answers(td, [
                {"prompt": "gate1", "answer": "a"},
                {"prompt": "gate2", "answer": "b"},
            ])
            ch = FileChannel(path)
            assert ch.prompt("gate1 prompt", ["a", "b"]) == "a"
            assert ch.prompt("gate2 prompt", ["a", "b"]) == "b"


# ---------------------------------------------------------------------------
# get_channel factory
# ---------------------------------------------------------------------------

class TestGetChannel:

    def test_returns_file_channel_when_env_set(self, monkeypatch):
        with tempfile.TemporaryDirectory() as td:
            answers = os.path.join(td, "a.json")
            with open(answers, "w") as f:
                json.dump([], f)
            monkeypatch.setenv("HARNESS_HITL_FILE", answers)
            ch = get_channel()
            assert isinstance(ch, FileChannel)

    def test_returns_stdin_channel_by_default(self):
        ch = get_channel()
        assert isinstance(ch, StdinChannel)

    def test_set_channel_overrides_factory(self, monkeypatch):
        with tempfile.TemporaryDirectory() as td:
            answers = os.path.join(td, "b.json")
            with open(answers, "w") as f:
                json.dump([], f)
            custom = FileChannel(answers)
            set_channel(custom)
            assert get_channel() is custom

    def test_reset_channel_clears_singleton(self):
        ch1 = get_channel()
        reset_channel()
        ch2 = get_channel()
        # After reset, a new instance is created
        assert ch1 is not ch2

    def test_file_channel_fails_closed_on_missing_answers(self, monkeypatch):
        """FileChannel with no matching entry raises — not silently skip."""
        with tempfile.TemporaryDirectory() as td:
            answers = os.path.join(td, "c.json")
            with open(answers, "w") as f:
                json.dump([], f)
            monkeypatch.setenv("HARNESS_HITL_FILE", answers)
            ch = get_channel()
            with pytest.raises(RuntimeError, match="No pre-recorded answer"):
                ch.confirm("Are you sure?")

"""Tests for the per-batch scope helpers in harness/graph.py.

Covers:
- ``_extend_batch_scope``: appends newly-touched files to
  ``batch_modified_files`` only when ``current_batch_id`` is non-zero.
- ``_scope_files_for_consumer``: routes consumer nodes (code_review,
  test_generation) to the batch list or to the cumulative session list,
  depending on whether we're in batch-mode.
- ``create_initial_state`` seeds ``batch_modified_files`` as an empty
  list so the TypedDict channel layer doesn't drop it.
"""

from __future__ import annotations

from typing import Any


from harness.graph import (
    _extend_batch_scope,
    _scope_files_for_consumer,
    create_initial_state,
)


def _state(**overrides: Any) -> dict[str, Any]:
    """Build a minimal state-like dict for the helpers under test."""
    base: dict[str, Any] = {
        "modified_files": [],
        "batch_modified_files": [],
        "current_batch_id": 0,
    }
    base.update(overrides)
    return base


class TestExtendBatchScope:
    def test_non_batch_mode_returns_existing_unchanged(self):
        st = _state(modified_files=["a.py"], batch_modified_files=["b.py"])
        # current_batch_id = 0 → not in batch-mode; existing batch list
        # is returned without modification.
        assert _extend_batch_scope(st, ["a.py", "c.py"]) == ["b.py"]

    def test_batch_mode_appends_new_files(self):
        st = _state(
            modified_files=["a.py"],
            batch_modified_files=["x.py"],
            current_batch_id=3,
        )
        # The node was called with state.modified_files=["a.py"] and
        # returns new modified=["a.py", "b.py", "c.py"]. New files are
        # ["b.py", "c.py"]; they append to the batch list after "x.py".
        result = _extend_batch_scope(st, ["a.py", "b.py", "c.py"])
        assert result == ["x.py", "b.py", "c.py"]

    def test_batch_mode_dedupes(self):
        st = _state(
            modified_files=["a.py"],
            batch_modified_files=["b.py"],
            current_batch_id=1,
        )
        # b.py is already in the batch list — adding it again must not
        # duplicate. Also a.py is in pre-call modified_files, so it's
        # not "new in this call" and shouldn't be added either.
        result = _extend_batch_scope(st, ["a.py", "b.py", "c.py"])
        assert result == ["b.py", "c.py"]

    def test_no_new_files_returns_existing_unchanged(self):
        st = _state(
            modified_files=["a.py", "b.py"],
            batch_modified_files=["a.py"],
            current_batch_id=1,
        )
        # Every file in the returned list was already in pre-call
        # modified_files → no new touches → unchanged batch list.
        assert _extend_batch_scope(st, ["a.py", "b.py"]) == ["a.py"]

    def test_empty_existing_batch_starts_fresh(self):
        st = _state(
            modified_files=["a.py"],
            batch_modified_files=[],
            current_batch_id=1,
        )
        result = _extend_batch_scope(st, ["a.py", "new.py"])
        assert result == ["new.py"]

    def test_preserves_insertion_order(self):
        st = _state(
            modified_files=["existing.py"],
            batch_modified_files=["first.py"],
            current_batch_id=2,
        )
        result = _extend_batch_scope(
            st, ["existing.py", "z.py", "a.py", "m.py"]
        )
        # Order from the patch result is preserved (no alphabetic sort)
        # so the operator sees the file list in apply-time order.
        assert result == ["first.py", "z.py", "a.py", "m.py"]


class TestScopeFilesForConsumer:
    def test_non_batch_mode_returns_session_modified_files(self):
        st = _state(modified_files=["a.py", "b.py"], current_batch_id=0)
        assert _scope_files_for_consumer(st) == ["a.py", "b.py"]

    def test_batch_mode_returns_batch_list(self):
        st = _state(
            modified_files=["a.py", "b.py"],
            batch_modified_files=["c.py"],
            current_batch_id=5,
        )
        # In batch-mode the consumer sees the batch scope, NOT the
        # session cumulative list.
        assert _scope_files_for_consumer(st) == ["c.py"]

    def test_batch_mode_empty_batch_list_falls_back_to_session(self):
        # On the very first invocation in a fresh batch, batch list may
        # be empty even though current_batch_id is set. The consumer
        # falls back to session modified_files so it doesn't no-op.
        st = _state(
            modified_files=["seed.py"],
            batch_modified_files=[],
            current_batch_id=1,
        )
        assert _scope_files_for_consumer(st) == ["seed.py"]

    def test_returns_copies_not_aliases(self):
        st = _state(modified_files=["a.py"], current_batch_id=0)
        out = _scope_files_for_consumer(st)
        out.append("mutated.py")
        assert st["modified_files"] == ["a.py"]


class TestInitialState:
    def test_create_initial_state_seeds_batch_modified_files_empty(self):
        state = create_initial_state(
            workspace_path="/tmp/fake",
            initial_prompt="x",
            build_command="true",
        )
        assert state.get("batch_modified_files") == []

    def test_create_initial_state_seeds_modified_files_empty(self):
        state = create_initial_state(
            workspace_path="/tmp/fake",
            initial_prompt="x",
            build_command="true",
        )
        # Sanity check that the existing session-level list is also
        # seeded — we're verifying both lists coexist on initial state.
        assert state.get("modified_files") == []

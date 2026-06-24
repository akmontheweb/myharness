"""Pytest fixtures shared across the suite.

Currently this file exists only to isolate the harness-global state.db
on a per-test basis. ``harness.story_state`` resolves the DB path via
the ``TEANE_STATE_DB`` env var first, so monkeypatching the env per
test reroutes every story_state.open_story_db() call inside that test
into its own ``tmp_path/state.db`` — the operator's real
``~/.harness/state.db`` is never read or written by the test suite.
"""

from __future__ import annotations


import pytest


@pytest.fixture(autouse=True)
def isolated_state_db(tmp_path, monkeypatch):
    """Per-test override of the global state.db location.

    Autouse so every test (story-mode or not) is automatically isolated.
    Tests that don't open state.db pay no cost — the env var is set but
    nothing reads it.
    """
    db = tmp_path / "isolated-state.db"
    monkeypatch.setenv("TEANE_STATE_DB", str(db))
    yield db
    # tmp_path cleanup handles the file removal.

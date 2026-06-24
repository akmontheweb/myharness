"""Tests for the CLI story-mode flags + CR→STORY bridge (step 8)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from harness import story_state


# ---------------------------------------------------------------------------
# CLI argparse — flags exist and parse correctly
# ---------------------------------------------------------------------------

def _parse_run_args(extra: list[str]) -> Any:
    from harness.cli import build_parser
    parser = build_parser()
    return parser.parse_args(["run", "-p", "do a thing", *extra])


def test_run_parser_exposes_stories_flag():
    args = _parse_run_args(["--stories", "true"])
    assert getattr(args, "decomposition_enabled") is True


def test_run_parser_defaults_stories_off():
    """Default OFF — opt-in. Today's monolithic flow stays for everyone
    who doesn't ask for the new behavior."""
    args = _parse_run_args([])
    assert getattr(args, "decomposition_enabled") is False


def test_run_parser_accepts_batch_size():
    args = _parse_run_args(["--story-batch-size", "10"])
    assert args.story_batch_size == 10


def test_run_parser_accepts_commit_on_story():
    args = _parse_run_args(["--commit-on-story", "true"])
    assert args.commit_on_story is True


def test_run_parser_accepts_story_repair_cap():
    args = _parse_run_args(["--story-repair-cap", "5"])
    assert args.story_repair_cap == 5


def test_run_parser_default_repair_cap_is_3():
    args = _parse_run_args([])
    assert args.story_repair_cap == 3


def test_run_parser_default_batch_size_is_5():
    args = _parse_run_args([])
    assert args.story_batch_size == 5


# ---------------------------------------------------------------------------
# create_initial_state — accepts the new kwargs
# ---------------------------------------------------------------------------

def test_create_initial_state_accepts_story_kwargs(tmp_path: Path):
    from harness.graph import create_initial_state
    s = create_initial_state(
        workspace_path=str(tmp_path),
        initial_prompt="p",
        build_command="make",
        decomposition_enabled=True,
        commit_on_story=True,
        story_batch_size=7,
        story_repair_cap=4,
        stories_db_path=str(tmp_path / "state.db"),
    )
    assert s["decomposition_enabled"] is True
    assert s["commit_on_story"] is True
    assert s["story_batch_size"] == 7
    assert s["story_repair_cap"] == 4
    assert s["stories_db_path"].endswith("state.db")


def test_create_initial_state_safe_defaults(tmp_path: Path):
    """All story fields default to safe no-ops — the monolithic flow
    must remain bit-for-bit identical when the caller doesn't opt in."""
    from harness.graph import create_initial_state
    s = create_initial_state(
        workspace_path=str(tmp_path),
        initial_prompt="p",
        build_command="make",
    )
    assert s["decomposition_enabled"] is False
    assert s["commit_on_story"] is False
    assert s["current_story_id"] == ""
    assert s["current_batch_id"] == 0
    assert s["story_scope_files"] == []
    assert s["story_modified_baseline"] == []
    assert s["stories_db_path"] == ""


# ---------------------------------------------------------------------------
# CR → STORY bridge
# ---------------------------------------------------------------------------

@pytest.fixture
def cr_workspace(tmp_path: Path) -> str:
    ws = tmp_path / "cr-ws"
    ws.mkdir()
    cr_dir = ws / "change_requests"
    cr_dir.mkdir()
    (cr_dir / "CR_001_add_login.txt").write_text("Add a /login endpoint.")
    (cr_dir / "CR_002_add_logout.txt").write_text("Add a /logout endpoint.")
    return str(ws)


def _ingest_state(workspace: str, **extra: Any) -> dict[str, Any]:
    base = {
        "workspace_path": workspace,
        "change_request_mode": True,
        "change_requests_dir_abs": os.path.join(workspace, "change_requests"),
        "archive_target_dir": os.path.join(workspace, "change_requests", "applied"),
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "seed"},
        ],
        "session_id": "sess-1",
        "loop_counter": {},
    }
    base.update(extra)
    return base


def test_cr_bridge_creates_one_story_per_cr_when_decomp_enabled(cr_workspace: str):
    from harness.graph import ingest_change_requests_node
    state = _ingest_state(cr_workspace, decomposition_enabled=True)
    out = asyncio.run(ingest_change_requests_node(state))

    assert out.get("stories_db_path", "").endswith("state.db")
    app = story_state.app_name_for_workspace(cr_workspace)
    conn = story_state.open_story_db()
    try:
        stories = story_state.list_stories(conn, app)
    finally:
        conn.close()
    assert len(stories) == 2
    refs = {s["external_ref"] for s in stories}
    assert refs == {"CR-1", "CR-2"}
    assert all(s["epic"] == "change-request" for s in stories)
    # Each story is tagged as a CR-kind row stamped with its CR id.
    assert all(s["build_kind"] == story_state.BUILD_KIND_CR for s in stories)
    cr_ids_seen = {s["cr_ids"][0] for s in stories if s["cr_ids"]}
    assert cr_ids_seen == {1, 2}


def test_cr_bridge_is_idempotent(cr_workspace: str):
    """Running ingest twice (resume) must not create duplicate rows —
    existing external_refs are skipped."""
    from harness.graph import ingest_change_requests_node
    state = _ingest_state(cr_workspace, decomposition_enabled=True)

    asyncio.run(ingest_change_requests_node(state))
    # Re-create archived files so the second ingest finds them again
    cr_dir = Path(cr_workspace, "change_requests")
    if not (cr_dir / "CR_001_add_login.txt").exists():
        (cr_dir / "CR_001_add_login.txt").write_text("Add a /login endpoint.")
    if not (cr_dir / "CR_002_add_logout.txt").exists():
        (cr_dir / "CR_002_add_logout.txt").write_text("Add a /logout endpoint.")
    asyncio.run(ingest_change_requests_node(state))

    app = story_state.app_name_for_workspace(cr_workspace)
    conn = story_state.open_story_db()
    try:
        stories = story_state.list_stories(conn, app)
    finally:
        conn.close()
    assert len(stories) == 2


def test_cr_bridge_skipped_when_decomp_disabled(cr_workspace: str):
    """Default flow — no story rows for this app, monolithic CR mode runs unchanged."""
    from harness.graph import ingest_change_requests_node
    state = _ingest_state(cr_workspace, decomposition_enabled=False)
    out = asyncio.run(ingest_change_requests_node(state))

    assert "stories_db_path" not in out
    # The global state.db may or may not exist; the guarantee is that
    # no rows landed for this app.
    app = story_state.app_name_for_workspace(cr_workspace)
    if os.path.isfile(story_state.state_db_path()):
        conn = story_state.open_story_db()
        try:
            assert story_state.list_stories(conn, app) == []
        finally:
            conn.close()

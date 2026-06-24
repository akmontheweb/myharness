"""Unit tests for harness/decomposition.py — the spec → stories node."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pytest

from harness import decomposition, story_state


# ---------------------------------------------------------------------------
# Test gateway double
# ---------------------------------------------------------------------------

@dataclass
class _FakeResponse:
    content: str


class _FakeGateway:
    """Replaces ``harness.gateway.Gateway`` for tests.

    Behavior:
      - ``responses`` queue: pop one per ``dispatch`` call.
      - If the queue is empty, raises so tests don't hang on a missing
        stub.
      - ``raise_on_call``: when set, raise the given exception instead
        of returning a response (covers the gateway-error branch).
    """

    def __init__(
        self,
        responses: list[str],
        *,
        raise_on_call: Optional[Exception] = None,
        budget_after: float = 1.50,
    ):
        self._responses = list(responses)
        self._raise = raise_on_call
        self._budget = budget_after
        self.calls: list[dict[str, Any]] = []

    async def dispatch(
        self, *, messages, role, budget_remaining_usd, **_kw
    ) -> tuple[_FakeResponse, float]:
        self.calls.append({
            "messages": list(messages),
            "role": role,
            "budget_in": budget_remaining_usd,
        })
        if self._raise is not None:
            raise self._raise
        if not self._responses:
            raise AssertionError("fake gateway out of responses")
        content = self._responses.pop(0)
        return _FakeResponse(content=content), self._budget


@pytest.fixture
def workspace(tmp_path: Path) -> str:
    ws = tmp_path / "decomp-ws"
    ws.mkdir()
    return str(ws)


@pytest.fixture(autouse=True)
def _clear_gateway():
    """Each test starts with no gateway registered."""
    from harness.graph import set_gateway
    prior_g = set_gateway.__globals__.get("_gateway")
    prior_c = set_gateway.__globals__.get("_gateway_config")
    set_gateway.__globals__["_gateway"] = None
    set_gateway.__globals__["_gateway_config"] = None
    yield
    set_gateway.__globals__["_gateway"] = prior_g
    set_gateway.__globals__["_gateway_config"] = prior_c


def _write_spec(workspace: str, body: str = "Build a TODO API.") -> None:
    docs = os.path.join(workspace, "docs")
    os.makedirs(docs, exist_ok=True)
    Path(os.path.join(docs, "SPEC_REQUIREMENTS.md")).write_text(body)


def _build_state(workspace: str, budget: float = 2.00) -> dict[str, Any]:
    return {
        "workspace_path": workspace,
        "messages": [{"role": "system", "content": "system"}],
        "budget_remaining_usd": budget,
    }


def _valid_payload() -> str:
    return json.dumps({
        "epics": [{"name": "core", "description": "MVP"}],
        "stories": [
            {
                "story_key": "STORY-1",
                "epic": "core",
                "title": "Create a TODO",
                "description": "POST /todos creates an item.",
                "acceptance_criteria": [
                    "POST /todos with title returns 201",
                    "Created item appears in GET /todos",
                ],
                "depends_on": [],
                "scope_files": ["src/todos/create.py"],
            },
            {
                "story_key": "STORY-2",
                "epic": "core",
                "title": "List TODOs",
                "description": "GET /todos returns the list.",
                "acceptance_criteria": ["GET /todos returns JSON array"],
                "depends_on": ["STORY-1"],
                "scope_files": ["src/todos/list.py"],
            },
        ],
        "summary": "Two stories: create and list.",
    })


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_accepts_minimal_valid_payload():
    payload = json.loads(_valid_payload())
    cleaned = decomposition._validate_stories_payload(payload)
    assert len(cleaned) == 2
    assert cleaned[0]["title"] == "Create a TODO"
    assert cleaned[1]["depends_on"] == ["STORY-1"]


def test_validate_rejects_non_dict():
    with pytest.raises(ValueError, match="top-level"):
        decomposition._validate_stories_payload(["not", "an", "object"])


def test_validate_rejects_empty_stories():
    with pytest.raises(ValueError, match="non-empty"):
        decomposition._validate_stories_payload({"stories": []})


def test_validate_rejects_over_cap():
    payload = {
        "stories": [
            {
                "story_key": f"STORY-{i}",
                "title": f"t{i}",
                "acceptance_criteria": ["x"],
            }
            for i in range(1, decomposition.MAX_STORIES_PER_PASS + 2)
        ]
    }
    with pytest.raises(ValueError, match="too many"):
        decomposition._validate_stories_payload(payload)


def test_validate_rejects_bad_story_key():
    payload = {"stories": [{
        "story_key": "ABC-1", "title": "t", "acceptance_criteria": ["x"]
    }]}
    with pytest.raises(ValueError, match="invalid story_key"):
        decomposition._validate_stories_payload(payload)


def test_validate_rejects_missing_acceptance():
    payload = {"stories": [{
        "story_key": "STORY-1", "title": "t", "acceptance_criteria": []
    }]}
    with pytest.raises(ValueError, match="acceptance"):
        decomposition._validate_stories_payload(payload)


def test_validate_rejects_forward_dependency():
    payload = {"stories": [
        {"story_key": "STORY-1", "title": "a",
         "acceptance_criteria": ["x"], "depends_on": ["STORY-2"]},
        {"story_key": "STORY-2", "title": "b",
         "acceptance_criteria": ["y"]},
    ]}
    with pytest.raises(ValueError, match="not declared earlier"):
        decomposition._validate_stories_payload(payload)


def test_validate_rejects_duplicate_keys():
    payload = {"stories": [
        {"story_key": "STORY-1", "title": "a", "acceptance_criteria": ["x"]},
        {"story_key": "STORY-1", "title": "b", "acceptance_criteria": ["y"]},
    ]}
    with pytest.raises(ValueError, match="duplicate"):
        decomposition._validate_stories_payload(payload)


# ---------------------------------------------------------------------------
# Fence stripping
# ---------------------------------------------------------------------------

def test_strip_json_fence_handles_fenced():
    raw = "```json\n{\"a\": 1}\n```"
    assert decomposition.strip_json_fence(raw) == '{"a": 1}'


def test_strip_json_fence_passes_through_clean():
    assert decomposition.strip_json_fence('{"a": 1}') == '{"a": 1}'


# ---------------------------------------------------------------------------
# Node — happy path
# ---------------------------------------------------------------------------

def test_decomposition_node_happy_path(workspace: str, monkeypatch):
    from harness.graph import set_gateway
    _write_spec(workspace)
    gw = _FakeGateway([_valid_payload()])
    set_gateway(gw)

    state = _build_state(workspace)
    out = asyncio.run(decomposition.decomposition_node(state))

    assert out["current_gate"] == "STORIES"
    assert out["node_state"]["decomposition_complete"] is True
    assert out["node_state"]["story_count"] == 2
    assert out["node_state"]["story_keys"] == ["STORY-1", "STORY-2"]
    assert out["stories_db_path"].endswith("state.db")
    assert out["budget_remaining_usd"] == 1.50

    # DB has the stories
    app = story_state.app_name_for_workspace(workspace)
    conn = story_state.open_story_db()
    try:
        stories = story_state.list_stories(conn, app)
    finally:
        conn.close()
    assert [s["story_key"] for s in stories] == ["STORY-1", "STORY-2"]
    assert stories[1]["depends_on"] == ["STORY-1"]

    # Markdown view regenerated
    assert os.path.exists(os.path.join(workspace, "docs", "STORIES.md"))


def test_decomposition_node_uses_planning_role(workspace: str):
    from harness.graph import set_gateway
    from harness.gateway import NodeRole
    _write_spec(workspace)
    gw = _FakeGateway([_valid_payload()])
    set_gateway(gw)

    asyncio.run(decomposition.decomposition_node(_build_state(workspace)))
    assert gw.calls[0]["role"] == NodeRole.PLANNING


# ---------------------------------------------------------------------------
# Node — error paths
# ---------------------------------------------------------------------------

def test_decomposition_node_no_spec(workspace: str):
    """Spec missing → graceful error, no DB write for this app."""
    from harness.graph import set_gateway
    set_gateway(_FakeGateway([]))  # should not be called
    out = asyncio.run(decomposition.decomposition_node(_build_state(workspace)))
    assert out["node_state"]["error"] == "spec_requirements_missing"
    assert out["node_state"]["story_count"] == 0
    # Global state.db may or may not exist depending on test ordering;
    # the important guarantee is that no rows landed for this app.
    app = story_state.app_name_for_workspace(workspace)
    if os.path.isfile(story_state.state_db_path()):
        conn = story_state.open_story_db()
        try:
            assert story_state.list_stories(conn, app) == []
        finally:
            conn.close()


def test_decomposition_node_no_gateway(workspace: str):
    _write_spec(workspace)
    out = asyncio.run(decomposition.decomposition_node(_build_state(workspace)))
    assert out["node_state"]["error"] == "no_gateway"


def test_decomposition_node_budget_exhausted(workspace: str):
    from harness.graph import set_gateway
    _write_spec(workspace)
    set_gateway(_FakeGateway([]))
    out = asyncio.run(
        decomposition.decomposition_node(_build_state(workspace, budget=0.0))
    )
    assert out["node_state"]["error"] == "budget_exhausted"


def test_decomposition_node_invalid_json(workspace: str):
    from harness.graph import set_gateway
    _write_spec(workspace)
    set_gateway(_FakeGateway(["not actually json"]))
    out = asyncio.run(decomposition.decomposition_node(_build_state(workspace)))
    assert out["node_state"]["error"].startswith("invalid_json")
    # DB should not have been populated for this app
    app = story_state.app_name_for_workspace(workspace)
    conn = story_state.open_story_db()
    try:
        assert story_state.list_stories(conn, app) == []
    finally:
        conn.close()


def test_decomposition_node_validation_failure(workspace: str):
    from harness.graph import set_gateway
    _write_spec(workspace)
    bad = json.dumps({"stories": [{
        "story_key": "BAD-1", "title": "x", "acceptance_criteria": ["y"]
    }]})
    set_gateway(_FakeGateway([bad]))
    out = asyncio.run(decomposition.decomposition_node(_build_state(workspace)))
    assert out["node_state"]["error"].startswith("validation")


def test_decomposition_node_dispatch_exception(workspace: str):
    from harness.graph import set_gateway
    _write_spec(workspace)
    set_gateway(_FakeGateway([], raise_on_call=RuntimeError("upstream 503")))
    out = asyncio.run(decomposition.decomposition_node(_build_state(workspace)))
    assert out["node_state"]["error"].startswith("dispatch_failed")
    assert "upstream 503" in out["node_state"]["error"]

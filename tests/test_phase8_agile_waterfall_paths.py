"""Phase 8 — agile vs waterfall spec creation.

The discovery interview and the reverse-engineer prompt now branch
on ``state["decomposition_enabled"]`` so the spec the operator gets
matches the chosen flow:

- Agile (``decomposition_enabled=True``) — INVEST stories framed
  through ``harness/skills/docgen/requirements_discovery.md``; the
  reverse-engineer prompt asks for SAFe ``EPIC / FEAT / STORY``
  shape.
- Waterfall (``decomposition_enabled=False``) — flat FR/NFR through
  ``harness/skills/docgen/requirements_discovery_waterfall.md``; the
  reverse-engineer prompt asks for ISO-29148 ``FR-NNN`` shape with
  "shall" language.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fixtures + stubs
# ---------------------------------------------------------------------------

@dataclass
class _StubUsage:
    input_tokens: int = 100
    output_tokens: int = 80
    cached_tokens: int = 0
    cost_usd: float = 0.001
    model: str = "stub"


@dataclass
class _StubResponse:
    content: str
    usage: _StubUsage = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.usage is None:
            self.usage = _StubUsage()


class _CapturingGateway:
    """Records the messages dispatched so tests can inspect the prompt."""

    class config:
        repair_fallback = ""
        planning_fallback = ""

    def __init__(self, response_content: str = '{"modules": [], "complete": true, "summary": "ok"}'):
        self._response = response_content
        self.dispatched: list[dict[str, Any]] = []

    async def dispatch(self, *, messages, role, budget_remaining_usd, **_kw):
        self.dispatched.append({"messages": list(messages), "role": role})
        return _StubResponse(content=self._response), budget_remaining_usd - 0.001

    def aggregate_tokens(self, tracker, usage, role=None):
        out = dict(tracker or {})
        out["total_cost_usd"] = out.get("total_cost_usd", 0.0) + float(usage.cost_usd)
        return out


@pytest.fixture
def stub_gateway():
    from harness.graph import set_gateway
    holder: dict[str, _CapturingGateway] = {}

    def _set(content: str = '{"modules": [], "complete": true, "summary": "ok"}') -> _CapturingGateway:
        gw = _CapturingGateway(content)
        set_gateway(gw)
        holder["gw"] = gw
        return gw

    yield _set
    set_gateway(None)


def _build_discovery_state(workspace: str, *, agile: bool) -> dict[str, Any]:
    return {
        "workspace_path": workspace,
        "messages": [{"role": "system", "content": "system"}],
        "budget_remaining_usd": 1.5,
        "decomposition_enabled": agile,
        "node_state": {},
    }


# ---------------------------------------------------------------------------
# Phase 8a — requirements_discovery_node picks the right skill
# ---------------------------------------------------------------------------

class TestDiscoveryInterviewPromptBranching:

    @pytest.mark.asyncio
    async def test_agile_loads_INVEST_prompt(self, tmp_path, stub_gateway):
        from harness.graph import requirements_discovery_node
        gw = stub_gateway()
        state = _build_discovery_state(str(tmp_path), agile=True)
        await requirements_discovery_node(state)
        # User-role message is the prompt body.
        joined = "\n".join(
            m.get("content", "") for m in gw.dispatched[0]["messages"]
            if m.get("role") == "user"
        )
        # The agile (existing) prompt mentions INVEST + Given/When/Then.
        assert "INVEST" in joined
        assert "FEATURES & USER STORIES" in joined

    @pytest.mark.asyncio
    async def test_waterfall_loads_shall_prompt(self, tmp_path, stub_gateway):
        from harness.graph import requirements_discovery_node
        gw = stub_gateway()
        state = _build_discovery_state(str(tmp_path), agile=False)
        await requirements_discovery_node(state)
        joined = "\n".join(
            m.get("content", "") for m in gw.dispatched[0]["messages"]
            if m.get("role") == "user"
        )
        # Waterfall variant frames Sector 2 around FRs and "shall".
        assert "FUNCTIONAL REQUIREMENTS" in joined
        assert "shall" in joined.lower()
        # Distinct from agile: the section name is FR not "FEATURES &
        # USER STORIES" (the agile heading). The word "INVEST" may
        # appear in the disclaimer ("no INVEST framing") so we don't
        # assert its absence — instead pin the positive marker for
        # the waterfall section heading.
        assert "FEATURES & USER STORIES" not in joined
        assert "ISO 29148" in joined


# ---------------------------------------------------------------------------
# Phase 8c — reverse_spec_node prompt branches on --agile
# ---------------------------------------------------------------------------

class TestReverseSpecPromptBranching:

    @pytest.mark.asyncio
    async def test_agile_reverse_spec_requests_safe_shape(
        self, tmp_path, stub_gateway,
    ):
        from harness.graph import reverse_spec_node
        # Reverse-spec node returns a draft as a system/user message;
        # canned content shape doesn't matter for the prompt assertion.
        gw = stub_gateway(content="<SPEC_REQUIREMENTS>\n# stub\n<SPEC_ARCHITECTURE>\n# stub\n")
        state = {
            "workspace_path": str(tmp_path),
            "messages": [],
            "budget_remaining_usd": 1.5,
            "decomposition_enabled": True,
            "generate_specs": True,
            "node_state": {},
        }
        await reverse_spec_node(state)
        joined = "\n".join(
            m.get("content", "") for m in gw.dispatched[0]["messages"]
            if m.get("role") == "user"
        )
        # Agile prompt mentions Epic/Feature/Story headings explicitly.
        assert "EPIC-NNN" in joined or "Epic:" in joined
        assert "FEAT-NNN" in joined or "Feature:" in joined
        assert "STORY-NNN" in joined or "Story:" in joined
        assert "Given/When/Then" in joined or "agile" in joined.lower()

    @pytest.mark.asyncio
    async def test_waterfall_reverse_spec_requests_flat_FR_shape(
        self, tmp_path, stub_gateway,
    ):
        from harness.graph import reverse_spec_node
        gw = stub_gateway(content="<SPEC_REQUIREMENTS>\n# stub\n<SPEC_ARCHITECTURE>\n# stub\n")
        state = {
            "workspace_path": str(tmp_path),
            "messages": [],
            "budget_remaining_usd": 1.5,
            "decomposition_enabled": False,
            "generate_specs": True,
            "node_state": {},
        }
        await reverse_spec_node(state)
        joined = "\n".join(
            m.get("content", "") for m in gw.dispatched[0]["messages"]
            if m.get("role") == "user"
        )
        # Waterfall prompt mentions flat FR-NNN + "shall".
        assert "FR-NNN" in joined
        assert "shall" in joined.lower()
        # Includes the negative-shape disclaimer telling the LLM NOT
        # to use agile vocabulary (the word "Epic" appears in the
        # disclaimer; check that the prompt explicitly forbids it).
        assert "Do NOT use agile vocabulary" in joined

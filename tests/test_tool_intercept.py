"""Regression tests for the graph's tool-block interceptor (planning_node).

Covers:
    - ``_run_tool_loop`` returns input unchanged when no tool blocks are
      present (the "common case" — must add zero cost).
    - When blocks are present and the skill is unregistered, the loop
      surfaces a "not registered" message back into the conversation and
      stops (never crashes).
    - When blocks are present and skills ARE registered, the loop
      executes them, appends the result, re-dispatches, and strips the
      blocks from the final content (so the patcher never sees them).
    - The round cap is enforced.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.gateway import (
    Gateway,
    GatewayConfig,
    LLMResponse,
    NodeRole,
    TokenUsage,
)
from harness.graph import _run_tool_loop
from harness.skills import (
    SkillParameter,
    SkillRegistry,
    SkillSchema,
    SkillType,
    ToolSkill,
)


def _make_gateway_with_scripted(responses: list[LLMResponse]) -> Gateway:
    """Returns a Gateway whose dispatch yields ``responses`` in order."""
    cfg = GatewayConfig(
        planning_primary="stub:tool",
        patching_primary="stub:tool",
        repair_primary="stub:tool",
    )
    gw = Gateway(cfg)
    idx = {"i": 0}

    async def fake_dispatch(
        *,
        messages: list[dict[str, Any]],
        role: Any,
        budget_remaining_usd: float,
        **_kwargs: Any,
    ):
        out = responses[min(idx["i"], len(responses) - 1)]
        idx["i"] += 1
        return out, max(0.0, budget_remaining_usd - 0.001)

    gw.dispatch = fake_dispatch  # type: ignore[assignment]
    return gw


def _drop_test_skill(name: str) -> None:
    reg = SkillRegistry()
    reg._skills.pop(name, None)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Common-case: no blocks → no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_tool_loop_no_blocks_is_noop():
    gw = _make_gateway_with_scripted([])
    content = "Here is my plan with no tool calls."
    final, msgs, budget, rounds = await _run_tool_loop(
        initial_response_content=content,
        messages=[],
        gateway=gw,
        role=NodeRole.PLANNING,
        budget=1.00,
        cap=3,
    )
    assert final == content
    assert msgs == []
    assert budget == 1.00
    assert rounds == 0


# ---------------------------------------------------------------------------
# Block present but skill unregistered → graceful "not registered" message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_tool_loop_unregistered_skill_does_not_crash():
    _drop_test_skill("web_fetch")
    _drop_test_skill("web_search")
    # Two scripted re-dispatches: the first sees the "not registered" tool
    # result, the second can decide to proceed without tools.
    scripted = [
        LLMResponse(
            content="OK, proceeding without the docs.",
            usage=TokenUsage(input_tokens=1, output_tokens=1, model_name="stub", cost_usd=0.0),
            model="stub",
        ),
    ]
    gw = _make_gateway_with_scripted(scripted)
    content = 'I need docs. <<<WEB_FETCH url="https://example.com/">>>'
    final, msgs, budget, rounds = await _run_tool_loop(
        initial_response_content=content,
        messages=[],
        gateway=gw,
        role=NodeRole.PLANNING,
        budget=1.00,
        cap=3,
    )
    # Loop ran 1 round (one block intercepted), then the scripted second
    # response had no blocks so it terminated.
    assert rounds == 1
    # The tool-result message body must mention "not registered".
    flat = "\n".join(str(m) for m in msgs)
    assert "not registered" in flat
    # Final content has the <<<...>>> stripped.
    assert "<<<" not in final


# ---------------------------------------------------------------------------
# Block present, skill registered → executes, re-dispatches, strips blocks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_tool_loop_executes_registered_skill_and_strips_block():
    _drop_test_skill("web_fetch")
    _drop_test_skill("web_search")

    called: list[dict[str, Any]] = []

    async def fake_web_fetch(**kwargs: Any) -> dict[str, Any]:
        called.append(kwargs)
        return {"url": kwargs.get("url"), "content": "FAKE PAGE BODY", "truncated": False}

    SkillRegistry().register(ToolSkill(
        SkillSchema(
            name="web_fetch",
            description="test",
            skill_type=SkillType.TOOL,
            parameters=[SkillParameter("url", "string", "url", required=True)],
        ),
        fn=fake_web_fetch,
    ))

    scripted = [
        LLMResponse(
            content="All done — no more tools needed.",
            usage=TokenUsage(input_tokens=1, output_tokens=1, model_name="stub", cost_usd=0.0),
            model="stub",
        ),
    ]
    gw = _make_gateway_with_scripted(scripted)
    content = 'Reading: <<<WEB_FETCH url="https://example.com/x">>>\nWill plan after.'
    final, msgs, budget, rounds = await _run_tool_loop(
        initial_response_content=content,
        messages=[],
        gateway=gw,
        role=NodeRole.PLANNING,
        budget=1.00,
        cap=3,
    )
    assert called == [{"url": "https://example.com/x"}]
    assert rounds == 1
    assert "<<<" not in final
    # The intermediate assistant message (with the fetch block stripped)
    # was appended to the conversation; then a user tool-result message;
    # then the loop re-dispatched and got "All done".
    assert any(m["role"] == "user" and "FAKE PAGE BODY" in m["content"] for m in msgs)
    _drop_test_skill("web_fetch")


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_tool_loop_enforces_cap():
    _drop_test_skill("web_fetch")
    _drop_test_skill("web_search")

    async def loop_forever_fetch(**_kwargs: Any) -> dict[str, Any]:
        return {"content": "more results here"}

    SkillRegistry().register(ToolSkill(
        SkillSchema(
            name="web_fetch",
            description="test",
            skill_type=SkillType.TOOL,
            parameters=[SkillParameter("url", "string", "url", required=True)],
        ),
        fn=loop_forever_fetch,
    ))

    # Always emit a fresh tool block — would loop indefinitely without the cap.
    scripted = [
        LLMResponse(
            content='<<<WEB_FETCH url="https://example.com/loop">>>',
            usage=TokenUsage(input_tokens=1, output_tokens=1, model_name="stub", cost_usd=0.0),
            model="stub",
        ),
    ] * 10  # pad — only first `cap` will be consumed
    gw = _make_gateway_with_scripted(scripted)
    final, msgs, budget, rounds = await _run_tool_loop(
        initial_response_content='<<<WEB_FETCH url="https://example.com/loop">>>',
        messages=[],
        gateway=gw,
        role=NodeRole.PLANNING,
        budget=1.00,
        cap=2,
    )
    assert rounds == 2
    # Final content was the last re-dispatched response; tool blocks stripped.
    assert "<<<" not in final
    _drop_test_skill("web_fetch")

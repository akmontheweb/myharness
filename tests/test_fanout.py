"""Regression tests for the multi-agent fan-out (#11).

The runner is exercised against a stub gateway that records
every dispatch and lets each test script the response. The
gateway is concurrency-safe (per-call lock) so we can assert
on the parallelism observed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from harness.fanout import (
    AgentSpec,
    _parse_first_json,
    make_fanout_skill,
    run_parallel_agents,
    run_with_verification,
)
from harness.gateway import LLMResponse, TokenUsage


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _ConcurrencyGateway:
    """Stub gateway whose dispatch sleeps for a configurable delay so we
    can observe the runner's concurrency. Also captures every messages
    payload so callers can assert on them."""

    def __init__(
        self,
        *,
        delay_seconds: float = 0.01,
        responses_by_user: dict[str, str] | None = None,
        default_response: str = "ok",
        per_call_cost: float = 0.001,
    ):
        self._delay = delay_seconds
        self._responses = responses_by_user or {}
        self._default = default_response
        self._cost = per_call_cost
        self.calls: list[list[dict[str, Any]]] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self._lock = asyncio.Lock()

    async def dispatch(
        self, *, messages, role, budget_remaining_usd, model_override=None,
        **_kwargs,
    ):
        async with self._lock:
            self.calls.append(list(messages))
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        await asyncio.sleep(self._delay)
        async with self._lock:
            self.in_flight -= 1
        user_text = ""
        for m in messages:
            if m.get("role") == "user":
                user_text = str(m.get("content") or "")
                break
        text = self._responses.get(user_text, self._default)
        usage = TokenUsage(
            input_tokens=10, output_tokens=5,
            model_name="stub:fan", cost_usd=self._cost,
        )
        new_budget = max(0.0, budget_remaining_usd - self._cost)
        return (
            LLMResponse(content=text, usage=usage, model="stub:fan"),
            new_budget,
        )


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_parallel_agents_empty_input():
    gw = _ConcurrencyGateway()
    results, budget = await run_parallel_agents(
        [], gw, budget_remaining_usd=1.0,
    )
    assert results == []
    assert budget == 1.0


@pytest.mark.asyncio
async def test_run_parallel_agents_preserves_input_order():
    gw = _ConcurrencyGateway(
        delay_seconds=0.005,
        responses_by_user={"a": "A", "b": "B", "c": "C", "d": "D"},
    )
    specs = [
        AgentSpec(name=str(i), user_prompt=p)
        for i, p in enumerate(["a", "b", "c", "d"])
    ]
    results, _ = await run_parallel_agents(
        specs, gw, budget_remaining_usd=1.0,
    )
    assert [r.content for r in results] == ["A", "B", "C", "D"]
    assert [r.name for r in results] == ["0", "1", "2", "3"]


@pytest.mark.asyncio
async def test_run_parallel_agents_respects_max_concurrency():
    gw = _ConcurrencyGateway(delay_seconds=0.02)
    specs = [
        AgentSpec(name=str(i), user_prompt=str(i)) for i in range(6)
    ]
    results, _ = await run_parallel_agents(
        specs, gw, budget_remaining_usd=1.0, max_concurrency=2,
    )
    assert len(results) == 6
    assert all(r.success for r in results)
    assert gw.peak_in_flight <= 2


@pytest.mark.asyncio
async def test_run_parallel_agents_deducts_cost_from_shared_budget():
    gw = _ConcurrencyGateway(per_call_cost=0.04)
    specs = [AgentSpec(name=str(i), user_prompt=str(i)) for i in range(3)]
    _, new_budget = await run_parallel_agents(
        specs, gw, budget_remaining_usd=1.0,
    )
    # 3 calls × $0.04 = $0.12
    assert new_budget == pytest.approx(1.0 - 0.12, rel=1e-6)


@pytest.mark.asyncio
async def test_run_parallel_agents_rejects_when_shared_budget_exhausted():
    gw = _ConcurrencyGateway(per_call_cost=0.5)
    specs = [
        AgentSpec(name=str(i), user_prompt=str(i), budget_hint=0.5)
        for i in range(4)
    ]
    results, new_budget = await run_parallel_agents(
        specs, gw, budget_remaining_usd=1.0, max_concurrency=4,
    )
    successes = sum(1 for r in results if r.success)
    rejects = sum(
        1 for r in results
        if (not r.success) and r.error and "budget exhausted" in r.error
    )
    # We can afford at most 2 of 4 at $0.50 each.
    assert successes <= 2
    assert rejects >= 2
    assert new_budget >= 0.0


@pytest.mark.asyncio
async def test_run_parallel_agents_handles_dispatch_exception():
    class _BoomGateway:
        async def dispatch(self, **_kw):
            raise RuntimeError("simulated provider failure")

    specs = [AgentSpec(name="x", user_prompt="hi")]
    results, _ = await run_parallel_agents(
        specs, _BoomGateway(), budget_remaining_usd=1.0,
    )
    assert results[0].success is False
    assert "simulated provider failure" in (results[0].error or "")


@pytest.mark.asyncio
async def test_run_parallel_agents_timeout_returns_error():
    class _SlowGateway:
        async def dispatch(self, **_kw):
            await asyncio.sleep(0.5)
            usage = TokenUsage(input_tokens=1, output_tokens=1, model_name="stub", cost_usd=0.0)
            return LLMResponse(content="late", usage=usage, model="stub"), 1.0

    specs = [
        AgentSpec(name="slow", user_prompt="x", timeout_seconds=0.05),
    ]
    results, _ = await run_parallel_agents(
        specs, _SlowGateway(), budget_remaining_usd=1.0,
    )
    assert results[0].success is False
    assert "timeout" in (results[0].error or "")


# ---------------------------------------------------------------------------
# Adversarial verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_with_verification_accepts_majority_accept():
    gw = _ConcurrencyGateway(
        responses_by_user={},
        default_response='{"refuted": false, "reason": "looks good"}',
    )
    finder_spec = AgentSpec(name="finder", user_prompt="find bugs")
    # Override the finder response by using a separate stub class that
    # routes on role; here we just have all the verifiers say "not
    # refuted" so the finding stands.
    finder, verdict, _ = await run_with_verification(
        finder_spec, gateway=gw, budget_remaining_usd=2.0,
        n_verifiers=3,
    )
    assert finder.success is True
    assert verdict.is_real is True
    assert verdict.confidence > 0.5
    assert len(verdict.votes) == 3


@pytest.mark.asyncio
async def test_run_with_verification_rejects_when_majority_refute():
    gw = _ConcurrencyGateway(
        default_response='{"refuted": true, "reason": "wrong path"}',
    )
    finder_spec = AgentSpec(name="finder", user_prompt="find bugs")
    finder, verdict, _ = await run_with_verification(
        finder_spec, gateway=gw, budget_remaining_usd=2.0,
        n_verifiers=3,
    )
    assert finder.success is True
    assert verdict.is_real is False
    assert verdict.confidence <= 0.5


@pytest.mark.asyncio
async def test_run_with_verification_finder_failure_skips_verifiers():
    class _FailingFinderGateway:
        def __init__(self):
            self.calls = 0

        async def dispatch(self, **_kw):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("finder went boom")
            # If we ever get here, verifiers ran — fail the test.
            raise AssertionError("verifier was dispatched after finder failure")

    finder_spec = AgentSpec(name="f", user_prompt="x")
    finder, verdict, _ = await run_with_verification(
        finder_spec, gateway=_FailingFinderGateway(),
        budget_remaining_usd=2.0, n_verifiers=3,
    )
    assert finder.success is False
    assert verdict.votes == []


# ---------------------------------------------------------------------------
# JSON extractor
# ---------------------------------------------------------------------------

def test_parse_first_json_finds_object_in_noise():
    text = 'preamble {"refuted": false, "reason": "ok"} trailing'
    out = _parse_first_json(text)
    assert out == {"refuted": False, "reason": "ok"}


def test_parse_first_json_returns_none_when_no_object():
    assert _parse_first_json("just text") is None
    assert _parse_first_json("") is None


def test_parse_first_json_handles_nested_braces():
    text = '{"outer": {"inner": 1}}'
    out = _parse_first_json(text)
    assert out == {"outer": {"inner": 1}}


# ---------------------------------------------------------------------------
# SubAgentFanoutSkill — text-DSL entry point
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fanout_skill_dispatches_through_registered_gateway(monkeypatch):
    gw = _ConcurrencyGateway(
        responses_by_user={"a": "alpha", "b": "beta"},
    )

    def _stub_get_gateway():
        return gw

    monkeypatch.setattr("harness.graph.get_gateway", _stub_get_gateway)
    skill = make_fanout_skill()
    result = await skill.execute(
        prompts='["a", "b"]',
        budget_usd=1.0,
    )
    assert isinstance(result, dict)
    assert "results" in result
    rows = result["results"]
    assert [r["content"] for r in rows] == ["alpha", "beta"]
    assert all(r["success"] for r in rows)


@pytest.mark.asyncio
async def test_fanout_skill_rejects_malformed_prompts(monkeypatch):
    monkeypatch.setattr("harness.graph.get_gateway", lambda: _ConcurrencyGateway())
    skill = make_fanout_skill()
    result = await skill.execute(prompts="not-json-at-all")
    assert "error" in result


@pytest.mark.asyncio
async def test_fanout_skill_supports_named_specs(monkeypatch):
    gw = _ConcurrencyGateway(responses_by_user={"question": "answer"})
    monkeypatch.setattr("harness.graph.get_gateway", lambda: gw)
    skill = make_fanout_skill()
    result = await skill.execute(
        prompts=(
            '[{"name": "sector-auth", "system_prompt": "be brief", '
            '"user_prompt": "question"}]'
        ),
    )
    rows = result["results"]
    assert rows[0]["name"] == "sector-auth"
    assert rows[0]["content"] == "answer"


@pytest.mark.asyncio
async def test_fanout_skill_returns_error_when_no_gateway(monkeypatch):
    monkeypatch.setattr("harness.graph.get_gateway", lambda: None)
    skill = make_fanout_skill()
    result = await skill.execute(prompts='["x"]')
    assert "error" in result and "gateway" in result["error"].lower()

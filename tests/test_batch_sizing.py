"""Tests for harness/batch_sizing.py — LLM-proposed batches with
deterministic fallback."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from harness.batch_sizing import (
    MAX_STORIES,
    deterministic_batches,
    propose_or_fallback,
    validate_batches,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _story(key: str, *, deps: list[str] | None = None) -> dict[str, Any]:
    return {
        "story_key": key,
        "title": f"Story {key}",
        "depends_on": deps or [],
    }


class _FakeGateway:
    """Records each dispatch call; replies with a scripted JSON string.

    Tests inject one to exercise the LLM path without a real API."""

    def __init__(self, reply_content: str, *, cost: float = 0.01,
                 raise_exc: Exception | None = None):
        self._reply = reply_content
        self._cost = cost
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def dispatch(self, *, messages, role, budget_remaining_usd, **_kw):
        self.calls.append({
            "messages": messages,
            "role": role,
            "budget_remaining_usd": budget_remaining_usd,
        })
        if self._raise is not None:
            raise self._raise
        response = SimpleNamespace(content=self._reply)
        return response, max(0.0, budget_remaining_usd - self._cost)


# ---------------------------------------------------------------------------
# validate_batches
# ---------------------------------------------------------------------------

class TestValidate:
    def test_empty_stories_and_empty_batches_is_ok(self):
        assert validate_batches([], []) == []

    def test_empty_batches_with_stories_present_is_error(self):
        assert validate_batches([_story("STORY-1")], []) == [
            "batches is empty but stories were provided"
        ]

    def test_single_story_single_batch_is_ok(self):
        errs = validate_batches(
            [_story("STORY-1")],
            [{"batch_id": 1, "story_keys": ["STORY-1"]}],
        )
        assert errs == []

    def test_missing_story_is_flagged(self):
        errs = validate_batches(
            [_story("STORY-1"), _story("STORY-2")],
            [{"batch_id": 1, "story_keys": ["STORY-1"]}],
        )
        assert errs == ["story STORY-2 is not assigned to any batch"]

    def test_duplicate_story_is_flagged(self):
        errs = validate_batches(
            [_story("STORY-1"), _story("STORY-2")],
            [
                {"batch_id": 1, "story_keys": ["STORY-1", "STORY-2"]},
                {"batch_id": 2, "story_keys": ["STORY-1"]},
            ],
        )
        assert any("STORY-1 appears in batch 1 and batch 2" in e for e in errs)

    def test_unknown_story_in_batch_is_flagged(self):
        errs = validate_batches(
            [_story("STORY-1")],
            [{"batch_id": 1, "story_keys": ["STORY-1", "STORY-99"]}],
        )
        assert any("unknown story_key 'STORY-99'" in e for e in errs)

    def test_forward_cross_batch_dep_is_flagged(self):
        # STORY-2 depends on STORY-1; placing STORY-1 in batch 2 and
        # STORY-2 in batch 1 makes STORY-2's dep land in a LATER batch.
        # Post-Phase-I this is still forbidden — only same-batch or
        # earlier-batch deps are allowed.
        errs = validate_batches(
            [_story("STORY-1"), _story("STORY-2", deps=["STORY-1"])],
            [
                {"batch_id": 1, "story_keys": ["STORY-2"]},
                {"batch_id": 2, "story_keys": ["STORY-1"]},
            ],
        )
        assert any(
            "dep must be in an earlier or same batch" in e for e in errs
        )

    def test_same_batch_dep_in_correct_order_is_accepted(self):
        # Phase I: same-batch dependencies are now ALLOWED when the
        # dep comes first in story_keys. STORY-2 depends on STORY-1
        # and STORY-1 is listed first, so this is valid.
        errs = validate_batches(
            [_story("STORY-1"), _story("STORY-2", deps=["STORY-1"])],
            [{"batch_id": 1, "story_keys": ["STORY-1", "STORY-2"]}],
        )
        assert errs == []

    def test_same_batch_dep_out_of_order_is_flagged(self):
        # The reverse — STORY-2 listed before its dep STORY-1 — must
        # still be flagged because _next_story_in_batch walks
        # story_keys in list order.
        errs = validate_batches(
            [_story("STORY-1"), _story("STORY-2", deps=["STORY-1"])],
            [{"batch_id": 1, "story_keys": ["STORY-2", "STORY-1"]}],
        )
        assert any("must come BEFORE its dependent" in e for e in errs)

    def test_orphan_dep_is_ignored(self):
        # STORY-1 references EXTERNAL-9 which isn't in the input — that's
        # fine; the validator only checks edges that target known stories.
        errs = validate_batches(
            [_story("STORY-1", deps=["EXTERNAL-9"])],
            [{"batch_id": 1, "story_keys": ["STORY-1"]}],
        )
        assert errs == []

    def test_batch_id_must_start_at_1(self):
        errs = validate_batches(
            [_story("STORY-1")],
            [{"batch_id": 2, "story_keys": ["STORY-1"]}],
        )
        assert any("expected 1, got 2" in e for e in errs)

    def test_batch_id_must_be_contiguous(self):
        errs = validate_batches(
            [_story("STORY-1"), _story("STORY-2")],
            [
                {"batch_id": 1, "story_keys": ["STORY-1"]},
                {"batch_id": 3, "story_keys": ["STORY-2"]},
            ],
        )
        assert any("expected 2, got 3" in e for e in errs)


# ---------------------------------------------------------------------------
# deterministic_batches
# ---------------------------------------------------------------------------

class TestDeterministic:
    def test_empty_input_returns_empty(self):
        assert deterministic_batches([]) == []

    def test_single_story_single_batch(self):
        out = deterministic_batches([_story("STORY-1")])
        assert out == [{"batch_id": 1, "story_keys": ["STORY-1"]}]

    def test_three_independent_stories_one_batch(self):
        out = deterministic_batches([
            _story("STORY-1"), _story("STORY-2"), _story("STORY-3"),
        ])
        assert out == [{"batch_id": 1, "story_keys": ["STORY-1", "STORY-2", "STORY-3"]}]

    def test_chain_dependency_splits_per_layer(self):
        out = deterministic_batches([
            _story("STORY-1"),
            _story("STORY-2", deps=["STORY-1"]),
            _story("STORY-3", deps=["STORY-2"]),
        ])
        assert out == [
            {"batch_id": 1, "story_keys": ["STORY-1"]},
            {"batch_id": 2, "story_keys": ["STORY-2"]},
            {"batch_id": 3, "story_keys": ["STORY-3"]},
        ]

    def test_diamond_dependency(self):
        # STORY-1 → (STORY-2, STORY-3) → STORY-4
        out = deterministic_batches([
            _story("STORY-1"),
            _story("STORY-2", deps=["STORY-1"]),
            _story("STORY-3", deps=["STORY-1"]),
            _story("STORY-4", deps=["STORY-2", "STORY-3"]),
        ])
        assert out == [
            {"batch_id": 1, "story_keys": ["STORY-1"]},
            {"batch_id": 2, "story_keys": ["STORY-2", "STORY-3"]},
            {"batch_id": 3, "story_keys": ["STORY-4"]},
        ]

    def test_batch_size_hint_slices_wide_layers(self):
        # 7 independent stories with hint=3 → batches of 3, 3, 1
        keys = [f"STORY-{i}" for i in range(1, 8)]
        out = deterministic_batches(
            [_story(k) for k in keys], batch_size_hint=3
        )
        assert [len(b["story_keys"]) for b in out] == [3, 3, 1]
        # Every story must appear exactly once
        flat = [k for b in out for k in b["story_keys"]]
        assert sorted(flat) == keys

    def test_orphan_dep_treated_as_satisfied(self):
        # STORY-1's only dep is on an external story not in the input.
        # Deterministic batcher should treat it as ready immediately.
        out = deterministic_batches([_story("STORY-1", deps=["EXTERNAL-9"])])
        assert out == [{"batch_id": 1, "story_keys": ["STORY-1"]}]

    def test_dependency_cycle_falls_back_to_single_batch(self):
        # A ↔ B cycle. The batcher cannot order them; it must emit a
        # single batch containing both rather than infinite-loop.
        out = deterministic_batches([
            _story("STORY-1", deps=["STORY-2"]),
            _story("STORY-2", deps=["STORY-1"]),
        ])
        assert out == [{"batch_id": 1, "story_keys": ["STORY-1", "STORY-2"]}]

    def test_output_passes_validator(self):
        stories = [
            _story("STORY-1"),
            _story("STORY-2", deps=["STORY-1"]),
            _story("STORY-3", deps=["STORY-1"]),
            _story("STORY-4", deps=["STORY-2", "STORY-3"]),
        ]
        assert validate_batches(stories, deterministic_batches(stories)) == []


# ---------------------------------------------------------------------------
# propose_or_fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestProposeOrFallback:
    async def test_empty_stories_returns_empty(self):
        out, used_llm, budget = await propose_or_fallback([], None, 1.0)
        assert out == []
        assert used_llm is False
        assert budget == 1.0

    async def test_no_gateway_falls_back(self):
        stories = [_story("STORY-1"), _story("STORY-2")]
        out, used_llm, budget = await propose_or_fallback(stories, None, 1.0)
        assert used_llm is False
        assert out == [{"batch_id": 1, "story_keys": ["STORY-1", "STORY-2"]}]
        assert budget == 1.0  # no LLM call, budget untouched

    async def test_zero_budget_falls_back(self):
        gw = _FakeGateway(reply_content="{}")
        stories = [_story("STORY-1")]
        out, used_llm, budget = await propose_or_fallback(stories, gw, 0.0)
        assert used_llm is False
        assert gw.calls == []  # no dispatch happened
        assert out == [{"batch_id": 1, "story_keys": ["STORY-1"]}]
        assert budget == 0.0

    async def test_oversize_input_skips_llm(self):
        stories = [_story(f"STORY-{i}") for i in range(1, MAX_STORIES + 2)]
        gw = _FakeGateway(reply_content="{}")
        out, used_llm, budget = await propose_or_fallback(stories, gw, 1.0)
        assert used_llm is False
        assert gw.calls == []
        # Every input story is in some batch.
        flat = [k for b in out for k in b["story_keys"]]
        assert sorted(flat) == sorted(s["story_key"] for s in stories)
        assert budget == 1.0

    async def test_llm_success_path_used(self):
        stories = [
            _story("STORY-1"),
            _story("STORY-2", deps=["STORY-1"]),
        ]
        gw = _FakeGateway(reply_content=json.dumps({
            "batches": [
                {"batch_id": 1, "story_keys": ["STORY-1"]},
                {"batch_id": 2, "story_keys": ["STORY-2"]},
            ]
        }))
        out, used_llm, budget = await propose_or_fallback(stories, gw, 1.0)
        assert used_llm is True
        assert out == [
            {"batch_id": 1, "story_keys": ["STORY-1"]},
            {"batch_id": 2, "story_keys": ["STORY-2"]},
        ]
        assert budget == pytest.approx(0.99)
        assert len(gw.calls) == 1

    async def test_llm_invalid_json_falls_back(self):
        stories = [_story("STORY-1"), _story("STORY-2")]
        gw = _FakeGateway(reply_content="not json at all")
        out, used_llm, _ = await propose_or_fallback(stories, gw, 1.0)
        assert used_llm is False
        # Deterministic result: both independent → one batch
        assert out == [{"batch_id": 1, "story_keys": ["STORY-1", "STORY-2"]}]
        assert len(gw.calls) == 1

    async def test_llm_schema_violation_falls_back(self):
        # LLM returns batches but misses a story → validator rejects;
        # we should fall back deterministically.
        stories = [_story("STORY-1"), _story("STORY-2")]
        gw = _FakeGateway(reply_content=json.dumps({
            "batches": [{"batch_id": 1, "story_keys": ["STORY-1"]}],
        }))
        out, used_llm, _ = await propose_or_fallback(stories, gw, 1.0)
        assert used_llm is False
        flat = [k for b in out for k in b["story_keys"]]
        assert sorted(flat) == ["STORY-1", "STORY-2"]

    async def test_llm_dispatch_exception_falls_back(self):
        stories = [_story("STORY-1")]
        gw = _FakeGateway(reply_content="", raise_exc=RuntimeError("boom"))
        out, used_llm, budget = await propose_or_fallback(stories, gw, 1.0)
        assert used_llm is False
        assert out == [{"batch_id": 1, "story_keys": ["STORY-1"]}]
        # Budget unchanged because the dispatch raised before charging.
        # (The fake gateway raises before decrementing.)
        assert budget == 1.0

    async def test_llm_fence_wrapped_json_is_tolerated(self):
        stories = [_story("STORY-1")]
        gw = _FakeGateway(reply_content=(
            "```json\n"
            + json.dumps({"batches": [{"batch_id": 1, "story_keys": ["STORY-1"]}]})
            + "\n```"
        ))
        out, used_llm, _ = await propose_or_fallback(stories, gw, 1.0)
        assert used_llm is True
        assert out == [{"batch_id": 1, "story_keys": ["STORY-1"]}]

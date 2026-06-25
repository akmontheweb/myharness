"""Global no-progress failsafe — Layer 3 of the runaway-loop defenses.

Layer 1 (``route_after_patching``) and Layer 2 (``story_loop_node``'s
per-story auto-advance) catch the patching ↔ story_loop tight cycle
that the 2026-06-25 incident hit. This module is the BACKSTOP for the
loops we haven't anticipated yet — anything that burns budget without
moving the modified-files / patch-success needle.

The invariant we want to hold is simple:

    *In no situation should the run keep spending money without
    producing code changes.*

Mechanism:

- Budget-heavy code-generation nodes (``patching_node`` and
  ``repair_node``) call :func:`update_and_check` after each LLM turn,
  passing the new budget remaining and a boolean ``progress_made``
  flag.
- The tracker remembers ``budget_at_last_progress``. On every call
  where ``progress_made=False``, it computes the budget delta since
  the last progress marker; if that delta exceeds
  ``NO_PROGRESS_BUDGET_THRESHOLD_USD`` (default $1.50, overridable
  via ``state['no_progress_budget_usd']``), the tracker is marked
  ``tripped``.
- The existing routers (``route_after_patching``,
  ``route_after_compiler``) check :func:`tripped` early and route to
  ``human_intervention_node`` regardless of which sub-graph is
  looping.

A single round of real progress (``progress_made=True``) retires the
failsafe — the marker is reset to the current budget and the tripped
flag is cleared. This is what lets a normal run that hits one bad
turn recover without operator help.

Stored in ``state['loop_counter']['progress_tracker']`` so it
inherits the existing per-batch reset semantics in
``story_loop.batch_commit_node`` (which scrubs loop_counter between
batches) — a fresh batch always gets a fresh failsafe budget.
"""

from __future__ import annotations

from typing import Any

NO_PROGRESS_BUDGET_THRESHOLD_USD = 1.50
"""Default budget delta (USD) that, when spent without any
``progress_made=True`` call, trips the failsafe. Picked to give the
LLM ~10 retries at typical per-call cost (~$0.015) before escalating —
generous enough to absorb a few stumbles, tight enough that an
unattended overnight run can't drain the whole budget on a stuck
loop. Override per-run via ``state['no_progress_budget_usd']``."""


def update_and_check(
    loop_counter: dict[str, Any],
    budget_remaining_usd: float,
    progress_made: bool,
    threshold_usd: float = NO_PROGRESS_BUDGET_THRESHOLD_USD,
) -> bool:
    """Update the tracker; return True iff the failsafe is tripped now.

    ``progress_made`` is the caller's verdict on whether *this* turn
    produced real progress. Patching nodes pass ``success_count > 0``;
    other nodes can pass any equivalent signal (a story_state row
    flipped to done, a new modified_files entry, etc.).

    Mutates ``loop_counter['progress_tracker']`` in place.
    """
    tracker = dict(loop_counter.get("progress_tracker") or {})

    if progress_made or "budget_at_last_progress" not in tracker:
        # Either we made progress or this is the very first sample.
        # Either way, snapshot the current budget as the new marker
        # and clear any prior tripped flag.
        loop_counter["progress_tracker"] = {
            "budget_at_last_progress": float(budget_remaining_usd),
            "tripped": False,
        }
        return False

    last_budget = float(
        tracker.get("budget_at_last_progress", budget_remaining_usd)
    )
    spent_since = max(0.0, last_budget - float(budget_remaining_usd))
    is_tripped = spent_since >= float(threshold_usd)

    loop_counter["progress_tracker"] = {
        "budget_at_last_progress": last_budget,
        "tripped": is_tripped,
        "spent_since_progress_usd": spent_since,
    }
    return is_tripped


def tripped(loop_counter: dict[str, Any] | None) -> bool:
    """Read-only check used by routers (which can't mutate state).

    Returns False when ``loop_counter`` is missing or the tracker has
    never been initialised — the failsafe stays dormant until a
    code-generation node has had a chance to record the first sample.
    """
    if not isinstance(loop_counter, dict):
        return False
    tracker = loop_counter.get("progress_tracker") or {}
    return bool(tracker.get("tripped"))


def reset(loop_counter: dict[str, Any]) -> None:
    """Discard the tracker. Used by callers that intentionally want to
    re-baseline (e.g. after operator intervention from
    human_intervention_node, when the resumed run should not inherit
    the pre-pause spend without progress)."""
    loop_counter.pop("progress_tracker", None)

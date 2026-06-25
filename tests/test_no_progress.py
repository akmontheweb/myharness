"""Unit tests for harness/no_progress.py — Layer 3 failsafe."""

from __future__ import annotations

from harness import no_progress


def test_tripped_returns_false_on_missing_loop_counter():
    assert no_progress.tripped(None) is False
    assert no_progress.tripped({}) is False


def test_first_sample_initialises_marker_without_tripping():
    lc: dict = {}
    tripped = no_progress.update_and_check(
        lc, budget_remaining_usd=10.00, progress_made=False,
    )
    assert tripped is False
    assert lc["progress_tracker"]["budget_at_last_progress"] == 10.00
    assert lc["progress_tracker"]["tripped"] is False


def test_progress_resets_marker_to_current_budget():
    lc: dict = {
        "progress_tracker": {
            "budget_at_last_progress": 10.00, "tripped": True,
        }
    }
    tripped = no_progress.update_and_check(
        lc, budget_remaining_usd=8.00, progress_made=True,
    )
    assert tripped is False
    assert lc["progress_tracker"]["budget_at_last_progress"] == 8.00
    assert lc["progress_tracker"]["tripped"] is False


def test_no_progress_below_threshold_does_not_trip():
    lc: dict = {
        "progress_tracker": {
            "budget_at_last_progress": 10.00, "tripped": False,
        }
    }
    # Spent only $1.00 — under the $1.50 default threshold.
    tripped = no_progress.update_and_check(
        lc, budget_remaining_usd=9.00, progress_made=False,
    )
    assert tripped is False
    assert lc["progress_tracker"]["tripped"] is False


def test_no_progress_above_threshold_trips():
    lc: dict = {
        "progress_tracker": {
            "budget_at_last_progress": 10.00, "tripped": False,
        }
    }
    # Spent $1.50 with no progress → trip.
    tripped = no_progress.update_and_check(
        lc, budget_remaining_usd=8.50, progress_made=False,
    )
    assert tripped is True
    assert lc["progress_tracker"]["tripped"] is True
    assert no_progress.tripped(lc) is True


def test_custom_threshold_respected():
    lc: dict = {
        "progress_tracker": {
            "budget_at_last_progress": 10.00, "tripped": False,
        }
    }
    # Spent $0.50 — under the $1.00 custom threshold.
    tripped = no_progress.update_and_check(
        lc, budget_remaining_usd=9.50, progress_made=False, threshold_usd=1.00,
    )
    assert tripped is False
    # Spent $1.00 — at the custom threshold → trip.
    tripped = no_progress.update_and_check(
        lc, budget_remaining_usd=9.00, progress_made=False, threshold_usd=1.00,
    )
    assert tripped is True


def test_reset_clears_tracker():
    lc: dict = {
        "progress_tracker": {
            "budget_at_last_progress": 10.00, "tripped": True,
        }
    }
    no_progress.reset(lc)
    assert "progress_tracker" not in lc
    assert no_progress.tripped(lc) is False


def test_marker_persists_across_no_progress_calls():
    """The marker must stick across multiple no-progress calls so the
    spend-since calculation grows monotonically until it crosses the
    threshold — it must NOT silently re-baseline to each call's budget."""
    lc: dict = {}
    # Bootstrap the marker at $10.
    no_progress.update_and_check(lc, budget_remaining_usd=10.00, progress_made=False)
    # Three no-progress turns at $0.60 each — cumulatively $1.80, above
    # the $1.50 default threshold, so it must trip by the third call.
    assert no_progress.update_and_check(lc, 9.40, progress_made=False) is False  # spent 0.60
    assert no_progress.update_and_check(lc, 8.80, progress_made=False) is False  # spent 1.20
    assert no_progress.update_and_check(lc, 8.20, progress_made=False) is True   # spent 1.80


def test_single_progress_round_retires_a_tripped_failsafe():
    """The retirement semantics: one successful patch round always
    resets the failsafe even if it was previously tripped. This is what
    lets a stuck-then-unstuck run continue without operator help."""
    lc: dict = {}
    no_progress.update_and_check(lc, budget_remaining_usd=10.00, progress_made=False)
    no_progress.update_and_check(lc, budget_remaining_usd=8.00, progress_made=False)
    assert no_progress.tripped(lc) is True

    no_progress.update_and_check(lc, budget_remaining_usd=7.50, progress_made=True)
    assert no_progress.tripped(lc) is False
    assert lc["progress_tracker"]["budget_at_last_progress"] == 7.50

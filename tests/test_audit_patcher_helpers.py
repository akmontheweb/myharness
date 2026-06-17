"""Tests for patcher audit hardening (batch 9).

Covers:
  - _strip_line_number_prefixes requires contiguous numeric run     (§6.11)
"""

from __future__ import annotations


from harness.patcher import _strip_line_number_prefixes


# ---------------------------------------------------------------------------
# Contiguous numeric run requirement (audit §6.11)
# ---------------------------------------------------------------------------


def test_strip_returns_none_when_no_prefixes():
    """Plain code without any ``N|`` prefixes returns None — nothing to strip."""
    assert _strip_line_number_prefixes("def foo():\n    return 1\n") is None


def test_strip_returns_none_on_non_contiguous_numbers():
    """When the prefix numbers don't form a contiguous run, the helper
    returns None — guards against coincidental matches on content that
    happens to be pipe-separated integers."""
    search = "  3| line three\n  7| line seven\n"
    assert _strip_line_number_prefixes(search) is None


def test_strip_works_on_contiguous_run():
    search = "  3| line three\n  4| line four\n  5| line five\n"
    out = _strip_line_number_prefixes(search)
    assert out is not None
    assert "line three" in out
    assert "line four" in out
    assert "line five" in out
    # The prefixes are gone.
    assert "3|" not in out
    assert "4|" not in out


def test_strip_single_line_works():
    """A single numbered line is trivially contiguous."""
    search = "  42| only line\n"
    out = _strip_line_number_prefixes(search)
    assert out is not None
    assert "only line" in out


def test_strip_returns_none_when_any_line_missing_prefix():
    """If ANY non-blank line lacks the prefix, nothing strips."""
    search = "  3| line three\nplain content\n  4| line four\n"
    assert _strip_line_number_prefixes(search) is None


def test_strip_preserves_blank_lines():
    search = "  1| first\n\n  2| second\n"
    out = _strip_line_number_prefixes(search)
    assert out is not None
    assert "first" in out
    assert "second" in out

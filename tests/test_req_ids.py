"""Tests for ``harness/req_ids.py`` — the shared regex + parser
behind the v5 requirements ingest.

Two heading families coexist:

- Waterfall / ISO 29148: ``FR-NNN``, ``NFR-XXX-NNN``, ``US-NN-NN``
- Agile / SAFe (Phase 8): ``EPIC-NNN``, ``FEAT-NNN``, ``STORY-NNN``,
  ``STORY-NFR-NNN``

Phase 8 added the SAFe family so the agile-mode spec produced by
``requirements_doc.md`` Path A is parseable by the same ingest the
waterfall flow uses.
"""

from __future__ import annotations

from harness.req_ids import (
    EPIC_ID_RE,
    FEAT_ID_RE,
    FR_ID_RE,
    NFR_ID_RE,
    STORY_ID_RE,
    STORY_NFR_ID_RE,
    US_ID_RE,
    kind_for,
    parse_spec_requirements,
)


# ---------------------------------------------------------------------------
# kind_for — every family round-trips through the dispatch table
# ---------------------------------------------------------------------------

class TestKindFor:

    def test_waterfall_families(self):
        assert kind_for("FR-001") == "fr"
        assert kind_for("FR-9999") == "fr"
        assert kind_for("NFR-SEC-001") == "nfr"
        assert kind_for("NFR-PERF-014") == "nfr"
        assert kind_for("US-01-02") == "us"

    def test_agile_safe_families(self):
        assert kind_for("EPIC-001") == "epic"
        assert kind_for("FEAT-014") == "feat"
        assert kind_for("STORY-101") == "safe_story"
        assert kind_for("STORY-0001") == "safe_story"

    def test_safe_nfr_story_wins_over_story_prefix(self):
        """STORY-NFR-NNN is a strict prefix superset of STORY-NNN —
        the NFR family must be checked first."""
        assert kind_for("STORY-NFR-001") == "safe_nfr_story"
        assert kind_for("STORY-NFR-014") == "safe_nfr_story"

    def test_v5_internal_story_keys_not_matched(self):
        """v5's internal ``STORY-N`` (no padding) MUST NOT match
        the SAFe ``STORY-NNN`` regex (3+ digits required). Otherwise
        v5 story_keys would silently become "requirements"."""
        assert kind_for("STORY-1") is None
        assert kind_for("STORY-9") is None
        assert kind_for("STORY-99") is None

    def test_v5_ac_keys_not_matched(self):
        """``STORY-3.AC-2`` is the v5 AC marker form — the SAFe
        regex must not accept it as a story."""
        assert kind_for("STORY-3.AC-2") is None
        assert kind_for("STORY-001.AC-1") is None

    def test_unknown_returns_none(self):
        assert kind_for("CR-7") is None
        assert kind_for("BOGUS-123") is None
        assert kind_for("FR-") is None
        assert kind_for("") is None


# ---------------------------------------------------------------------------
# parse_spec_requirements — both heading shapes
# ---------------------------------------------------------------------------

class TestParseWaterfall:
    """Flat ``### FR-NNN: Title`` heading shape — the form
    ``docs/SPEC_REQUIREMENTS.md`` in this repo uses today."""

    SPEC = (
        "# Product spec\n\n"
        "Some preamble.\n\n"
        "### FR-001: Login\n"
        "User can log in.\n\n"
        "### FR-002: Logout\n"
        "User can log out and the session ends.\n\n"
        "#### NFR-SEC-001: Token storage\n"
        "Session tokens MUST be hashed at rest.\n\n"
        "### US-03-02: Reset confirmation screen\n"
        "User sees a confirmation page after reset.\n"
    )

    def test_parses_all_four_headings(self):
        rows = parse_spec_requirements(self.SPEC)
        assert [r.req_key for r in rows] == [
            "FR-001", "FR-002", "NFR-SEC-001", "US-03-02",
        ]

    def test_kinds_assigned_correctly(self):
        rows = parse_spec_requirements(self.SPEC)
        kinds = {r.req_key: r.kind for r in rows}
        assert kinds["FR-001"] == "fr"
        assert kinds["NFR-SEC-001"] == "nfr"
        assert kinds["US-03-02"] == "us"

    def test_body_captured_until_next_heading(self):
        rows = parse_spec_requirements(self.SPEC)
        by_key = {r.req_key: r for r in rows}
        assert by_key["FR-001"].body == "User can log in."
        assert by_key["NFR-SEC-001"].body == (
            "Session tokens MUST be hashed at rest."
        )

    def test_source_line_one_indexed(self):
        rows = parse_spec_requirements(self.SPEC)
        # FR-001 is on line 5 (1-indexed) of self.SPEC.
        assert rows[0].source_line == 5


class TestParseAgileSAFe:
    """SAFe ``## Epic: EPIC-NNN — Title`` heading shape — emitted by
    Path A of ``harness/skills/docgen/requirements_doc.md``."""

    SPEC = (
        "# Product spec\n\n"
        "## Epic: EPIC-001 — Authentication\n"
        "All user-identity capabilities.\n\n"
        "### Feature: FEAT-014 — Password reset\n"
        "Operator can reset password via email.\n\n"
        "#### Story: STORY-101 — Operator clicks reset link\n"
        "Confirms via email link, sets new password.\n\n"
        "#### Enabler Story: STORY-NFR-001 — TLS 1.3 minimum\n"
        "All endpoints terminate TLS 1.3+.\n"
    )

    def test_parses_all_safe_headings(self):
        rows = parse_spec_requirements(self.SPEC)
        assert [r.req_key for r in rows] == [
            "EPIC-001", "FEAT-014", "STORY-101", "STORY-NFR-001",
        ]

    def test_kinds_assigned_correctly(self):
        rows = parse_spec_requirements(self.SPEC)
        kinds = {r.req_key: r.kind for r in rows}
        assert kinds["EPIC-001"] == "epic"
        assert kinds["FEAT-014"] == "feat"
        assert kinds["STORY-101"] == "safe_story"
        assert kinds["STORY-NFR-001"] == "safe_nfr_story"

    def test_titles_captured_without_label_prefix(self):
        rows = parse_spec_requirements(self.SPEC)
        by_key = {r.req_key: r for r in rows}
        # The "Epic: " / "Feature: " / "Story: " / "Enabler Story: "
        # label prefix must be stripped from the captured title.
        assert by_key["EPIC-001"].title == "Authentication"
        assert by_key["FEAT-014"].title == "Password reset"
        assert by_key["STORY-101"].title == "Operator clicks reset link"
        assert by_key["STORY-NFR-001"].title == "TLS 1.3 minimum"

    def test_body_captured(self):
        rows = parse_spec_requirements(self.SPEC)
        by_key = {r.req_key: r for r in rows}
        assert by_key["FEAT-014"].body == (
            "Operator can reset password via email."
        )


class TestMixedSpec:
    """Specs in the wild may mix shapes — operator manually edited a
    SAFe spec to add a flat FR row, etc. The parser must accept the
    union."""

    def test_mixed_safe_and_waterfall(self):
        spec = (
            "## Epic: EPIC-001 — Auth\n"
            "Epic body.\n\n"
            "### Feature: FEAT-001 — Login\n"
            "Feature body.\n\n"
            "### FR-001: Legacy flat FR\n"
            "Operator added this by hand.\n"
        )
        rows = parse_spec_requirements(spec)
        assert [r.req_key for r in rows] == [
            "EPIC-001", "FEAT-001", "FR-001",
        ]
        assert [r.kind for r in rows] == ["epic", "feat", "fr"]


# ---------------------------------------------------------------------------
# Individual regex sanity (used by traceability.py and ingest separately)
# ---------------------------------------------------------------------------

class TestRegexSanity:

    def test_story_id_re_requires_three_plus_digits(self):
        assert STORY_ID_RE.search("STORY-001")
        assert STORY_ID_RE.search("STORY-100")
        # v5 internal keys with 1-2 digits must NOT match.
        assert not STORY_ID_RE.search("STORY-1 ")
        assert not STORY_ID_RE.search("STORY-9")
        assert not STORY_ID_RE.search("STORY-99 ")

    def test_epic_and_feat_accept_1_4_digits(self):
        assert EPIC_ID_RE.search("EPIC-1")
        assert EPIC_ID_RE.search("EPIC-9999")
        assert FEAT_ID_RE.search("FEAT-1")
        assert FEAT_ID_RE.search("FEAT-9999")

    def test_safe_nfr_story_matches(self):
        assert STORY_NFR_ID_RE.search("STORY-NFR-001")
        assert STORY_NFR_ID_RE.search("STORY-NFR-14")

    def test_word_boundaries_avoid_substring_matches(self):
        # ``USER-1234`` shouldn't be mistaken for ``FR-1234`` or similar.
        assert not FR_ID_RE.search("USER-1234")
        assert not NFR_ID_RE.search("ANFR-SEC-001")
        # ``US-`` is a real prefix, so this one IS expected to match.
        assert US_ID_RE.search("US-01-02")

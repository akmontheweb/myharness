"""Tests for the FR-XXX traceability audit."""
from __future__ import annotations

import os

from harness.traceability import audit_workspace, format_report


def _w(tmp_path, rel: str, body: str) -> None:
    abs_path = os.path.join(str(tmp_path), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(body)


def test_no_spec_returns_none(tmp_path):
    assert audit_workspace(str(tmp_path)) is None


def test_empty_spec_yields_full_coverage(tmp_path):
    _w(tmp_path, "docs/SPEC_REQUIREMENTS.md", "# Empty spec\n\nNothing here.\n")
    report = audit_workspace(str(tmp_path))
    assert report is not None
    assert report.total_ids == 0
    assert report.coverage_pct == 100.0
    assert report.untraced == []


def test_fully_traced_spec(tmp_path):
    _w(tmp_path, "docs/SPEC_REQUIREMENTS.md",
       "# Spec\n\n## FR-001 Login\n\n## FR-002 Logout\n")
    _w(tmp_path, "src/auth.py",
       "# Implements FR-001 and FR-002.\n"
       "def login(): pass\n"
       "def logout(): pass\n")
    report = audit_workspace(str(tmp_path))
    assert report is not None
    assert report.total_ids == 2
    assert report.traced_ids == 2
    assert report.untraced == []


def test_untraced_fr_surfaces(tmp_path):
    _w(tmp_path, "docs/SPEC_REQUIREMENTS.md",
       "# Spec\n\n## FR-001\n## FR-002\n## FR-007 Email password reset\n")
    _w(tmp_path, "src/auth.py",
       "# Implements FR-001 and FR-002.\ndef login(): pass\n")
    report = audit_workspace(str(tmp_path))
    assert report is not None
    assert report.total_ids == 3
    assert report.traced_ids == 2
    assert len(report.untraced) == 1
    assert report.untraced[0].req_id == "FR-007"
    assert report.untraced[0].kind == "fr"


def test_us_and_nfr_identifiers_picked_up(tmp_path):
    _w(tmp_path, "docs/SPEC_REQUIREMENTS.md",
       "## US-01-02 Login flow\n"
       "## NFR-SEC-001 TLS 1.3 minimum\n")
    _w(tmp_path, "tests/test_auth.py", "# Covers US-01-02 happy path.\n")
    report = audit_workspace(str(tmp_path))
    assert report is not None
    # US-01-02 traced via test file; NFR-SEC-001 not mentioned anywhere.
    untraced_ids = {u.req_id for u in report.untraced}
    assert untraced_ids == {"NFR-SEC-001"}


def test_spec_self_reference_does_not_count_as_trace(tmp_path):
    # If FR-001 only appears in the spec itself (and nowhere else), it
    # should still be flagged as untraced.
    _w(tmp_path, "docs/SPEC_REQUIREMENTS.md",
       "## FR-001 Some requirement\n\n"
       "This requirement is FR-001 and we keep saying FR-001 here.\n")
    report = audit_workspace(str(tmp_path))
    assert report is not None
    assert report.total_ids == 1
    assert report.traced_ids == 0
    assert report.untraced[0].req_id == "FR-001"


def test_skips_never_source_dirs(tmp_path):
    # If the only mention of FR-001 is inside node_modules, it must
    # NOT count as a trace — codegen artefacts there don't reflect
    # the project's actual implementation.
    _w(tmp_path, "docs/SPEC_REQUIREMENTS.md", "## FR-001\n")
    _w(tmp_path, "node_modules/some-pkg/README.md",
       "This package mentions FR-001 for unrelated reasons.\n")
    report = audit_workspace(str(tmp_path))
    assert report is not None
    assert report.untraced[0].req_id == "FR-001"


def test_format_report_empty_when_full_coverage(tmp_path):
    _w(tmp_path, "docs/SPEC_REQUIREMENTS.md", "## FR-001\n")
    _w(tmp_path, "src/main.py", "# FR-001\n")
    report = audit_workspace(str(tmp_path))
    assert format_report(report) == ""


def test_format_report_groups_by_kind(tmp_path):
    _w(tmp_path, "docs/SPEC_REQUIREMENTS.md",
       "## FR-001\n## FR-002\n## US-01-01\n## NFR-PERF-001\n")
    # Nothing referenced anywhere else.
    _w(tmp_path, "src/empty.py", "# unrelated\n")
    report = audit_workspace(str(tmp_path))
    text = format_report(report)
    assert "Functional Requirements" in text
    assert "User Stories" in text
    assert "Non-Functional Requirements" in text
    assert "FR-001" in text and "FR-002" in text
    assert "US-01-01" in text
    assert "NFR-PERF-001" in text

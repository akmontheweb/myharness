"""Shared regexes + parser for requirement identifiers.

teane recognises seven identifier families in ``docs/SPEC_REQUIREMENTS.md``.
The first three are the **waterfall / ISO 29148** vocabulary the flat
spec path emits; the last four are the **agile / SAFe** vocabulary the
agile spec path emits.

Waterfall (flat FR list):

- ``FR-NNN`` — functional requirements (``FR-007``)
- ``NFR-XXX-NNN`` — non-functional requirements grouped by category
  (``NFR-SEC-001``, ``NFR-PERF-014``)
- ``US-NN-NN`` — user stories from a discovery doc that uses the
  hyphenated form (``US-03-02``)

Agile / SAFe (Epic → Feature → Story hierarchy emitted by the
``requirements_doc.md`` Path A skill):

- ``EPIC-NNN`` — epic-level requirement (``EPIC-001``)
- ``FEAT-NNN`` — feature-level requirement (``FEAT-014``)
- ``STORY-NNN`` — story-level requirement, 3+ digits to avoid
  collisions with v5's internal ``STORY-N`` work-unit keys
  (``STORY-001`` is a spec requirement; ``STORY-1`` is a v5 row).
- ``STORY-NFR-NNN`` — agile "enabler story" for non-functional work
  (``STORY-NFR-001``)

Both the v5 ``requirements_ingest`` (parses headings into rows in the
``requirements`` table) and the v5 SQL traceability audit
(``harness/traceability.py``) share these regexes — they used to live
only in ``traceability.py`` for the text-grep audit; lifting them
here keeps a single source of truth as the audit migrates to
DB-backed queries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Identifier patterns. Anchored on word boundaries so plain text
# containing the token gets matched without picking up sub-strings
# inside identifiers like ``USER-1234``.
#
# Waterfall family:
FR_ID_RE = re.compile(r"\bFR-\d{1,4}\b")
US_ID_RE = re.compile(r"\bUS-\d{1,3}-\d{1,3}\b")
NFR_ID_RE = re.compile(r"\bNFR-[A-Z]+-\d{1,4}\b")

# Agile / SAFe family. STORY_ID_RE requires 3+ digits so SAFe
# requirement IDs (``STORY-001``) never collide with v5 internal
# story_keys (``STORY-1``, ``STORY-2``, …) in heading parses. The
# STORY_NFR_ID_RE check must run BEFORE STORY_ID_RE (``\b`` matches
# the dash, so ``STORY-001`` is a substring of ``STORY-NFR-001``).
EPIC_ID_RE = re.compile(r"\bEPIC-\d{1,4}\b")
FEAT_ID_RE = re.compile(r"\bFEAT-\d{1,4}\b")
STORY_ID_RE = re.compile(r"\bSTORY-\d{3,4}\b")
STORY_NFR_ID_RE = re.compile(r"\bSTORY-NFR-\d{1,4}\b")

# Heading patterns the ingest parser looks for. Convention matches what
# the existing decomposition LLM emits and what the agile / waterfall
# spec skills produce, e.g.:
#
#   ### FR-007: One-line title                    (waterfall)
#   #### NFR-SEC-001: Encrypt session tokens at rest
#   ## Epic: EPIC-001 — Authentication            (SAFe epic)
#   ### Feature: FEAT-014 — Password reset        (SAFe feature)
#   #### Story: STORY-101 — Operator can reset    (SAFe story)
#   #### Enabler Story: STORY-NFR-001 — TLS ≥ 1.3 (SAFe NFR story)
#
# Two or more ``#``, an optional label word + dash/colon, then the id
# token, then ``:``/``—``/`` -`` + title. Anything after the title goes
# into ``body`` (captured until the next heading by
# ``parse_spec_requirements`` — see below).
#
# Order in the alternation matters: STORY-NFR-NNN before STORY-NNN
# because the former is a strict superset prefix.
_HEADING_RE = re.compile(
    r"^\s*#{2,}\s+"
    # Optional label prefix (e.g. ``Epic:``, ``Feature:``, ``Story:``,
    # ``Enabler Story:``) — matched permissively and discarded.
    r"(?:[A-Za-z][A-Za-z ]{0,30}:\s+)?"
    r"(?P<id>"
    r"FR-\d{1,4}"
    r"|NFR-[A-Z]+-\d{1,4}"
    r"|US-\d{1,3}-\d{1,3}"
    r"|EPIC-\d{1,4}"
    r"|FEAT-\d{1,4}"
    r"|STORY-NFR-\d{1,4}"
    r"|STORY-\d{3,4}"
    r")"
    # Title separator: ``:``, em-dash, or `` -`` (markdown header style).
    r"\s*(?:[:\-]|—)\s*(?P<title>.+?)\s*$"
)


# Terminators that close out the body of the current requirement. Any
# ``#``-prefixed heading qualifies (a new requirement OR a section
# header), as does a horizontal rule.
_BODY_TERMINATOR_RE = re.compile(r"^\s*(?:#{1,}\s|---\s*$)")


def kind_for(req_key: str) -> Optional[str]:
    """Return the ``kind`` string for a given requirement id, or
    ``None`` when the token doesn't match any known family.

    Returns one of: ``fr``, ``nfr``, ``us``, ``epic``, ``feat``,
    ``safe_story``, ``safe_nfr_story``. Used by
    ``requirements_ingest`` to set the ``requirements.kind`` column
    without re-running every regex.

    Order matters: the SAFe NFR-story check must fire before the SAFe
    story check, since the former is a strict prefix superset.
    """
    if FR_ID_RE.fullmatch(req_key):
        return "fr"
    if NFR_ID_RE.fullmatch(req_key):
        return "nfr"
    if US_ID_RE.fullmatch(req_key):
        return "us"
    if EPIC_ID_RE.fullmatch(req_key):
        return "epic"
    if FEAT_ID_RE.fullmatch(req_key):
        return "feat"
    if STORY_NFR_ID_RE.fullmatch(req_key):
        return "safe_nfr_story"
    if STORY_ID_RE.fullmatch(req_key):
        return "safe_story"
    return None


@dataclass(frozen=True)
class ParsedRequirement:
    """One requirement row scraped from a spec file.

    ``source_line`` is 1-indexed (matches editor line numbers and the
    convention git/grep use). ``body`` may be empty when the heading
    has no following prose before the next terminator.
    """
    req_key: str
    kind: str
    title: str
    body: str
    source_line: int


def parse_spec_requirements(
    text: str,
) -> list[ParsedRequirement]:
    """Walk ``text`` and yield one :class:`ParsedRequirement` per
    heading that matches the FR/NFR/US convention.

    The body is the lines between the current heading and the next
    heading or horizontal rule (``---``). Leading/trailing blank
    lines are trimmed; internal whitespace is preserved verbatim so
    snippets like fenced code blocks survive intact.

    Duplicate ``req_key`` headings are NOT deduplicated here — caller
    (``requirements_ingest``) relies on the DB's ``ON CONFLICT
    DO UPDATE`` to UPSERT, so a late heading wins. This matches the
    "spec edits propagate" contract documented in
    ``harness/story_state.py:create_requirements``.
    """
    out: list[ParsedRequirement] = []
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        m = _HEADING_RE.match(line)
        if not m:
            i += 1
            continue
        req_key = m.group("id")
        kind = kind_for(req_key)
        if kind is None:
            # Shouldn't happen given the regex above, but defensive:
            # an id that matches no kind is skipped rather than crashing.
            i += 1
            continue
        title = m.group("title").strip()
        body_start = i + 1
        j = body_start
        while j < n and not _BODY_TERMINATOR_RE.match(lines[j]):
            j += 1
        body_lines = lines[body_start:j]
        # Strip leading/trailing blank lines but keep internal layout.
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        out.append(ParsedRequirement(
            req_key=req_key,
            kind=kind,
            title=title,
            body="\n".join(body_lines),
            source_line=i + 1,  # 1-indexed
        ))
        i = j
    return out

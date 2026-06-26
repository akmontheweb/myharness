"""
FR-XXX traceability audit.

After codegen completes, scans ``docs/SPEC_REQUIREMENTS.md`` for every
``FR-NNN`` and ``US-NNN-NN`` identifier and verifies each appears
somewhere in the workspace's source or test tree. IDs with zero
mentions are reported as **un-traced** — likely a story or functional
requirement that codegen silently dropped.

This catches the failure mode where:

  1. The requirements doc enumerates 14 functional requirements.
  2. Codegen produces 11 modules implementing 9 of them.
  3. The build passes, tests pass.
  4. Nobody notices that FR-007 ("Email password reset") was never
     implemented until a user reports it from prod.

It is **advisory** — does not block the run, just emits a structured
report. Wiring it as a blocking gate is a future improvement; the
caller decides what to do with the warnings.

Coverage rules:

  - An ID is "traced" if its literal token (``FR-007`` or ``US-03-02``)
    appears in ANY of: a source comment, a docstring, a test name, or
    a markdown file other than the spec itself.
  - Case is preserved (FR is upper-case in the spec by convention).
  - Source-tree scan honours ``_NEVER_SOURCE_DIRS`` so node_modules,
    .venv, dist, etc. are skipped.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


_NEVER_SOURCE_DIRS: frozenset[str] = frozenset({
    "node_modules", "__pycache__", "dist", "build", "target", "out",
    ".git", ".tox", ".venv", "venv",
    "coverage", "htmlcov", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".harness", ".teane",
})

# Source extensions we scan for ID references. Code, test, and docs only —
# binary files are skipped.
_SOURCE_EXTS: tuple[str, ...] = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java",
    ".md", ".rst", ".txt", ".sql", ".yaml", ".yml", ".toml",
)

# Default IDs to audit. FR-001 .. FR-999 and US-01-02 style.
_FR_ID_RE = re.compile(r"\bFR-\d{1,4}\b")
_US_ID_RE = re.compile(r"\bUS-\d{1,3}-\d{1,3}\b")
_NFR_ID_RE = re.compile(r"\bNFR-[A-Z]+-\d{1,4}\b")

# Cap on the number of files we scan to keep this O(workspace_size) but
# bounded. A 5000-file workspace at ~50KB/file would dominate the run
# otherwise; truncating to 2000 source files covers every realistic case.
_SCAN_FILE_CAP = 2000


@dataclass(frozen=True)
class UntracedRequirement:
    """A requirement ID found in the spec but not anywhere else.

    Attributes:
        req_id: The literal identifier (``FR-007``, ``US-03-02``,
            ``NFR-SEC-001``).
        kind: ``"fr"``, ``"us"``, or ``"nfr"`` — for downstream
            grouping in the report.
    """
    req_id: str
    kind: str


@dataclass(frozen=True)
class TraceabilityReport:
    """Aggregate result of one traceability audit pass.

    Attributes:
        spec_path: Workspace-relative path of the spec that was scanned.
        total_ids: How many distinct requirement IDs the spec declared.
        traced_ids: Subset whose literal token appears in at least one
            source/test/doc file outside the spec.
        untraced: List of :class:`UntracedRequirement` records — these
            are the audit findings the report should surface.
    """
    spec_path: str
    total_ids: int
    traced_ids: int
    untraced: list[UntracedRequirement]

    @property
    def coverage_pct(self) -> float:
        """Percentage of declared IDs that have at least one trace.

        Returns ``100.0`` when the spec declared zero IDs — vacuously
        complete; the operator's spec doesn't use the FR convention or
        the doc is empty.
        """
        if self.total_ids == 0:
            return 100.0
        return 100.0 * self.traced_ids / self.total_ids


def audit_workspace(
    workspace_path: str,
    *,
    spec_relpath: str = "docs/SPEC_REQUIREMENTS.md",
) -> Optional[TraceabilityReport]:
    """Audit a workspace for requirement-ID traceability.

    Args:
        workspace_path: Project root.
        spec_relpath: Relative path of the requirements spec to scan.
            Defaults to the conventional ``docs/SPEC_REQUIREMENTS.md``.

    Returns:
        A :class:`TraceabilityReport`, or ``None`` when the spec file
        doesn't exist (no audit applicable). Empty workspaces and specs
        that declare zero IDs return a report with ``total_ids=0`` and
        ``coverage_pct=100.0``.
    """
    if not workspace_path or not os.path.isdir(workspace_path):
        return None
    spec_abs = os.path.join(workspace_path, spec_relpath)
    if not os.path.isfile(spec_abs):
        logger.debug("[traceability] No spec at %s; skipping audit.", spec_abs)
        return None

    declared = _extract_declared_ids(spec_abs)
    if not declared:
        return TraceabilityReport(
            spec_path=spec_relpath,
            total_ids=0,
            traced_ids=0,
            untraced=[],
        )

    found_tokens = _collect_id_references(
        workspace_path,
        skip_files=frozenset({os.path.abspath(spec_abs)}),
        target_tokens=frozenset(req_id for req_id, _ in declared),
    )

    untraced = [
        UntracedRequirement(req_id=req_id, kind=kind)
        for req_id, kind in declared
        if req_id not in found_tokens
    ]
    return TraceabilityReport(
        spec_path=spec_relpath,
        total_ids=len(declared),
        traced_ids=len(declared) - len(untraced),
        untraced=untraced,
    )


def format_report(report: TraceabilityReport) -> str:
    """Render the report as a human-readable Markdown block.

    Empty when coverage is 100% — saves the operator from a noisy
    "everything is fine" line in the end-of-session output.
    """
    if not report.untraced:
        return ""
    lines: list[str] = [
        f"## Requirement Traceability Audit ({report.spec_path})",
        (
            f"{report.traced_ids}/{report.total_ids} requirement IDs "
            f"have at least one mention in source/test/doc files "
            f"({report.coverage_pct:.0f}% coverage). The following "
            f"{len(report.untraced)} ID(s) declared in the spec do NOT "
            f"appear anywhere in the workspace — likely silently dropped "
            f"by codegen. Verify each before considering the session "
            f"complete:"
        ),
    ]
    by_kind: dict[str, list[str]] = {}
    for item in report.untraced:
        by_kind.setdefault(item.kind, []).append(item.req_id)
    for kind in ("fr", "us", "nfr"):
        ids = by_kind.get(kind)
        if not ids:
            continue
        label = {
            "fr": "Functional Requirements",
            "us": "User Stories",
            "nfr": "Non-Functional Requirements",
        }[kind]
        lines.append(f"\n### {label}")
        for req_id in sorted(ids):
            lines.append(f"- `{req_id}` — not referenced in any source/test/doc file.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_declared_ids(spec_abs: str) -> list[tuple[str, str]]:
    """Scan the spec markdown for all FR / US / NFR identifiers.

    Returns a list of ``(req_id, kind)`` tuples preserving first-seen
    order. Duplicates within the spec are collapsed — the spec naturally
    references each ID multiple times (in the body, in the traceability
    table, etc.).
    """
    try:
        with open(spec_abs, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
    except OSError as exc:
        logger.warning("[traceability] Could not read spec %s: %s", spec_abs, exc)
        return []

    seen: dict[str, str] = {}
    for kind, pattern in (("fr", _FR_ID_RE), ("us", _US_ID_RE), ("nfr", _NFR_ID_RE)):
        for match in pattern.finditer(body):
            token = match.group(0)
            if token not in seen:
                seen[token] = kind
    return list(seen.items())


def _collect_id_references(
    workspace_path: str,
    *,
    skip_files: frozenset[str],
    target_tokens: frozenset[str],
) -> set[str]:
    """Walk the workspace and return which of ``target_tokens`` were
    referenced in source/test/doc files.

    Reads each candidate file at most once and short-circuits once every
    target token has been found at least once — large workspaces where
    the spec is heavily traceable stay fast.
    """
    found: set[str] = set()
    if not target_tokens:
        return found
    files_scanned = 0
    try:
        for sub_root, sub_dirs, sub_files in os.walk(workspace_path):
            sub_dirs[:] = [
                d for d in sub_dirs
                if not d.startswith(".") and d not in _NEVER_SOURCE_DIRS
            ]
            for fname in sub_files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in _SOURCE_EXTS:
                    continue
                abs_path = os.path.join(sub_root, fname)
                if os.path.abspath(abs_path) in skip_files:
                    continue
                files_scanned += 1
                if files_scanned > _SCAN_FILE_CAP:
                    logger.info(
                        "[traceability] File scan cap reached (%d); "
                        "remaining files skipped. Audit reports a lower "
                        "bound on coverage.",
                        _SCAN_FILE_CAP,
                    )
                    return found
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        body = f.read()
                except OSError:
                    continue
                for token in target_tokens - found:
                    if token in body:
                        found.add(token)
                if found == target_tokens:
                    return found
    except OSError:
        return found
    return found

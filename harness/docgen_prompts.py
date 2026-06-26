"""
Loader for documentation-generation system prompts.

The discovery nodes in ``harness.graph`` (``requirements_discovery_node``,
``architecture_discovery_node``) and the standalone CLI doc-gen skills in
``harness.skills`` (``_DOCGEN_SYSTEM_PROMPTS``) used to ship as inline
Python triple-quoted strings. That made the prompts hard to iterate on without
touching code, and the same checklist had to be duplicated whenever a new
sector (features, abuse cases, threat model, failure modes) needed to be
captured.

This module externalizes those prompts as markdown files under
``harness/skills/docgen/`` and exposes a tiny cached loader. Each file's
*entire* body — including the canonical JSON output schema for the
discovery prompts — is the source of truth. The harness reads the file
verbatim at request time; iterating the prompt means editing the .md file.

Layout::

    harness/skills/docgen/<name>.md

Names currently shipped:
    - requirements_discovery           — first-pass requirements discovery
    - requirements_discovery_followup  — follow-up rounds
    - architecture_discovery           — first-pass architecture discovery
    - architecture_discovery_followup  — follow-up rounds
    - requirements_doc                 — standalone Markdown spec doc
    - arch_doc                         — standalone Markdown ADR

Missing files raise ``FileNotFoundError`` rather than falling back to a
truncated default — the prompts are mission-critical (the discovery JSON
parser hard-requires the canonical ``modules`` key) so a silent fallback
to a stub would produce empty interview screens.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)


HARNESS_DOCGEN_DIR = os.path.join(os.path.dirname(__file__), "skills", "docgen")

_CACHE: dict[str, str] = {}
_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Agile-mode directive substitution for requirements_doc.md
# ---------------------------------------------------------------------------
#
# The shipped ``requirements_doc.md`` skill prompt contains a single
# ``{AGILE_MODE_DIRECTIVE}`` placeholder near the top that selects between
# Path A (SAFe/Agile RSD with Gherkin + INVEST) and Path B (ISO 29148
# default RSD). The harness substitutes one of these two banners based on
# the resolved ``--agile`` CLI flag (``args.decomposition_enabled`` after
# ``_resolve_agile_args`` runs). Substitution happens at load time via
# :func:`apply_agile_directive` so the cache returns the verbatim file
# body untouched — call sites apply the directive themselves.
_AGILE_MODE_DIRECTIVE_AGILE = (
    "**EXECUTION MODE: AGILE.** The `--agile` flag is set for this run. "
    "Follow **Path A — Agile RSD** below (SAFe Epic → Feature → Story "
    "hierarchy with Gherkin acceptance criteria, INVEST validation, and "
    "Enabler Stories for NFRs). Do NOT emit any content from **Path B**; "
    "treat it as documentation of the alternative format only."
)

_AGILE_MODE_DIRECTIVE_DEFAULT = (
    "**EXECUTION MODE: DEFAULT.** The `--agile` flag is not set for this "
    "run. Follow **Path B — Default RSD** below (ISO/IEC/IEEE 29148:2018 "
    "structure with numbered sections, `shall`/`should` requirement "
    "statements, and a flat RTM). Do NOT emit any content from **Path A**; "
    "treat it as documentation of the alternative format only."
)


def apply_agile_directive(body: str, *, agile: bool) -> str:
    """Substitute the ``{AGILE_MODE_DIRECTIVE}`` placeholder in a loaded
    docgen prompt with the agile- or default-mode banner.

    Returns ``body`` unchanged when the placeholder is absent — keeps the
    call site safe for prompts that don't carry the placeholder (e.g.
    arch_doc, the discovery prompts).
    """
    directive = _AGILE_MODE_DIRECTIVE_AGILE if agile else _AGILE_MODE_DIRECTIVE_DEFAULT
    return body.replace("{AGILE_MODE_DIRECTIVE}", directive)


def load(name: str, workspace_path: Optional[str] = None) -> str:
    """Return the markdown body of the docgen prompt named ``name``.

    Resolution order:
        1. ``{workspace_path}/skills/docgen/{name}.md`` — per-project override
        2. ``harness/skills/docgen/{name}.md``         — shipped default

    The first hit wins; results are cached in-process keyed by the resolved
    absolute path so subsequent calls in the same process avoid disk I/O.

    Args:
        name: Stem of the prompt file (no extension). E.g.
            ``"requirements_discovery"``.
        workspace_path: When supplied, the per-project override directory
            is consulted first. Pass ``None`` (the default) for callers
            that have no workspace context.

    Returns:
        The full markdown body as a single string.

    Raises:
        FileNotFoundError: when no matching file exists in either tier.
        OSError: when the file exists but cannot be read (permissions,
            symlink loop, etc.). Callers should let this propagate — the
            discovery prompts are required for correct operation.
    """
    candidates: list[str] = []
    if workspace_path:
        candidates.append(os.path.join(workspace_path, "skills", "docgen", f"{name}.md"))
    candidates.append(os.path.join(HARNESS_DOCGEN_DIR, f"{name}.md"))

    for path in candidates:
        if not os.path.isfile(path):
            continue

        with _CACHE_LOCK:
            cached = _CACHE.get(path)
        if cached is not None:
            return cached

        with open(path, "r", encoding="utf-8") as fp:
            body = fp.read()

        with _CACHE_LOCK:
            _CACHE[path] = body
        logger.debug("[docgen_prompts] Loaded '%s' from %s (%d chars).",
                     name, path, len(body))
        return body

    raise FileNotFoundError(
        f"docgen prompt '{name}' not found in any of: {candidates}. "
        f"Expected shipped default at harness/skills/docgen/{name}.md."
    )


def clear_cache() -> None:
    """Drop all cached prompt bodies. Tests call this after editing files."""
    with _CACHE_LOCK:
        _CACHE.clear()

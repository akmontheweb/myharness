"""
Technology-specific coding style guide loader.

Loads condensed style-guide markdown for the technologies actually in
use in the workspace, so the patcher and repair LLM produce code that
follows e.g. PEP 8 for Python, Airbnb for JavaScript, the GitLab/
Simon Holywell guides for SQL — and *only* those — without bloating
prompts for stacks the workspace doesn't use.

This module mirrors the existing harness/skills/*.md loader in
``harness.graph`` (``_load_skills_markdown`` / ``_parse_skill_frontmatter``)
but renders into its own prompt section with its own byte budget so
style content cannot crowd out architectural skills.

Layout:
    harness/style_guides/<name>.md         — shipped defaults
    {workspace_path}/style_guides/<name>.md — per-project overrides

Per-project files with the same filename as a harness default replace
that default entirely (same precedence rule the skills system uses).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


HARNESS_STYLE_GUIDES_DIR = os.path.join(os.path.dirname(__file__), "style_guides")

DEFAULT_MAX_FILE_CHARS = 24576
# Sized for the realistic worst-case polyglot full-stack workspace
# (composite web-design-system ≈ 18 KB + python ≈ 3 + react ≈ 3 +
# typescript ≈ 3 + html ≈ 3 + css ≈ 3 + sql ≈ 3 ≈ 36 KB). Composite
# design-system specs run ~14–22 KB each; focused single-source guides
# stay at ~3 KB. The static system prompt prefix-caches across calls,
# so the absolute size matters less than keeping it deterministic.
DEFAULT_MAX_TOTAL_CHARS = 49152


def _load_style_guides_markdown(
    style_guides_dir: str,
    workspace_tags: Optional[set[str]] = None,
    max_file_chars: int = DEFAULT_MAX_FILE_CHARS,
    skip_filenames: Optional[set[str]] = None,
) -> tuple[str, set[str]]:
    """Scan ``style_guides_dir`` and return (concatenated_body, loaded_filenames).

    Filtering, frontmatter parsing, and byte capping reuse the same
    helpers the harness/skills loader already uses (``_parse_skill_frontmatter``
    in ``harness.graph``) so behavior stays in lockstep.

    Args:
        style_guides_dir: Absolute path to the directory to scan.
        workspace_tags: Tags from ``impact._detect_workspace_stack``. When
            None, no filtering — every ``.md`` file loads. When supplied,
            files with an ``applies_to:`` frontmatter only load if at
            least one declared tag appears in ``workspace_tags``.
        max_file_chars: Per-file read cap.
        skip_filenames: Filenames already loaded from an earlier tier
            (project overrides win) — skip them here.

    Returns:
        (rendered_markdown, set_of_filenames_loaded). Empty string +
        empty set when nothing matches or the directory is missing.
    """
    if not os.path.isdir(style_guides_dir):
        return "", set()

    # Late-bound import: harness.graph imports this module from inside
    # _build_system_prompt (deferred), so a module-level import of
    # harness.graph here would create a cycle on the first call.
    from harness.graph import _parse_skill_frontmatter

    skip = skip_filenames or set()
    parts: list[str] = []
    loaded: set[str] = set()

    try:
        names = sorted(os.listdir(style_guides_dir))
    except OSError:
        return "", set()

    for fname in names:
        if not fname.endswith(".md") or fname in skip:
            continue
        fpath = os.path.join(style_guides_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as sf:
                content = sf.read(max_file_chars)
        except OSError:
            logger.warning("[style_guides] Could not read style guide: %s", fpath)
            continue

        applies_to, body = _parse_skill_frontmatter(content)

        # Files without frontmatter load unconditionally (matches the
        # skills loader's "universal" semantics).
        if workspace_tags is not None and applies_to is not None:
            if not (applies_to & workspace_tags):
                logger.debug(
                    "[style_guides] Skipping %s (applies_to=%s, workspace=%s)",
                    fname, sorted(applies_to), sorted(workspace_tags),
                )
                continue

        if body.strip():
            parts.append(body.rstrip())
            loaded.add(fname)

    return "\n\n".join(parts), loaded


def load_style_guides(
    workspace_path: str,
    workspace_tags: Optional[set[str]] = None,
    max_file_chars: int = DEFAULT_MAX_FILE_CHARS,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> str:
    """Return the rendered style-guide block to inject into the system prompt.

    Two-tier load order:
        1. Per-project overrides at ``{workspace_path}/style_guides/``
        2. Harness defaults at ``harness/style_guides/``
    Project files win on filename collision (a project ``python.md``
    replaces the shipped ``python.md`` entirely; it is not concatenated
    with it). This mirrors the skills system's precedence.

    Returns the empty string when no guides match — callers should
    skip the section header entirely in that case so projects that
    don't use any covered tech see no prompt growth.
    """
    project_dir = os.path.join(workspace_path or "", "style_guides")
    project_body, project_loaded = _load_style_guides_markdown(
        project_dir, workspace_tags=workspace_tags, max_file_chars=max_file_chars,
    )
    harness_body, _ = _load_style_guides_markdown(
        HARNESS_STYLE_GUIDES_DIR,
        workspace_tags=workspace_tags,
        max_file_chars=max_file_chars,
        skip_filenames=project_loaded,
    )

    # Project tier rendered first so the LLM sees house style ahead of
    # the shipped defaults.
    blocks = [b for b in (project_body, harness_body) if b]
    rendered = "\n\n".join(blocks)
    if not rendered:
        return ""
    if len(rendered) > max_total_chars:
        rendered = rendered[:max_total_chars]
    return rendered

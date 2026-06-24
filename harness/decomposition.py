"""LangGraph node that decomposes the approved requirements spec into stories.

This is the entry point of the Agile / per-story TDD path:

    human_gatekeeper(ARCHITECTURE) → decomposition_node
                                       → human_gatekeeper(STORIES)
                                       → batch_planner_node …

The node:

1. Reads ``<workspace>/docs/SPEC_REQUIREMENTS.md`` (and, when present,
   ``SPEC_ARCHITECTURE.md``) as the source material.
2. Calls the planning LLM with a structured prompt that asks for a
   list of vertical-slice stories, each with acceptance criteria,
   dependencies, and a scope_files hint.
3. Persists the stories into ``<workspace>/.teane/state.db`` via
   ``harness.story_state.create_stories``.
4. Regenerates ``docs/STORIES.md`` from the DB so the STORIES
   gatekeeper has a fresh view to show the operator.

LLM cost is capped: at most ``MAX_STORIES_PER_PASS`` stories per
call. If the model wants more, ``next_pass_summary`` carries
remaining intent into the next decomposition pass (future work — for
now a single pass is the contract).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


MAX_STORIES_PER_PASS = 20
"""Hard cap. The prompt instructs the LLM to merge or defer beyond this.
Large specs that need more than 20 stories should re-run decomposition
after the first batch lands — incremental decomposition is cheaper and
gives the operator a chance to course-correct after seeing real output."""


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _build_decomposition_prompt(
    spec_requirements: str,
    spec_architecture: str,
    workspace_path: str,
) -> str:
    """Compose the planner prompt. The LLM returns JSON; the body of
    this function is the contract every decomposition LLM must follow."""
    spec_block = "## SPEC_REQUIREMENTS.md\n\n" + (spec_requirements or "_(empty)_")
    if spec_architecture:
        spec_block += "\n\n## SPEC_ARCHITECTURE.md\n\n" + spec_architecture

    return f"""You are an Agile delivery planner. Decompose the approved
specification below into a list of **vertical-slice stories** that the
teane code-generation agent will implement one at a time using a
test-first loop (acceptance tests → code → run → repair → commit).

Workspace: {workspace_path}

A good story:

- Is a thin, end-to-end slice of value (e.g. "user can register with
  email + password and receive a confirmation"), NOT a horizontal
  layer (NOT "set up the database schema" by itself).
- Has 1–4 concrete acceptance criteria that a behavioral test can
  exercise against the public surface (CLI command, HTTP endpoint,
  library function, UI route).
- Names the files it expects to touch in ``scope_files`` when you
  have a high-confidence guess. Use module/file relative paths; the
  planner will intersect this with the workspace allowlist.
- Declares hard dependencies on prior stories in ``depends_on``.
  Independent stories run in parallel, so omit deps where genuinely
  optional. Use the ``story_key`` strings you assign (STORY-1,
  STORY-2, …).

Group stories under epics ONLY when the grouping is meaningful (e.g.
"auth", "billing"). If the project is small enough that everything is
one epic, leave ``epic`` null — don't invent ceremony.

Output STRICT JSON in this exact shape — no markdown, no code fence,
no commentary:

{{
  "epics": [
    {{"name": "auth", "description": "1-line"}}
  ],
  "stories": [
    {{
      "story_key": "STORY-1",
      "epic": "auth",
      "title": "User can register",
      "description": "1-2 sentence summary of intent.",
      "acceptance_criteria": [
        "POST /register with valid payload returns 201",
        "Duplicate email returns 409"
      ],
      "depends_on": [],
      "scope_files": ["src/auth/register.py", "tests/test_register.py"]
    }}
  ],
  "summary": "1-line description of the decomposition shape"
}}

Constraints:

- AT MOST {MAX_STORIES_PER_PASS} stories per pass. If the spec calls
  for more, merge the closest-coupled ones and put the leftovers in
  a final "polish" story that the next pass can re-decompose.
- ``story_key`` MUST be ``STORY-N`` with N starting at 1 and
  monotonically increasing. The DB layer re-checks this — mismatches
  are rejected.
- Every story MUST have at least one acceptance_criteria entry.
- ``depends_on`` may only reference story_keys that appear earlier
  in the same response.

Specification follows:

{spec_block}
"""


def _validate_stories_payload(data: Any) -> list[dict[str, Any]]:
    """Sanity-check the LLM's JSON. Returns the cleaned story list.

    Raises ValueError with a precise message on shape violations so
    the caller can surface it to the operator instead of writing a
    corrupt batch into the DB."""
    if not isinstance(data, dict):
        raise ValueError(f"top-level must be JSON object, got {type(data).__name__}")
    stories = data.get("stories")
    if not isinstance(stories, list) or not stories:
        raise ValueError("'stories' must be a non-empty list")
    if len(stories) > MAX_STORIES_PER_PASS:
        raise ValueError(
            f"too many stories ({len(stories)} > {MAX_STORIES_PER_PASS}); "
            "merge closely-coupled stories"
        )

    seen_keys: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for i, s in enumerate(stories, start=1):
        if not isinstance(s, dict):
            raise ValueError(f"story #{i} must be an object")
        key = s.get("story_key")
        if not isinstance(key, str) or not key.startswith("STORY-"):
            raise ValueError(f"story #{i} has invalid story_key: {key!r}")
        if key in seen_keys:
            raise ValueError(f"duplicate story_key {key}")
        seen_keys.add(key)
        title = s.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"{key} is missing a non-empty title")
        ac = s.get("acceptance_criteria") or []
        if not isinstance(ac, list) or not ac:
            raise ValueError(f"{key} must have at least one acceptance criterion")
        deps = s.get("depends_on") or []
        if not isinstance(deps, list):
            raise ValueError(f"{key} depends_on must be a list")
        for d in deps:
            if d not in seen_keys:
                raise ValueError(
                    f"{key} depends_on '{d}' which is not declared earlier"
                )
        scope = s.get("scope_files") or []
        if not isinstance(scope, list):
            raise ValueError(f"{key} scope_files must be a list")
        cleaned.append({
            "title": title.strip(),
            "epic": s.get("epic") or None,
            "description": s.get("description") or None,
            "acceptance_criteria": [str(x) for x in ac],
            "depends_on": [str(x) for x in deps],
            "scope_files": [str(x) for x in scope],
            "external_ref": s.get("external_ref") or None,
        })
    return cleaned


def strip_json_fence(content: str) -> str:
    """Tolerate a code-fenced JSON response even though the prompt
    forbids it — some models add fences anyway. Shared with
    ``harness.batch_sizing``; same shape applies to every JSON-mode
    LLM call we make."""
    s = content.strip()
    if s.startswith("```"):
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


async def decomposition_node(state: dict[str, Any]) -> dict[str, Any]:
    """Decompose the approved spec into stories, persist them, regenerate views.

    Returns a state-delta dict in the LangGraph convention. Sets:

    - ``stories_db_path`` — absolute path to the workspace state DB
    - ``current_gate`` = "STORIES" so the next hop into
      ``human_gatekeeper_node`` knows which gate to render
    - ``node_state.decomposition_complete`` boolean + story count
    """
    from harness.gateway import NodeRole
    from harness.graph import get_gateway
    from harness import story_state

    workspace = state.get("workspace_path") or os.getcwd()
    spec_req = _read_text(os.path.join(workspace, "docs", "SPEC_REQUIREMENTS.md"))
    if not spec_req.strip():
        logger.warning("[decomposition] SPEC_REQUIREMENTS.md is empty or missing")
        return {
            "current_gate": "STORIES",
            "node_state": {
                "current_node": "decomposition",
                "decomposition_complete": True,
                "error": "spec_requirements_missing",
                "story_count": 0,
            },
        }
    spec_arch = _read_text(os.path.join(workspace, "docs", "SPEC_ARCHITECTURE.md"))

    gateway = get_gateway()
    if gateway is None:
        logger.error("[decomposition] No gateway configured.")
        return {
            "current_gate": "STORIES",
            "node_state": {
                "current_node": "decomposition",
                "decomposition_complete": True,
                "error": "no_gateway",
                "story_count": 0,
            },
        }

    budget = state.get("budget_remaining_usd", 0.0)
    if budget <= 0:
        logger.warning("[decomposition] Budget exhausted ($%.4f); skipping.", budget)
        return {
            "current_gate": "STORIES",
            "node_state": {
                "current_node": "decomposition",
                "decomposition_complete": True,
                "error": "budget_exhausted",
                "story_count": 0,
            },
            "budget_remaining_usd": budget,
        }

    prompt = _build_decomposition_prompt(spec_req, spec_arch, workspace)
    system_msg = state.get("messages", [{}])[0] if state.get("messages") else {}
    call_messages = [system_msg, {"role": "user", "content": prompt}]

    try:
        response, budget = await gateway.dispatch(
            messages=call_messages,
            role=NodeRole.PLANNING,
            budget_remaining_usd=budget,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[decomposition] gateway dispatch failed: %s", exc)
        return {
            "current_gate": "STORIES",
            "node_state": {
                "current_node": "decomposition",
                "decomposition_complete": True,
                "error": f"dispatch_failed: {exc}",
                "story_count": 0,
            },
            "budget_remaining_usd": budget,
        }

    raw = strip_json_fence(getattr(response, "content", "") or "")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("[decomposition] LLM returned invalid JSON: %s", exc)
        return {
            "current_gate": "STORIES",
            "node_state": {
                "current_node": "decomposition",
                "decomposition_complete": True,
                "error": f"invalid_json: {exc}",
                "story_count": 0,
            },
            "budget_remaining_usd": budget,
        }

    try:
        cleaned = _validate_stories_payload(data)
    except ValueError as exc:
        logger.error("[decomposition] payload validation failed: %s", exc)
        return {
            "current_gate": "STORIES",
            "node_state": {
                "current_node": "decomposition",
                "decomposition_complete": True,
                "error": f"validation: {exc}",
                "story_count": 0,
            },
            "budget_remaining_usd": budget,
        }

    db_path = story_state.state_db_path()
    app_name = story_state.app_name_for_workspace(workspace)
    # CR-mode decompositions tag every story as a CR layer so the
    # traceability matrix can split greenfield work from incremental
    # change-request work. cr_ids is the integer set ingested in this
    # run; greenfield runs leave it None.
    if state.get("change_request_mode"):
        build_kind = story_state.BUILD_KIND_CR
        cr_ids = sorted({
            int(r.get("cr_id"))
            for r in (state.get("change_request_files") or [])
            if r.get("cr_id") is not None
        })
    else:
        build_kind = story_state.BUILD_KIND_GREENFIELD
        cr_ids = None
    conn = story_state.open_story_db()
    try:
        created_keys = story_state.create_stories(
            conn, app_name, cleaned,
            build_kind=build_kind, cr_ids=cr_ids,
        )
        stories_md, _ = story_state.regenerate_markdown_views(conn, workspace)
    finally:
        conn.close()

    logger.info(
        "[decomposition] created %d stories (%s); STORIES.md regenerated at %s",
        len(created_keys),
        ", ".join(created_keys),
        stories_md,
    )

    return {
        "stories_db_path": db_path,
        "current_gate": "STORIES",
        "budget_remaining_usd": budget,
        "node_state": {
            "current_node": "decomposition",
            "decomposition_complete": True,
            "story_count": len(created_keys),
            "story_keys": created_keys,
            "stories_md_path": stories_md,
            "summary": data.get("summary") or "",
        },
    }

"""Per-workspace completion markers for top-level teane flows.

teane's record of "did flow X complete cleanly for workspace Y" lives in
``<workspace>/.teane/last_<flow>.json``. cmd_run writes the marker on a
clean exit (exit_code == 0) for any flow in {build, patch, deploy};
``teane test``'s prerequisite gate reads them.

This file is intentionally small and self-contained — it stays out of
``harness/story_state.py`` (the global agile DB at ``~/.harness/state.db``)
because flow-completion is a workspace-local concern and shouldn't share
a schema with the multi-workspace agile rows.

Marker shape::

    {
      "flow": "deploy",
      "session_id": "abc123",
      "completed_at": "2026-06-30T17:42:11+00:00",
      "exit_code": 0,
      "summary": {"modified_files": 12, "loop_counters": {...}}  # optional
    }
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


_TEANE_DIR = ".teane"
_TRACKED_FLOWS = frozenset({"build", "patch", "deploy"})


def _marker_path(workspace_path: str, flow: str) -> str:
    return os.path.join(workspace_path, _TEANE_DIR, f"last_{flow}.json")


def record_flow_completion(
    *,
    workspace_path: str,
    flow: str,
    session_id: str,
    exit_code: int,
    summary: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Write the success marker for ``flow`` against ``workspace_path``.

    No-ops (returns None) when:
      - flow is not in the tracked set
      - exit_code != 0 (failure markers are not written — absence == failure)
      - write itself raises (logged, swallowed; failures here must not
        change cmd_run's exit code)

    Returns the marker path on success so callers can log it.
    """
    if flow not in _TRACKED_FLOWS:
        return None
    if exit_code != 0:
        return None

    path = _marker_path(workspace_path, flow)
    payload: dict[str, Any] = {
        "flow": flow,
        "session_id": session_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": exit_code,
    }
    if summary is not None:
        payload["summary"] = summary

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        logger.info("[flow_state] Recorded %s completion at %s", flow, path)
        return path
    except OSError as exc:
        logger.warning("[flow_state] Could not write %s marker: %s", flow, exc)
        return None


def read_flow_completion(
    workspace_path: str,
    flow: str,
) -> Optional[dict[str, Any]]:
    """Return the last-completion marker for ``flow``, or None if absent.

    Returns None on any IO/JSON error so callers treat it as
    "no clean prior run on record."
    """
    path = _marker_path(workspace_path, flow)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[flow_state] %s marker unreadable: %s", flow, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def check_test_prereqs(workspace_path: str) -> tuple[bool, str]:
    """Verify ``teane test`` prerequisites: a clean build OR patch, plus a clean deploy.

    Returns (ok, reason). ``reason`` is a short human-readable string suitable
    for logging or surfacing in node_state.

    The prerequisite shape mirrors the discussion plan:
      - At least one of build / patch must have completed cleanly (so the app exists)
      - deploy must have completed cleanly (so something is up to test against)
    """
    deploy = read_flow_completion(workspace_path, "deploy")
    if deploy is None:
        return False, "no clean `teane deploy` on record for this workspace"

    build = read_flow_completion(workspace_path, "build")
    patch = read_flow_completion(workspace_path, "patch")
    if build is None and patch is None:
        return False, "no clean `teane build` or `teane patch` on record"

    return True, "ok"

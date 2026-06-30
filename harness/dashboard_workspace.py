"""Workspace-scoped views for the operator dashboard.

Three concerns live here:

1. **Workspace status widget** — three tiles reading the flow-completion
   markers at ``<workspace>/.teane/last_{build,patch,deploy}.json``
   (written by :mod:`harness.flow_state`) plus a "Run test" gate driven
   by :func:`harness.flow_state.check_test_prereqs`.
2. **CR-DEFECT file serving** — a path-traversal-safe endpoint that
   serves the attachments emitted by ``teane test``'s defect bundles
   so the operator can review them from the session-detail page.
3. **Standalone traceability page** — ``/workspaces/<app>/traceability``
   linked from the dashboards landing for operators who want the
   workspace audit without opening a specific session.

Read-only; no writes land through this module.
"""

from __future__ import annotations

import glob
import html
import logging
import mimetypes
import os
import re
from typing import Any, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


# ---------------------------------------------------------------------------
# Workspace status widget
# ---------------------------------------------------------------------------

_TRACKED_FLOWS = ("build", "patch", "deploy")


def render_workspace_status_widget(workspace_path: str) -> str:
    """Render the per-workspace status card.

    Reads ``<workspace>/.teane/last_{build,patch,deploy}.json`` via
    :func:`harness.flow_state.read_flow_completion` and renders three
    tiles with the last session id + completion time. The Test tile
    consults :func:`harness.flow_state.check_test_prereqs` and either
    surfaces the prereq gap (linking to /run/deploy) or a "Ready"
    button linking to /run/test.

    Returns an empty string when the workspace path isn't usable so the
    session-detail page doesn't accumulate an empty card on fresh runs.
    """
    if not workspace_path or not os.path.isdir(workspace_path):
        return ""

    try:
        from harness.flow_state import check_test_prereqs, read_flow_completion
    except ImportError:
        return ""

    tiles: list[str] = []
    for flow in _TRACKED_FLOWS:
        record = read_flow_completion(workspace_path, flow)
        tiles.append(_render_flow_tile(flow, record))
    tiles.append(_render_test_gate_tile(workspace_path, check_test_prereqs))

    return (
        "<div class='card'><h2>Workspace status</h2>"
        f"<p class='muted'>{_esc(workspace_path)}</p>"
        "<div class='workspace-status-grid'>"
        + "".join(tiles) +
        "</div></div>"
    )


def _render_flow_tile(flow: str, record: Optional[dict[str, Any]]) -> str:
    if not record:
        return (
            "<div class='tile tile--muted'>"
            f"<h3>{_esc(flow.title())}</h3>"
            "<p class='muted'>No clean completion recorded.</p>"
            "</div>"
        )
    sid = record.get("session_id") or ""
    completed_at = record.get("completed_at") or ""
    exit_code = record.get("exit_code")
    return (
        "<div class='tile'>"
        f"<h3>{_esc(flow.title())}</h3>"
        f"<p>Session: <code>{_esc(sid)}</code></p>"
        f"<p>Completed: {_esc(completed_at)}</p>"
        f"<p>Exit: <code>{_esc(exit_code)}</code></p>"
        "</div>"
    )


def _render_test_gate_tile(workspace_path: str, check_fn) -> str:
    try:
        ok, reason = check_fn(workspace_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[workspace-status] prereq check failed: %s", exc)
        return (
            "<div class='tile tile--muted'>"
            "<h3>Test</h3>"
            "<p class='muted'>Could not evaluate prerequisites.</p>"
            "</div>"
        )
    if ok:
        ws_q = quote(workspace_path, safe="")
        return (
            "<div class='tile tile--green'>"
            "<h3>Test</h3>"
            "<p>Prerequisites met.</p>"
            f"<p><a href='/run/test?workspace={ws_q}' "
            "class='bx--btn bx--btn--primary bx--btn--sm'>"
            "Run test</a></p>"
            "</div>"
        )
    return (
        "<div class='tile tile--muted'>"
        "<h3>Test</h3>"
        f"<p class='muted'>Blocked: {_esc(reason)}</p>"
        "<p><a href='/run/deploy' class='bx--btn bx--btn--tertiary bx--btn--sm'>"
        "Deploy first</a></p>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Standalone traceability page (Phase 3.2 finish)
# ---------------------------------------------------------------------------

def render_workspace_traceability_page(workspace_path: str) -> str:
    """Standalone per-workspace traceability page.

    Wraps the same ``render_traceability_card`` used on the
    session-detail page so the workspace-keyed view and the in-session
    view stay in lock-step.
    """
    if not workspace_path:
        return (
            "<div class='card'><p class='muted'>"
            "Pass <code>?path=&lt;workspace&gt;</code> to render the audit "
            "for a specific workspace.</p></div>"
        )
    try:
        from harness.dashboard_v5views import render_traceability_card
    except ImportError:
        return "<div class='card'><p class='muted'>Traceability views unavailable.</p></div>"
    card = render_traceability_card(workspace_path)
    if not card:
        return (
            "<div class='card'><p class='muted'>"
            "Workspace not found or audit unavailable.</p></div>"
        )
    return card


# ---------------------------------------------------------------------------
# CR-DEFECT file serving (Phase 4.4)
# ---------------------------------------------------------------------------

# Operators can download these files via the dashboard's
# /api/workspace-file route. Anything else gets a 403 — defending
# against a malicious ``relpath`` that climbs out of change_requests/
# even after the realpath check passes.
_CR_DEFECT_ALLOWED_BASENAMES = frozenset({
    "narrative.txt",
    "source_spec.md",
    "cluster_evidence.json",
    "screenshot.png",
    "trace.zip",
    "dom.html",
})

# Matches the directory name `teane test` emits per
# :func:`harness.test_defects.emit_defect_cr`. The pattern is broad on
# purpose (date / slug / hash all enforced by emit_defect_cr); we only
# need to confirm the dirname prefix here so the operator can't ask
# for files under an arbitrary change_requests/* sibling.
_CR_DEFECT_DIR_RE = re.compile(r"^CR-DEFECT-")


def workspace_file_payload(
    workspace_path: str, relpath: str,
) -> tuple[int, str, bytes]:
    """Return ``(status, content_type, body)`` for the workspace-file
    GET endpoint.

    Two defenses against path-traversal:

    1. Resolve both ``workspace_path`` and the joined target via
       :func:`os.path.realpath` and verify the target stays under
       ``<workspace>/change_requests/``.
    2. Walk the resolved path: it must traverse
       ``change_requests/CR-DEFECT-<...>/`` exactly once, and the
       leaf basename must be in the allowlist
       :data:`_CR_DEFECT_ALLOWED_BASENAMES`.

    Returns ``(404, "text/plain", b"...")`` for any rejection so a
    malicious caller can't distinguish "file missing" from "denied".
    """
    if not workspace_path or not relpath:
        return 404, "text/plain", b"not found\n"
    ws_root = os.path.realpath(os.path.expanduser(workspace_path))
    if not os.path.isdir(ws_root):
        return 404, "text/plain", b"not found\n"

    # Reject literal absolute relpaths; we want the operator-supplied
    # query parameter to always join under the workspace root.
    if os.path.isabs(relpath):
        return 404, "text/plain", b"not found\n"

    target = os.path.realpath(os.path.join(ws_root, relpath))
    cr_root = os.path.realpath(os.path.join(ws_root, "change_requests"))
    # Defense 1: still under change_requests/
    if not (target == cr_root or target.startswith(cr_root + os.sep)):
        return 404, "text/plain", b"not found\n"

    # Walk: split into (change_requests, CR-DEFECT-..., basename).
    relative_to_cr = os.path.relpath(target, cr_root)
    parts = relative_to_cr.split(os.sep)
    if len(parts) < 2:
        return 404, "text/plain", b"not found\n"
    if not _CR_DEFECT_DIR_RE.match(parts[0]):
        return 404, "text/plain", b"not found\n"
    basename = parts[-1]
    if basename not in _CR_DEFECT_ALLOWED_BASENAMES:
        return 404, "text/plain", b"not found\n"

    # All checks passed; serve the file.
    if not os.path.isfile(target):
        return 404, "text/plain", b"not found\n"
    try:
        with open(target, "rb") as fh:
            data = fh.read()
    except OSError:
        return 404, "text/plain", b"not found\n"
    ctype, _ = mimetypes.guess_type(basename)
    return 200, ctype or "application/octet-stream", data


def list_cr_defects(workspace_path: str) -> list[str]:
    """Return the CR-DEFECT-* directory names directly under
    ``<workspace>/change_requests/``.

    Convenience helper for renderers that want to enumerate the
    available defect bundles without parsing a JSONL log.
    """
    if not workspace_path or not os.path.isdir(workspace_path):
        return []
    cr_root = os.path.join(workspace_path, "change_requests")
    if not os.path.isdir(cr_root):
        return []
    matches = []
    for entry in glob.glob(os.path.join(cr_root, "CR-DEFECT-*")):
        if os.path.isdir(entry):
            matches.append(os.path.basename(entry))
    matches.sort()
    return matches

"""V5-traceability and test-result views for the operator dashboard.

These renderers consume read-only data from the global
``~/.harness/state.db`` (v4/v5 schema in :mod:`harness.story_state`)
and the per-session JSONL log to surface:

- **Feature/story panel**: the stories the run worked on, grouped by
  feature, with build_kind / cr_ids badges per row.
- **Batch panel**: per-session batches with their commit SHA and
  build_kind, tying a ``teane build``/``patch`` session to the
  feature-first decomposition rows it produced.
- **Test-results panel**: the verdict (``passed``/``failed``/
  ``cluster_count``) plus links to each CR-DEFECT-* directory's
  attachments, served via the ``/api/workspace-file`` route in
  :mod:`harness.dashboard_workspace`.
- **Traceability panel**: per-workspace coverage gauges plus the
  untraced / untested lists from :func:`harness.traceability.audit_workspace`.

Nothing here writes; the dashboard's POST handlers stay in
:mod:`harness.dashboard`. The renderers take a workspace path or a
session_id and return an HTML fragment ready to drop inside a card.
"""

from __future__ import annotations

import html
import json
import logging
import os
import sqlite3
from typing import Any, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _safe_json_line(line: str) -> Optional[dict[str, Any]]:
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def resolve_workspace_from_log(log_path: str) -> str:
    """Read the first ``session_start`` event from ``log_path`` and
    return its ``workspace_path`` field. Empty string when the log
    doesn't exist, has no session_start, or isn't a valid JSONL stream.

    The dashboard's per-session detail view receives a session_id, not a
    workspace path; this helper bridges the two using the same event
    the sessions-list renderer already inspects.
    """
    if not log_path or not os.path.isfile(log_path):
        return ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                evt = _safe_json_line(line)
                if not evt:
                    continue
                if evt.get("event") == "session_start":
                    return str(evt.get("workspace_path") or "")
    except OSError:
        return ""
    return ""


def _build_kind_badge(build_kind: str) -> str:
    """One-line badge for ``stories.build_kind`` / ``batches.build_kind``.

    Green for greenfield (the canonical first run), blue for CR-driven
    (incremental patch addressing a change_request).
    """
    kind = (build_kind or "").strip().lower()
    if kind == "greenfield":
        return "<span class='bx--tag bx--tag--green'>greenfield</span>"
    if kind == "cr":
        return "<span class='bx--tag bx--tag--blue'>cr</span>"
    return f"<span class='bx--tag'>{_esc(kind or '—')}</span>"


def _cr_ids_badge(cr_ids: Any) -> str:
    """Render a ``[CR-3, CR-7]`` chip row for the cr_ids JSON list.

    Empty list → empty string (no chip clutter on greenfield rows).
    """
    if not cr_ids:
        return ""
    try:
        items = [int(c) for c in cr_ids]
    except (TypeError, ValueError):
        return ""
    if not items:
        return ""
    return " ".join(
        f"<span class='bx--tag bx--tag--purple'>CR-{c}</span>" for c in items
    )


def _open_state_db_readonly() -> Optional[sqlite3.Connection]:
    """Open ``~/.harness/state.db`` read-only for dashboard queries.

    Returns None when the file doesn't exist (e.g. waterfall workspace
    that never engaged Agile mode). The dashboard handles that by
    rendering an empty-state card rather than 500-ing.
    """
    try:
        from harness.story_state import state_db_path  # local import; story_state is heavy
    except ImportError:
        return None
    db_path = state_db_path()
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=2.0,
            isolation_level=None,
        )
        conn.execute("PRAGMA busy_timeout = 1000")
        return conn
    except sqlite3.Error as exc:
        logger.warning("[v5views] state.db open failed: %s", exc)
        return None


def _list_batches_for_session(
    conn: sqlite3.Connection, workspace: str, session_id: str,
) -> list[dict[str, Any]]:
    """All batches in ``workspace`` whose ``session_id`` matches.

    The ``batches`` table doesn't have a dedicated helper for this in
    story_state.py — every existing helper filters by cr_id or
    batch_id. We pull what we need via a small inline SELECT against
    the documented column layout.
    """
    rows = conn.execute(
        "SELECT id, session_id, feature_id, started_at, completed_at, "
        "status, committed_sha, build_kind, cr_ids "
        "FROM batches WHERE workspace = ? AND session_id = ? "
        "ORDER BY id",
        (workspace, session_id),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            cr_list = json.loads(r[8]) if r[8] else []
        except (json.JSONDecodeError, TypeError):
            cr_list = []
        out.append({
            "id": r[0],
            "session_id": r[1],
            "feature_id": r[2],
            "started_at": r[3],
            "completed_at": r[4],
            "status": r[5],
            "committed_sha": r[6],
            "build_kind": r[7] or "",
            "cr_ids": cr_list,
        })
    return out


def render_session_features_card(workspace_path: str) -> str:
    """Render the "Features & stories" card for the session detail page.

    Groups stories by feature; each feature lists its stories with
    status / build_kind / cr_ids badges. Empty state when the
    workspace has no rows in state.db (waterfall workspaces, or
    sessions older than the v4 decomposition rollout).
    """
    if not workspace_path:
        return ""
    try:
        from harness.story_state import (
            app_name_for_workspace, list_features, list_stories,
        )
    except ImportError:
        return ""
    conn = _open_state_db_readonly()
    if conn is None:
        return ""
    try:
        app = app_name_for_workspace(workspace_path)
        features = list_features(conn, app)
        stories = list_stories(conn, app)
    except sqlite3.Error as exc:
        conn.close()
        logger.warning("[v5views] features/stories read failed: %s", exc)
        return ""
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if not stories:
        return (
            "<div class='card'>"
            "<h2>Features &amp; stories</h2>"
            "<p class='muted'>This workspace has no v4/v5 decomposition rows. "
            "Either it's a waterfall run, or the build pre-dates the "
            "feature-first decomposition rollout.</p>"
            "</div>"
        )

    # Bucket stories by feature_id; preserve feature insertion order.
    by_feature: dict[Optional[int], list[dict[str, Any]]] = {}
    for s in stories:
        by_feature.setdefault(s.get("feature_id"), []).append(s)

    body_parts: list[str] = []
    seen_features: set[Optional[int]] = set()
    for feat in features:
        fid = feat["id"]
        feat_stories = by_feature.get(fid, [])
        if not feat_stories:
            continue
        seen_features.add(fid)
        body_parts.append(_render_feature_block(feat, feat_stories))
    # Orphan stories (feature_id NULL or feature row missing) — render
    # under a sentinel "Unassigned" group so they don't silently vanish.
    orphans: list[dict[str, Any]] = []
    for fid, batch in by_feature.items():
        if fid in seen_features:
            continue
        orphans.extend(batch)
    if orphans:
        body_parts.append(_render_feature_block(
            {"feature_key": "(unassigned)", "name": "Unassigned"}, orphans,
        ))

    return (
        "<div class='card'>"
        "<h2>Features &amp; stories</h2>"
        + "".join(body_parts)
        + "</div>"
    )


def _render_feature_block(
    feature: dict[str, Any], stories: list[dict[str, Any]],
) -> str:
    feature_key = feature.get("feature_key") or "(unknown)"
    feature_name = feature.get("name") or feature_key
    header = (
        f"<h3>{_esc(feature_name)} "
        f"<code class='muted fs-sm'>{_esc(feature_key)}</code></h3>"
    )
    rows = []
    for s in stories:
        status = s.get("status") or ""
        rows.append(
            "<tr>"
            f"<td><code>{_esc(s.get('story_key'))}</code></td>"
            f"<td>{_esc(s.get('title') or '')}</td>"
            f"<td><span class='bx--tag'>{_esc(status)}</span></td>"
            f"<td>{_build_kind_badge(str(s.get('build_kind') or ''))}</td>"
            f"<td>{_cr_ids_badge(s.get('cr_ids'))}</td>"
            "</tr>"
        )
    table = (
        "<div class='table-wrap'><table class='w-100'>"
        "<thead><tr><th>Story</th><th>Title</th><th>Status</th>"
        "<th>Build kind</th><th>CRs</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table></div>"
    )
    return header + table


def render_session_batches_card(
    workspace_path: str, session_id: str,
) -> str:
    """Render the "Batches" card scoped to the given session.

    Each row carries the batch id, status, build_kind, commit SHA, and
    cr_ids — so an operator can trace a single ``teane build``/``patch``
    session to the rows of the feature-first decomposition.
    """
    if not workspace_path or not session_id:
        return ""
    try:
        from harness.story_state import app_name_for_workspace
    except ImportError:
        return ""
    conn = _open_state_db_readonly()
    if conn is None:
        return ""
    try:
        app = app_name_for_workspace(workspace_path)
        batches = _list_batches_for_session(conn, app, session_id)
    except sqlite3.Error as exc:
        conn.close()
        logger.warning("[v5views] batches read failed: %s", exc)
        return ""
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if not batches:
        # Waterfall sessions don't emit batches; render nothing so the
        # session-detail page doesn't accumulate empty cards.
        return ""

    rows = []
    for b in batches:
        sha = (b.get("committed_sha") or "")[:10]
        rows.append(
            "<tr>"
            f"<td>{int(b['id'])}</td>"
            f"<td>{_esc(b.get('status') or '')}</td>"
            f"<td>{_build_kind_badge(str(b.get('build_kind') or ''))}</td>"
            f"<td><code class='muted'>{_esc(sha) if sha else '—'}</code></td>"
            f"<td>{_cr_ids_badge(b.get('cr_ids'))}</td>"
            f"<td>{_esc(b.get('started_at') or '')}</td>"
            f"<td>{_esc(b.get('completed_at') or '—')}</td>"
            "</tr>"
        )
    return (
        "<div class='card'><h2>Batches</h2>"
        "<div class='table-wrap'><table class='w-100'>"
        "<thead><tr><th>ID</th><th>Status</th><th>Build kind</th>"
        "<th>Commit</th><th>CRs</th><th>Started</th><th>Completed</th>"
        "</tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table></div></div>"
    )


def _read_last_node_state_test(log_path: str) -> Optional[dict[str, Any]]:
    """Walk ``log_path`` for the most recent event whose ``node_state.test``
    is non-empty. Returns the test dict (per ``test_target.py`` shape) or
    None when no test event is present.
    """
    if not log_path or not os.path.isfile(log_path):
        return None
    found: Optional[dict[str, Any]] = None
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                evt = _safe_json_line(line)
                if not evt:
                    continue
                ns = evt.get("node_state") or {}
                if not isinstance(ns, dict):
                    continue
                test_dict = ns.get("test")
                if isinstance(test_dict, dict) and test_dict:
                    found = test_dict
    except OSError:
        return None
    return found


def render_session_test_results_card(
    workspace_path: str, log_path: str,
) -> str:
    """Render the "Test results" card from the run's last
    ``node_state.test`` event.

    For ``skipped=True`` + ``reason=prereq_failed`` we render an
    empty-state with a link to ``/run/deploy`` — that's the typical
    case where ``teane test`` exited because no clean prior deploy
    exists.

    Successful runs render the verdict plus per-CR-DEFECT rows
    linking to the screenshot / trace / DOM / cluster-evidence
    attachments via the ``/api/workspace-file`` route.
    """
    test = _read_last_node_state_test(log_path)
    if not test:
        return ""

    skipped = bool(test.get("skipped"))
    reason = str(test.get("reason") or "")
    if skipped:
        if reason == "prereq_failed":
            detail = _esc(test.get("detail") or "test prerequisites not met")
            return (
                "<div class='card'><h2>Test results</h2>"
                f"<p>Test run was skipped: {detail}.</p>"
                "<p><a href='/run/deploy' class='bx--btn bx--btn--tertiary'>"
                "Go to deploy</a></p></div>"
            )
        return (
            "<div class='card'><h2>Test results</h2>"
            f"<p class='muted'>Skipped: {_esc(reason or 'unknown')}</p>"
            "</div>"
        )

    passed = int(test.get("passed") or 0)
    failed = int(test.get("failed") or 0)
    clusters = int(test.get("cluster_count") or 0)
    scope = str(test.get("scope") or "—")
    base_url = str(test.get("base_url") or "")
    cr_paths = test.get("cr_paths") or []
    verdict_tag = (
        "<span class='bx--tag bx--tag--green'>green</span>"
        if failed == 0 else
        "<span class='bx--tag bx--tag--red'>red</span>"
    )
    summary = (
        f"<p>{verdict_tag} "
        f"Passed: <strong>{passed}</strong>, "
        f"Failed: <strong>{failed}</strong>, "
        f"Clusters: <strong>{clusters}</strong> "
        f"(scope: <code>{_esc(scope)}</code>"
        + (f", base: <code>{_esc(base_url)}</code>" if base_url else "")
        + ")</p>"
    )

    defects_block = ""
    if cr_paths and workspace_path:
        rows = []
        for cr_path in cr_paths:
            cr_path_str = str(cr_path)
            try:
                relpath = os.path.relpath(cr_path_str, workspace_path)
            except ValueError:
                # Different drive letters on Windows etc — fall back to
                # the basename so the link still works.
                relpath = os.path.basename(cr_path_str)
            ws_q = quote(workspace_path, safe="")
            # Render one row per CR-DEFECT directory with quick links
            # to each attachment basename. The dashboard_workspace
            # /api/workspace-file route enforces the basename allowlist.
            links = []
            for name in (
                "narrative.txt", "screenshot.png", "trace.zip",
                "cluster_evidence.json", "dom.html", "source_spec.md",
            ):
                file_relpath = quote(
                    os.path.join(relpath, name), safe="/",
                )
                links.append(
                    f"<a href='/api/workspace-file?workspace={ws_q}"
                    f"&relpath={file_relpath}'>{name}</a>"
                )
            rows.append(
                "<tr>"
                f"<td><code>{_esc(os.path.basename(cr_path_str.rstrip('/')))}</code></td>"
                f"<td>{' · '.join(links)}</td>"
                "</tr>"
            )
        defects_block = (
            "<h3>Defect bundles</h3>"
            "<div class='table-wrap'><table class='w-100'>"
            "<thead><tr><th>CR-DEFECT</th><th>Attachments</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table></div>"
        )

    return (
        "<div class='card'><h2>Test results</h2>"
        + summary + defects_block +
        "</div>"
    )


def render_traceability_card(workspace_path: str) -> str:
    """Render the "Traceability" card for ``workspace_path``.

    Calls :func:`harness.traceability.audit_workspace` and surfaces the
    two coverage percentages + the untraced-requirements /
    untested-acceptance-criteria lists. Empty state when:

    - The workspace path is missing / invalid.
    - The audit can't run (no state.db, no SPEC_REQUIREMENTS.md).
    - ``total_reqs`` is 0 (rendering 100% would mislead — we say "no
      requirements declared" instead).
    """
    if not workspace_path:
        return ""
    try:
        from harness.traceability import audit_workspace
    except ImportError:
        return ""
    try:
        report = audit_workspace(workspace_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[v5views] traceability audit failed: %s", exc)
        return ""
    if report is None:
        return (
            "<div class='card'><h2>Traceability</h2>"
            "<p class='muted'>Traceability audit unavailable — workspace "
            "missing, pre-v5, or SPEC_REQUIREMENTS.md not yet generated.</p>"
            "</div>"
        )

    if report.total_reqs == 0:
        return (
            "<div class='card'><h2>Traceability</h2>"
            "<p class='muted'>No requirements declared for this workspace yet.</p>"
            "</div>"
        )

    req_pct = report.req_coverage_pct
    ac_pct = report.ac_coverage_pct
    gauges = (
        "<div class='trace-gauges'>"
        f"<div><strong>Requirements</strong>: {report.traced_reqs}"
        f"/{report.total_reqs} <span class='bx--tag bx--tag--green'>"
        f"{req_pct:.0f}%</span></div>"
        f"<div><strong>Acceptance criteria</strong>: {report.verified_acs}"
        f"/{report.total_acs} <span class='bx--tag bx--tag--green'>"
        f"{ac_pct:.0f}%</span></div>"
        "</div>"
    )

    untraced_html = ""
    if report.untraced:
        items = "".join(
            f"<li><code>{_esc(u.req_id)}</code> "
            f"<span class='muted'>({_esc(u.kind)})</span></li>"
            for u in report.untraced
        )
        untraced_html = (
            "<details><summary><strong>Untraced requirements "
            f"({len(report.untraced)})</strong></summary>"
            f"<ul>{items}</ul></details>"
        )

    untested_html = ""
    if report.untested_acs:
        items = "".join(
            f"<li><code>{_esc(u.ac_key)}</code> "
            f"<span class='muted'>({_esc(u.story_key)})</span>"
            f": {_esc(u.text)}</li>"
            for u in report.untested_acs
        )
        untested_html = (
            "<details><summary><strong>Untested acceptance criteria "
            f"({len(report.untested_acs)})</strong></summary>"
            f"<ul>{items}</ul></details>"
        )

    return (
        "<div class='card'><h2>Traceability</h2>"
        + gauges + untraced_html + untested_html +
        "</div>"
    )

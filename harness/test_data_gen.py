"""Synthetic test-data generator for ``teane test``.

Phase 3 deliverable. Reads the workspace's spec/blueprint sources, infers
a schema context, and either calls the gateway LLM (Phase 5 wires this)
or runs the deterministic fallback to produce ``tests/e2e/fixtures/seed.json``.

Agile vs waterfall awareness (per user requirement):
    * ``detect_flow_kind`` returns ``"agile"`` when the workspace has
      stories in the global state DB (``~/.harness/state.db``,
      checked via ``story_state.workspace_is_agile_managed``), else
      ``"waterfall"``.
    * Agile context pulls per-story acceptance criteria (``ac_key``)
      so generated rows can be tagged with the story they satisfy —
      lets Phase 4 scenario generation cross-reference seed data with
      scenarios via the same ``STORY-N.AC-M`` key.
    * Waterfall context bundles SPEC_REQUIREMENTS.md +
      SPEC_DATA_MODEL.md + SPEC_ARCHITECTURE.md verbatim.

Cache: the seed fixture is keyed by sha256 of the normalised context.
A re-run with the same context returns the cached fixture without
hitting the LLM. The cache key is persisted alongside the fixture as
``seed.cache_key`` so cache validity is checkable without recomputing.

Phase 3 ships SQLite lifecycle helpers. Postgres / MongoDB land in
Phase 5 alongside the deploy-aware DB-kind dispatch.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from harness import story_state

logger = logging.getLogger(__name__)


FLOW_AGILE = "agile"
FLOW_WATERFALL = "waterfall"

_DEFAULT_FIXTURE_DIR = os.path.join("tests", "e2e", "fixtures")
_DEFAULT_FIXTURE_NAME = "seed.json"
_CACHE_KEY_NAME = "seed.cache_key"

# Spec files to bundle into the schema context. Order matters — the LLM
# prompt downstream reads them in order, so data-model first lets a
# truncating model retain the most useful signal.
_SPEC_FILES = (
    "docs/SPEC_DATA_MODEL.md",
    "docs/SPEC_REQUIREMENTS.md",
    "docs/SPEC_ARCHITECTURE.md",
    "docs/DEPLOYMENT_BLUEPRINT.md",
)


# ---------------------------------------------------------------------------
# Flow detection
# ---------------------------------------------------------------------------


def detect_flow_kind(workspace_path: str) -> str:
    """Return ``"agile"`` or ``"waterfall"`` for ``workspace_path``.

    Thin wrapper around ``story_state.workspace_is_agile_managed`` —
    centralising the call here means downstream callers don't have to
    know that the agile signal lives in a shared SQLite table.
    """
    try:
        return FLOW_AGILE if story_state.workspace_is_agile_managed(workspace_path) else FLOW_WATERFALL
    except Exception as exc:  # noqa: BLE001 — detection must not raise
        logger.warning("[test_data_gen] flow kind detection failed: %s — defaulting to waterfall", exc)
        return FLOW_WATERFALL


# ---------------------------------------------------------------------------
# Schema context
# ---------------------------------------------------------------------------


@dataclass
class SchemaContext:
    """Everything the seed-data generator needs, packaged for hashing.

    ``flow_kind`` is the agile/waterfall switch. ``spec_excerpts`` is a
    dict mapping spec-filename → content (only those present on disk).
    For agile workspaces, ``stories`` carries per-story acceptance
    criteria so the LLM (or fallback) can tag generated rows.
    """

    workspace_path: str
    flow_kind: str
    spec_excerpts: dict[str, str] = field(default_factory=dict)
    stories: list[dict[str, Any]] = field(default_factory=list)

    def to_normalised_dict(self) -> dict[str, Any]:
        """Stable representation for hashing — sort keys, exclude workspace_path
        (a different developer's clone of the same project should produce
        the same cache key)."""
        return {
            "flow_kind": self.flow_kind,
            "spec_excerpts": dict(sorted(self.spec_excerpts.items())),
            "stories": [
                {k: v for k, v in sorted(s.items())} for s in self.stories
            ],
        }


def gather_schema_context(workspace_path: str) -> SchemaContext:
    """Build a :class:`SchemaContext` for ``workspace_path``.

    Workspace files referenced by ``_SPEC_FILES`` are read verbatim;
    missing files are silently skipped (so this works against minimal
    workspaces and tests). When ``detect_flow_kind`` returns agile, the
    function also pulls every story with its acceptance criteria for
    the workspace.
    """
    flow_kind = detect_flow_kind(workspace_path)
    spec_excerpts = _read_spec_files(workspace_path)
    stories: list[dict[str, Any]] = []
    if flow_kind == FLOW_AGILE:
        stories = _gather_agile_stories(workspace_path)
    return SchemaContext(
        workspace_path=workspace_path,
        flow_kind=flow_kind,
        spec_excerpts=spec_excerpts,
        stories=stories,
    )


def _read_spec_files(workspace_path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in _SPEC_FILES:
        path = os.path.join(workspace_path, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                out[rel] = fh.read()
        except OSError as exc:
            logger.warning("[test_data_gen] could not read %s: %s", path, exc)
    return out


def _gather_agile_stories(workspace_path: str) -> list[dict[str, Any]]:
    """Pull each story + its AC rows. Soft-fails to empty on any DB error."""
    try:
        app = story_state.app_name_for_workspace(workspace_path)
    except ValueError:
        return []
    try:
        conn = story_state.open_story_db(workspace_path=workspace_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[test_data_gen] could not open story DB: %s", exc)
        return []
    try:
        stories = story_state.list_stories(conn, app)
        for s in stories:
            try:
                acs = story_state.list_acceptance_criteria(conn, app, s["id"])
                s["acceptance_criteria_keys"] = [a.get("ac_key") for a in acs if a.get("ac_key")]
            except Exception as exc:  # noqa: BLE001
                logger.warning("[test_data_gen] AC lookup failed for story %s: %s", s.get("id"), exc)
                s["acceptance_criteria_keys"] = []
        # Strip rich nested fields we don't need for seeding — keeps
        # the cache key stable when storyish-but-irrelevant columns
        # (e.g. updated_at) drift.
        return [
            {
                "story_key": s.get("story_key"),
                "title": s.get("title"),
                "acceptance_criteria_keys": s.get("acceptance_criteria_keys", []),
            }
            for s in stories
        ]
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def compute_cache_key(context: SchemaContext) -> str:
    blob = json.dumps(context.to_normalised_dict(), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Seed generator
# ---------------------------------------------------------------------------


# DB-agnostic seed shape. Phase 3 just emits this dict; Phase 5 dispatches
# it to a SQLite / Postgres / Mongo applier based on the deploy blueprint.
#
#   {
#     "tables": {
#       "users": [{"id": 1, "email": "alice@test.local", "_verifies": "STORY-3.AC-2"}, ...],
#       ...
#     }
#   }
#
# The ``_verifies`` key is a teane convention — stripped by the applier
# before INSERT — that lets Phase 4 scenarios cross-reference seed rows
# with the ACs they exercise.


SeedGenerator = Callable[[SchemaContext], dict[str, Any]]


def generate_seed_data(
    context: SchemaContext,
    *,
    generator: Optional[SeedGenerator] = None,
) -> dict[str, Any]:
    """Produce a seed-data dict for ``context``.

    ``generator`` is the pluggable strategy:
      - None (default) → :func:`fallback_seed` — deterministic, offline,
        and intentionally minimal. Useful for tests, dev iteration, and
        as the fail-soft path when no gateway is configured.
      - Caller-provided callable → typically the LLM-backed generator
        wired in Phase 5.

    The result is validated for shape before returning so a buggy
    generator can't poison the fixture file.
    """
    gen = generator or fallback_seed
    seed = gen(context)
    _validate_seed_shape(seed)
    return seed


def fallback_seed(context: SchemaContext) -> dict[str, Any]:
    """Deterministic placeholder seed when no LLM generator is wired.

    Emits a single ``meta`` row carrying the cache key and flow kind so
    downstream consumers can verify the fixture is the one they expect.
    Phase 5 swaps this for an LLM-backed generator.
    """
    return {
        "tables": {
            "_teane_test_meta": [{
                "flow_kind": context.flow_kind,
                "story_count": len(context.stories),
                "spec_files": sorted(context.spec_excerpts.keys()),
            }],
        },
    }


def _validate_seed_shape(seed: dict[str, Any]) -> None:
    if not isinstance(seed, dict):
        raise ValueError("seed must be a dict")
    tables = seed.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("seed['tables'] must be a dict")
    for name, rows in tables.items():
        if not isinstance(name, str) or not name:
            raise ValueError("table names must be non-empty strings")
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError(f"seed table {name!r} must be a list of dicts")


# ---------------------------------------------------------------------------
# Fixture cache
# ---------------------------------------------------------------------------


def write_seed_fixture(
    workspace_path: str,
    seed: dict[str, Any],
    cache_key: str,
    *,
    fixture_dir: Optional[str] = None,
) -> str:
    """Write ``seed`` + cache-key marker. Returns the seed file path."""
    target_dir = _resolve_fixture_dir(workspace_path, fixture_dir)
    os.makedirs(target_dir, exist_ok=True)
    seed_path = os.path.join(target_dir, _DEFAULT_FIXTURE_NAME)
    cache_path = os.path.join(target_dir, _CACHE_KEY_NAME)
    with open(seed_path, "w", encoding="utf-8") as fh:
        json.dump(seed, fh, indent=2, sort_keys=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        fh.write(cache_key)
    logger.info("[test_data_gen] Wrote seed fixture at %s", seed_path)
    return seed_path


def cached_fixture_path(
    workspace_path: str,
    cache_key: str,
    *,
    fixture_dir: Optional[str] = None,
) -> Optional[str]:
    """Return the seed-fixture path if the cache key matches, else None.

    The caller uses this to short-circuit regeneration when the schema
    context hasn't changed since the last run.
    """
    target_dir = _resolve_fixture_dir(workspace_path, fixture_dir)
    seed_path = os.path.join(target_dir, _DEFAULT_FIXTURE_NAME)
    cache_path = os.path.join(target_dir, _CACHE_KEY_NAME)
    if not (os.path.isfile(seed_path) and os.path.isfile(cache_path)):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            stored = fh.read().strip()
    except OSError:
        return None
    return seed_path if stored == cache_key else None


def _resolve_fixture_dir(workspace_path: str, override: Optional[str]) -> str:
    if override is not None:
        return override
    return os.path.join(workspace_path, _DEFAULT_FIXTURE_DIR)


# ---------------------------------------------------------------------------
# SQLite test-DB lifecycle
# ---------------------------------------------------------------------------


def apply_seed_to_sqlite(db_path: str, seed: dict[str, Any]) -> int:
    """Insert ``seed`` rows into a SQLite DB at ``db_path``. Returns row count.

    Creates the parent directory if missing. Tables are created on the
    fly with TEXT columns inferred from the first row of each table —
    sufficient for synthetic test data; Phase 5 will plug in real
    schema-aware DDL once the dispatcher knows the DB kind.

    The ``_verifies`` convention key is stripped before INSERT.
    """
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        inserted = 0
        for table, rows in seed.get("tables", {}).items():
            if not rows:
                continue
            cleaned = [_strip_internal_keys(r) for r in rows]
            cols = sorted({k for r in cleaned for k in r.keys()})
            if not cols:
                continue
            ddl_cols = ", ".join(f"{c} TEXT" for c in cols)
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({ddl_cols})")
            placeholders = ", ".join("?" for _ in cols)
            col_list = ", ".join(cols)
            for row in cleaned:
                values = [
                    json.dumps(row[c]) if isinstance(row.get(c), (dict, list))
                    else (None if c not in row else str(row[c]))
                    for c in cols
                ]
                conn.execute(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                    values,
                )
                inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def reset_sqlite_db(db_path: str) -> int:
    """Drop every user table in ``db_path``. Returns number of tables dropped.

    SQLite-only and intentionally aggressive — ``teane test`` always
    starts from an empty slate so cross-run contamination can't make
    a flaky test look like a real defect. Returns 0 (no-op) when the
    DB file is absent.
    """
    if not os.path.isfile(db_path):
        return 0
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for name in tables:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.commit()
        return len(tables)
    finally:
        conn.close()


def _strip_internal_keys(row: dict[str, Any]) -> dict[str, Any]:
    """Remove teane-convention keys (``_verifies``, ``_source``, etc.)."""
    return {k: v for k, v in row.items() if not k.startswith("_")}

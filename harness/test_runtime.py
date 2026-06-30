"""End-to-end orchestration for ``teane test`` (Phase 5).

Wires Phases 2-4 together against the live dev compose stack:

  1. Detect agile vs waterfall flow kind (test_data_gen.detect_flow_kind)
  2. Discover the base URL by inspecting the running compose stack
  3. Gather scenario + schema context (caching keys keep regen cheap)
  4. Generate Playwright .spec.ts files (cache-aware)
  5. Generate synthetic seed data (cache-aware) and apply to test DB
  6. Install chromium if needed
  7. Run Playwright, capture JSON
  8. Parse → cluster → emit CR-DEFECT-* directories
  9. Persist a per-workspace ``last_test.json`` marker for `teane status`

The subprocess + docker calls are dependency-injected so tests can run
the full pipeline against canned outputs without docker / npx.

Phase 5 ships SQLite-only seeding (matches Phase 3's lifecycle). Phase 6
will harden edge cases (chromium missing, compose down, empty suite).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from harness import (
    playwright_gen,
    test_data_gen,
    test_defects,
)
from harness.flow_state import read_flow_completion

logger = logging.getLogger(__name__)


# Exit codes returned by run_test_pipeline. Mirror the CI-friendly contract
# the design discussion agreed on: 0 = clean, 2 = defects (CI fails loudly),
# 1 = infra problem (chromium missing, compose down, no scenarios, etc.).
EXIT_OK = 0
EXIT_INFRA = 1
EXIT_DEFECTS = 2


_DEFAULT_TEST_DB_REL = os.path.join(".teane", "test_app.db")
_DEFAULT_LAST_TEST_REL = os.path.join(".teane", "last_test.json")


# ---------------------------------------------------------------------------
# Result type — what the pipeline returns to test_node
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Structured outcome of one ``teane test`` invocation."""

    exit_code: int
    passed: int = 0
    failed: int = 0
    cluster_count: int = 0
    cr_paths: list[str] = field(default_factory=list)
    base_url: str = ""
    scope: str = "touched"
    reason: str = ""
    """Empty on success; populated with a short tag (``no_scenarios``,
    ``chromium_missing``, ``runner_failed``) on infra failures."""

    @property
    def total(self) -> int:
        return self.passed + self.failed


# ---------------------------------------------------------------------------
# Base URL discovery
# ---------------------------------------------------------------------------


# Inspector type: async callable taking workspace_path → published base URL.
# Test code substitutes a sync stub via :func:`asyncio.coroutine` wrapping.
BaseUrlInspector = Callable[[str], "asyncio.Future[str]"]


async def discover_base_url(workspace_path: str, *, fallback: str = "http://localhost:3000") -> str:
    """Best-effort: read the deploy's INSTALLATION.md for a published URL.

    Phase 5 keeps this deliberately lightweight — INSTALLATION.md is the
    artefact the deploy flow already writes with the human-facing URL, and
    reading it doesn't require shelling out to ``docker inspect``. A later
    phase can graduate to actual port discovery via compose if needed.
    """
    install = os.path.join(workspace_path, "INSTALLATION.md")
    if not os.path.isfile(install):
        return fallback
    try:
        with open(install, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return fallback
    # Match the first http(s) URL pointing at localhost / 127.* / a docker host.
    import re as _re
    m = _re.search(r"http://(?:localhost|127\.[\d.]+|0\.0\.0\.0)(?::\d+)?(?:/[^\s)]*)?", text)
    if m:
        return m.group(0).rstrip("/.")
    return fallback


# ---------------------------------------------------------------------------
# Playwright runner
# ---------------------------------------------------------------------------


PlaywrightRunner = Callable[[list[str], str], tuple[int, str, str]]
"""(cmd, cwd) → (returncode, stdout, stderr)."""


def run_playwright(
    *,
    e2e_dir: str,
    workspace_path: str,
    retries: int,
    base_url: str,
    runner: Optional[PlaywrightRunner] = None,
) -> tuple[int, Optional[dict[str, Any]], str]:
    """Run ``npx playwright test --reporter=json --retries=N``.

    Returns (returncode, parsed_json_or_None, stderr_excerpt). The JSON
    is None when the runner failed to even produce structured output —
    callers treat that as an infra failure.

    ``runner`` is injectable so tests don't spawn real subprocesses.
    """
    cmd = [
        "npx", "playwright", "test",
        "--reporter=json",
        f"--retries={int(retries)}",
    ]
    env_hint = f"BASE_URL={base_url} "
    logger.info("[test_runtime] %s%s (cwd=%s)", env_hint, " ".join(cmd), e2e_dir)

    runner = runner or _default_playwright_runner
    rc, stdout, stderr = runner(cmd, workspace_path)
    parsed: Optional[dict[str, Any]] = None
    if stdout.strip():
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            logger.warning("[test_runtime] non-JSON Playwright output: %s", exc)
    return rc, parsed, (stderr or "")[-800:]


def _default_reachability_probe(base_url: str, *, timeout: float = 3.0) -> bool:
    """Return True iff ``base_url`` responds at all (any HTTP status is fine —
    we only care that the TCP+HTTP stack is up, not that the request was
    semantically valid)."""
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(base_url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        # Any HTTP response — even 404 or 500 — means the server is up.
        return True
    except (OSError, urllib.error.URLError):
        return False


def _default_playwright_runner(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, cwd=cwd, check=False,
        capture_output=True, text=True,
        env={**os.environ},
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


ReachabilityProbe = Callable[[str], bool]
"""``probe(base_url) -> reachable``. Tests stub with a no-op."""


@dataclass
class PipelineOverrides:
    """Test seams — every external touchpoint is here for stubbing."""

    base_url: Optional[str] = None
    scenario_generator: Optional[playwright_gen.ScenarioGenerator] = None
    seed_generator: Optional[test_data_gen.SeedGenerator] = None
    chromium_runner: Optional[Callable[[list[str]], int]] = None
    playwright_runner: Optional[PlaywrightRunner] = None
    reachability_probe: Optional[ReachabilityProbe] = None
    skip_reachability: bool = False
    now: Optional[datetime] = None


async def run_test_pipeline(
    workspace_path: str,
    *,
    scope: str = "touched",
    retries: int = 2,
    no_cleanup: bool = False,
    overrides: Optional[PipelineOverrides] = None,
) -> PipelineResult:
    """End-to-end pipeline. Pure async so the LangGraph node can await it."""
    overrides = overrides or PipelineOverrides()

    # 1-2. Base URL — explicit override wins, else read INSTALLATION.md.
    base_url = overrides.base_url or await discover_base_url(workspace_path)
    logger.info("[test_runtime] base_url=%s", base_url)

    # 3. Scenario context + cache key.
    scenario_ctx = playwright_gen.gather_scenario_context(workspace_path, base_url=base_url)
    scenario_key = playwright_gen.compute_scenario_cache_key(scenario_ctx)

    # 4. Generate (or reuse cached) scenarios.
    e2e_cached = playwright_gen.cached_scenarios_dir(workspace_path, scenario_key)
    if e2e_cached is None:
        specs = playwright_gen.generate_scenarios(
            scenario_ctx, generator=overrides.scenario_generator,
        )
        if not specs or all(not s.scenarios for s in specs):
            return _infra_result(
                exit_code=EXIT_INFRA, base_url=base_url, scope=scope,
                reason="no_scenarios",
            )
        playwright_gen.write_scenarios(workspace_path, specs, scenario_key)
        logger.info("[test_runtime] wrote %d spec file(s)", len(specs))
    else:
        logger.info("[test_runtime] scenarios cache hit (%s)", scenario_key[:12])

    e2e_dir = playwright_gen._resolve_e2e_dir(workspace_path, None)

    # 5. Schema context + cache key for seed data; apply to test DB.
    schema_ctx = scenario_ctx.schema
    seed_key = test_data_gen.compute_cache_key(schema_ctx)
    cached_seed = test_data_gen.cached_fixture_path(workspace_path, seed_key)
    if cached_seed is None:
        seed = test_data_gen.generate_seed_data(
            schema_ctx, generator=overrides.seed_generator,
        )
        seed_path = test_data_gen.write_seed_fixture(workspace_path, seed, seed_key)
    else:
        seed_path = cached_seed
        with open(seed_path, "r", encoding="utf-8") as fh:
            seed = json.load(fh)
    test_db = os.path.join(workspace_path, _DEFAULT_TEST_DB_REL)
    test_data_gen.reset_sqlite_db(test_db)
    inserted = test_data_gen.apply_seed_to_sqlite(test_db, seed)
    logger.info("[test_runtime] seeded %d rows into %s", inserted, test_db)

    # 6a. Reachability probe — bail fast with a clear reason if the app
    # isn't actually serving (compose-down between deploy and test, port
    # change, etc.). Playwright would otherwise eat a 30s timeout per
    # scenario and the user couldn't tell infra failure from real defect.
    if not overrides.skip_reachability:
        probe = overrides.reachability_probe or _default_reachability_probe
        if not probe(base_url):
            return _infra_result(
                exit_code=EXIT_INFRA, base_url=base_url, scope=scope,
                reason="app_unreachable",
            )

    # 6b. Chromium runtime check.
    if not playwright_gen.ensure_chromium_installed(runner=overrides.chromium_runner):
        return _infra_result(
            exit_code=EXIT_INFRA, base_url=base_url, scope=scope,
            reason="chromium_missing",
        )

    # 7. Run Playwright.
    rc, parsed, stderr_tail = run_playwright(
        e2e_dir=e2e_dir,
        workspace_path=workspace_path,
        retries=retries,
        base_url=base_url,
        runner=overrides.playwright_runner,
    )
    if parsed is None:
        logger.error("[test_runtime] Playwright produced no JSON; rc=%d. stderr tail: %s", rc, stderr_tail)
        return _infra_result(
            exit_code=EXIT_INFRA, base_url=base_url, scope=scope,
            reason="runner_failed",
        )

    # 8. Parse / cluster / emit defect CRs.
    failures = test_defects.parse_playwright_json(parsed)
    clusters = test_defects.cluster_failures(failures)
    passed = _count_passed(parsed)

    cr_paths: list[str] = []
    for cluster in clusters:
        cr_paths.append(test_defects.emit_defect_cr(
            cluster, workspace_path, now=overrides.now,
        ))

    if not no_cleanup:
        test_data_gen.reset_sqlite_db(test_db)

    result = PipelineResult(
        exit_code=EXIT_DEFECTS if failures else EXIT_OK,
        passed=passed,
        failed=len(failures),
        cluster_count=len(clusters),
        cr_paths=cr_paths,
        base_url=base_url,
        scope=scope,
    )

    # 9. Persist last_test marker (used by `teane status` + future patch loop budget).
    _write_last_test_marker(workspace_path, result, scenario_key=scenario_key)
    _maybe_warn_scope_touched(scope, workspace_path)
    return result


def _count_passed(blob: dict[str, Any]) -> int:
    """Count passed results across the suites tree."""
    total = 0

    def _walk(suite: dict[str, Any]) -> None:
        nonlocal total
        for spec in suite.get("specs") or []:
            for test in spec.get("tests") or []:
                for result in test.get("results") or []:
                    if result.get("status") == "passed":
                        total += 1
        for child in suite.get("suites") or []:
            _walk(child)

    for suite in blob.get("suites") or []:
        _walk(suite)
    return total


def _maybe_warn_scope_touched(scope: str, workspace_path: str) -> None:
    """``--scope=touched`` requires CR-attribution from the most recent
    deploy. Phase 5 doesn't filter scenarios yet — log a one-shot warning
    so operators see the limitation rather than silently running everything."""
    if scope != "touched":
        return
    deploy_marker = read_flow_completion(workspace_path, "deploy")
    if deploy_marker is None:
        return
    logger.info(
        "[test_runtime] scope=touched is not yet wired (Phase 5 ships full-suite); "
        "treating as scope=full. Phase 6 will plumb cr_attribution.",
    )


def _infra_result(
    *, exit_code: int, base_url: str, scope: str, reason: str,
) -> PipelineResult:
    return PipelineResult(
        exit_code=exit_code, base_url=base_url, scope=scope, reason=reason,
    )


def _write_last_test_marker(
    workspace_path: str, result: PipelineResult, *, scenario_key: str,
) -> None:
    """Per-workspace marker for `teane status` + observability. Failure to
    write must never change the pipeline's exit code."""
    path = os.path.join(workspace_path, _DEFAULT_LAST_TEST_REL)
    payload = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": result.exit_code,
        "passed": result.passed,
        "failed": result.failed,
        "cluster_count": result.cluster_count,
        "cr_paths": result.cr_paths,
        "base_url": result.base_url,
        "scope": result.scope,
        "reason": result.reason,
        "scenario_cache_key": scenario_key,
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
    except OSError as exc:
        logger.warning("[test_runtime] could not write last_test.json: %s", exc)

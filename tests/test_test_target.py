"""Phase 1 tests for the ``teane test`` target.

Covers:
- flow_state marker round-trip (write on success, no-op on failure)
- check_test_prereqs guard (missing deploy / missing build|patch / clean)
- test_node skeleton — prereq pass → exit_code 0 + reason="not_implemented"
- test_node skeleton — prereq fail → exit_code 1 + reason="prereq_failed"
- CLI subparser registers `teane test` with --scope / --retries / --no-cleanup

Phases 2-6 will add tests for clustering, scenario gen, execution, etc.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from harness import flow_state
from harness.cli import build_parser
from harness.test_target import REASON_PREREQ_FAILED
from harness.test_target import test_node as _test_node_impl


# ---------------------------------------------------------------------------
# flow_state — completion marker round-trip
# ---------------------------------------------------------------------------


def test_record_then_read_round_trip(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    path = flow_state.record_flow_completion(
        workspace_path=workspace,
        flow="deploy",
        session_id="sess-1",
        exit_code=0,
        summary={"modified_files": 7},
    )
    assert path is not None
    assert os.path.isfile(path)

    data = flow_state.read_flow_completion(workspace, "deploy")
    assert data is not None
    assert data["flow"] == "deploy"
    assert data["session_id"] == "sess-1"
    assert data["exit_code"] == 0
    assert data["summary"] == {"modified_files": 7}
    assert "completed_at" in data


def test_record_skipped_on_failure(tmp_path: Path) -> None:
    """exit_code != 0 → no marker written; absence == failure."""
    workspace = str(tmp_path)
    result = flow_state.record_flow_completion(
        workspace_path=workspace,
        flow="build",
        session_id="sess-x",
        exit_code=1,
    )
    assert result is None
    assert flow_state.read_flow_completion(workspace, "build") is None


def test_record_skipped_for_untracked_flow(tmp_path: Path) -> None:
    """Only build/patch/deploy are tracked. 'test' itself doesn't self-mark."""
    result = flow_state.record_flow_completion(
        workspace_path=str(tmp_path),
        flow="test",
        session_id="sess-y",
        exit_code=0,
    )
    assert result is None


def test_read_returns_none_for_missing(tmp_path: Path) -> None:
    assert flow_state.read_flow_completion(str(tmp_path), "deploy") is None


def test_read_returns_none_for_corrupt(tmp_path: Path) -> None:
    teane_dir = tmp_path / ".teane"
    teane_dir.mkdir()
    (teane_dir / "last_deploy.json").write_text("{not-json", encoding="utf-8")
    assert flow_state.read_flow_completion(str(tmp_path), "deploy") is None


# ---------------------------------------------------------------------------
# flow_state — check_test_prereqs gate
# ---------------------------------------------------------------------------


def _mark(tmp_path: Path, flow: str) -> None:
    """Write a synthetic clean-completion marker."""
    flow_state.record_flow_completion(
        workspace_path=str(tmp_path),
        flow=flow,
        session_id=f"sess-{flow}",
        exit_code=0,
    )


def test_prereq_fails_when_no_markers(tmp_path: Path) -> None:
    ok, reason = flow_state.check_test_prereqs(str(tmp_path))
    assert not ok
    assert "deploy" in reason


def test_prereq_fails_when_only_build(tmp_path: Path) -> None:
    _mark(tmp_path, "build")
    ok, reason = flow_state.check_test_prereqs(str(tmp_path))
    assert not ok
    assert "deploy" in reason


def test_prereq_fails_when_only_deploy(tmp_path: Path) -> None:
    _mark(tmp_path, "deploy")
    ok, reason = flow_state.check_test_prereqs(str(tmp_path))
    assert not ok
    assert "build" in reason or "patch" in reason


def test_prereq_passes_with_build_and_deploy(tmp_path: Path) -> None:
    _mark(tmp_path, "build")
    _mark(tmp_path, "deploy")
    ok, reason = flow_state.check_test_prereqs(str(tmp_path))
    assert ok
    assert reason == "ok"


def test_prereq_passes_with_patch_and_deploy(tmp_path: Path) -> None:
    _mark(tmp_path, "patch")
    _mark(tmp_path, "deploy")
    ok, _ = flow_state.check_test_prereqs(str(tmp_path))
    assert ok


# ---------------------------------------------------------------------------
# test_node — skeleton behaviour
# ---------------------------------------------------------------------------


def test_test_node_blocks_when_prereq_missing(tmp_path: Path) -> None:
    state = {"workspace_path": str(tmp_path)}
    result = asyncio.run(_test_node_impl(state))
    assert result["exit_code"] == 1
    test_state = result["node_state"]["test"]
    assert test_state["skipped"] is True
    assert test_state["reason"] == REASON_PREREQ_FAILED
    assert "deploy" in test_state["detail"]


def test_test_node_runs_pipeline_when_prereqs_met(tmp_path: Path) -> None:
    """Phase 5: with prereqs met but no spec files in the workspace, the
    fallback generator produces a minimal smoke spec, but with no
    chromium / playwright available (sandboxed test env) the pipeline
    reports an infra failure rather than a defect — which is exactly
    the right signal for `teane status` to surface."""
    from harness import test_runtime
    _mark(tmp_path, "build")
    _mark(tmp_path, "deploy")
    # Override the chromium runner so we don't try to actually install
    # browsers — and stub the playwright runner so we don't shell out.
    overrides = test_runtime.PipelineOverrides(
        base_url="http://app:3000",
        chromium_runner=lambda cmd: 0,  # pretend install succeeded
        skip_reachability=True,  # no real network in this test
        playwright_runner=lambda cmd, cwd: (
            0,
            '{"suites": [{"specs": [{"file": "x.spec.ts", '
            '"tests": [{"title": "t", "results": [{"status": "passed", "duration": 1}]}]}]}]}',
            "",
        ),
    )
    state = {
        "workspace_path": str(tmp_path),
        "test_overrides": overrides,
    }
    result = asyncio.run(_test_node_impl(state))
    assert result["exit_code"] == 0
    test_state = result["node_state"]["test"]
    assert test_state["skipped"] is False
    assert test_state["reason"] == "ok"
    assert test_state["passed"] == 1
    assert test_state["failed"] == 0


# ---------------------------------------------------------------------------
# CLI — subparser registration and arg defaults
# ---------------------------------------------------------------------------


def test_test_subcommand_registered() -> None:
    parser = build_parser()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            assert "test" in action.choices
            return
    raise AssertionError("subparsers action not found")


def test_test_subcommand_parses_with_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["test", "-w", "/tmp/x", "-p", "verify it works"])
    assert args.command == "test"
    assert args.scope == "touched"
    assert args.retries == 2
    assert args.no_cleanup is False


def test_test_subcommand_parses_all_flags() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "test", "-w", "/tmp/x", "-p", "verify",
        "--scope", "full", "--retries", "0", "--no-cleanup",
    ])
    assert args.scope == "full"
    assert args.retries == 0
    assert args.no_cleanup is True


def test_test_subcommand_rejects_invalid_scope() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "test", "-w", "/tmp/x", "-p", "verify", "--scope", "invalid",
        ])

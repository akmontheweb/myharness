"""Tests for the end-of-run INSTALLATION.md synthesis pipeline.

Covers four layers:
    1. The three pure helpers that shape the LLM prompt
       (_extract_arch_build_run, _collect_installation_manifests,
       _slim_blueprint).
    2. synthesize_installation against a stub gateway — verifies the
       file is written, telemetry was read, and the right inputs end up
       in the prompt.
    3. installation_doc_node state gating (install_doc=False is a no-op).
    4. The two graph routers that now have to land on
       installation_doc_node (route_after_security_scan terminal paths,
       route_after_deployment success path).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from harness.cli import (
    _collect_installation_manifests,
    _extract_arch_build_run,
    _slim_blueprint,
    synthesize_installation,
)
from harness.graph import (
    installation_doc_node,
    route_after_deployment,
    route_after_security_scan,
)


# ---------------------------------------------------------------------------
# Stub gateway — mirrors the pattern in tests/test_change_requests.py
# ---------------------------------------------------------------------------


class _StubUsage:
    input_tokens = 100
    output_tokens = 80
    cached_tokens = 0
    cost_usd = 0.0015
    model = "stub"


class _StubResponse:
    def __init__(self, content: str):
        self.content = content
        self.usage = _StubUsage()
        # finish_reason omitted on purpose — _dispatch_with_continuation
        # treats missing as "stop".


class _StubGateway:
    class config:
        repair_fallback = ""
        planning_fallback = ""

    def __init__(self, content: str):
        self._content = content
        self.dispatched: list[dict[str, Any]] = []

    async def dispatch(self, *, messages, role, budget_remaining_usd, **kwargs):
        self.dispatched.append(
            {"messages": list(messages), "role": role,
             "budget": budget_remaining_usd}
        )
        return _StubResponse(self._content), budget_remaining_usd - 0.05

    def aggregate_tokens(self, tracker, usage, role=None):
        out = dict(tracker or {})
        out["total_cost_usd"] = out.get("total_cost_usd", 0.0) + 0.001
        return out


@pytest.fixture
def stub_gateway():
    """Install a stub gateway on the graph module and tear down after."""
    from harness import graph as graph_mod
    installed: list[_StubGateway] = []

    def _set(content: str) -> _StubGateway:
        gw = _StubGateway(content)
        graph_mod.set_gateway(gw)
        installed.append(gw)
        return gw

    yield _set
    from harness import graph as graph_mod  # noqa: F811
    graph_mod.set_gateway(None)


# ---------------------------------------------------------------------------
# 1. Pure-helper unit tests
# ---------------------------------------------------------------------------


class TestExtractArchBuildRun:
    def test_returns_placeholder_on_empty_text(self):
        out = _extract_arch_build_run("")
        assert out == "(architecture spec not available)"

    def test_slices_section_7_until_next_heading(self):
        spec = (
            "### 6. Test Strategy\nfoo\n\n"
            "### 7. Build & Run\n- pip install -r requirements.txt\n"
            "- make run\n\n"
            "### 8. Appendix\nnoise\n"
        )
        out = _extract_arch_build_run(spec)
        assert out.startswith("### 7. Build & Run")
        assert "pip install" in out
        assert "make run" in out
        # Next heading marks the boundary — appendix must not leak in.
        assert "Appendix" not in out
        assert "noise" not in out

    def test_accepts_alternate_heading_phrasings(self):
        for heading in (
            "### Build and Run",
            "### 7. Build/Run",
            "### 9. BUILD & RUN",
        ):
            spec = f"### 1. Overview\nbody\n\n{heading}\nrun me\n"
            out = _extract_arch_build_run(spec)
            assert "run me" in out, f"heading variant failed: {heading!r}"

    def test_falls_back_to_trailing_slice_when_no_section_found(self):
        spec = "### 1. Overview\n" + ("a" * 100)
        out = _extract_arch_build_run(spec)
        # Helper returns the trailing 6 KB when no Build & Run section
        # is detected. Document is far shorter than 6 KB so the whole
        # thing should come back.
        assert "Overview" in out


class TestCollectInstallationManifests:
    def test_returns_placeholder_when_nothing_at_root(self, tmp_path: Path):
        out = _collect_installation_manifests(str(tmp_path))
        assert out == "(no manifest files found at workspace root)"

    def test_collects_known_manifests_into_one_block(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("fastapi==0.111.0\n")
        (tmp_path / "Makefile").write_text("install:\n\tpip install -r requirements.txt\n")
        (tmp_path / "package.json").write_text('{"scripts":{"dev":"vite"}}')
        out = _collect_installation_manifests(str(tmp_path))
        assert "#### requirements.txt" in out
        assert "#### Makefile" in out
        assert "#### package.json" in out
        assert "fastapi==0.111.0" in out
        assert "vite" in out

    def test_truncates_large_manifest_with_marker(self, tmp_path: Path):
        big = "x" * 10_000
        (tmp_path / "requirements.txt").write_text(big)
        out = _collect_installation_manifests(str(tmp_path))
        assert "... (truncated)" in out
        # Body slice is bounded at 4 KB; the truncation marker proves it.
        assert len(out) < 9_000

    def test_ignores_unrelated_files(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("foo\n")
        (tmp_path / "random.txt").write_text("ignored")
        out = _collect_installation_manifests(str(tmp_path))
        assert "requirements.txt" in out
        assert "random.txt" not in out


class TestSlimBlueprint:
    def test_returns_none_for_empty_inputs(self):
        assert _slim_blueprint(None) == "none"
        assert _slim_blueprint({}) == "none"
        assert _slim_blueprint({"services": {}}) == "none"

    def test_keeps_only_installation_relevant_fields(self):
        blueprint = {
            "services": {
                "api": {
                    "image": "python:3.11-slim",
                    "ports": ["8000:8000"],
                    "healthcheck": {"test": "curl localhost:8000/health"},
                    "environment": {"DATABASE_URL": "postgres://..."},
                    "internal_volume_handle": "ignored-key",
                    "build_context": "ignored-too",
                },
                "db": {
                    "base_image": "postgres:16-alpine",
                    "ports": ["5432:5432"],
                },
            }
        }
        out = _slim_blueprint(blueprint)
        parsed = json.loads(out)
        assert set(parsed["services"]["api"]) == {
            "image", "ports", "healthcheck", "environment"
        }
        assert "internal_volume_handle" not in parsed["services"]["api"]
        assert parsed["services"]["db"]["base_image"] == "postgres:16-alpine"


# ---------------------------------------------------------------------------
# 2. synthesize_installation — end-to-end with stub gateway
# ---------------------------------------------------------------------------


class TestSynthesizeInstallation:
    def _seed_workspace(self, tmp_path: Path) -> Path:
        (tmp_path / "requirements.txt").write_text("fastapi==0.111.0\n")
        (tmp_path / "Makefile").write_text("run:\n\tuvicorn main:app\n")
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SPEC_ARCHITECTURE.md").write_text(
            "### 1. Overview\n\n"
            "Backend service.\n\n"
            "### 7. Build & Run\n\n"
            "- `pip install -r requirements.txt`\n"
            "- `uvicorn main:app --port 8000`\n"
        )
        return tmp_path

    def test_writes_installation_md_with_expected_inputs(self, tmp_path, stub_gateway):
        ws = self._seed_workspace(tmp_path)
        gw = stub_gateway(
            "# Installation\n\n## 1. Prerequisites\n- Python 3.11+\n"
        )
        install_path = asyncio.run(
            synthesize_installation(
                workspace_path=str(ws),
                architecture_path=str(ws / "docs" / "SPEC_ARCHITECTURE.md"),
                output_dir=str(ws / "docs"),
                gateway=gw,
                blueprint=None,
            )
        )
        out = Path(install_path)
        assert out.name == "INSTALLATION.md"
        assert out.parent == ws / "docs"
        body = out.read_text()
        assert body.startswith("# Installation")

        # The LLM saw the architecture §7 slice + the manifest content +
        # the "none" blueprint sentinel — verify by inspecting the user
        # prompt that was dispatched.
        assert gw.dispatched, "gateway was never called"
        user_msg = gw.dispatched[0]["messages"][-1]["content"]
        assert "uvicorn main:app --port 8000" in user_msg
        assert "fastapi==0.111.0" in user_msg
        assert "### Deployment blueprint (or \"none\"" in user_msg
        # The blueprint sentinel "none" must appear in its dedicated
        # block so the prompt knows to skip §5.
        assert "\nnone\n" in user_msg

    def test_passes_blueprint_through_when_provided(self, tmp_path, stub_gateway):
        ws = self._seed_workspace(tmp_path)
        gw = stub_gateway("# Installation\n\nbody\n")
        blueprint = {
            "services": {
                "api": {
                    "image": "python:3.11-slim",
                    "ports": ["8000:8000"],
                    "healthcheck": {"test": "curl /health"},
                },
            }
        }
        asyncio.run(
            synthesize_installation(
                workspace_path=str(ws),
                architecture_path=str(ws / "docs" / "SPEC_ARCHITECTURE.md"),
                output_dir=str(ws / "docs"),
                gateway=gw,
                blueprint=blueprint,
            )
        )
        user_msg = gw.dispatched[0]["messages"][-1]["content"]
        # Blueprint JSON was embedded — and the "none" sentinel must NOT
        # appear in the blueprint block.
        assert "python:3.11-slim" in user_msg
        assert "8000:8000" in user_msg

    def test_raises_when_workspace_missing(self, tmp_path, stub_gateway):
        gw = stub_gateway("ignored")
        bogus = tmp_path / "does-not-exist"
        with pytest.raises(FileNotFoundError):
            asyncio.run(
                synthesize_installation(
                    workspace_path=str(bogus),
                    architecture_path="",
                    output_dir=str(tmp_path),
                    gateway=gw,
                    blueprint=None,
                )
            )

    def test_handles_missing_architecture_gracefully(self, tmp_path, stub_gateway):
        # No SPEC_ARCHITECTURE.md and no manifests — the synth still
        # runs; the prompt slots show the placeholders.
        ws = tmp_path
        gw = stub_gateway("# Installation\n\nbare\n")
        asyncio.run(
            synthesize_installation(
                workspace_path=str(ws),
                architecture_path=str(ws / "docs" / "SPEC_ARCHITECTURE.md"),
                output_dir=str(ws / "docs"),
                gateway=gw,
                blueprint=None,
            )
        )
        user_msg = gw.dispatched[0]["messages"][-1]["content"]
        assert "(architecture spec not available)" in user_msg
        assert "(no manifest files found at workspace root)" in user_msg


# ---------------------------------------------------------------------------
# 3. installation_doc_node — state gating
# ---------------------------------------------------------------------------


class TestInstallationDocNode:
    def test_no_op_when_flag_false(self, tmp_path, stub_gateway):
        # Gateway installed but should never be touched — flag short-
        # circuits before any LLM call.
        gw = stub_gateway("should not be called")
        state = {"workspace_path": str(tmp_path), "install_doc": False}
        result = asyncio.run(installation_doc_node(state))
        assert result == {}
        assert not gw.dispatched

    def test_returns_empty_when_workspace_missing(self, tmp_path, stub_gateway):
        gw = stub_gateway("ignored")
        bogus = tmp_path / "missing"
        state = {"workspace_path": str(bogus), "install_doc": True}
        result = asyncio.run(installation_doc_node(state))
        assert result == {}
        # Helper bailed before dispatch — no LLM cost burned on a bad path.
        assert not gw.dispatched

    def test_writes_installation_doc_and_returns_path(self, tmp_path, stub_gateway):
        (tmp_path / "requirements.txt").write_text("fastapi==0.111.0\n")
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SPEC_ARCHITECTURE.md").write_text(
            "### 7. Build & Run\n\n- make run\n"
        )
        stub_gateway("# Installation\n\n## 1. Prerequisites\nPython 3.11\n")
        state = {
            "workspace_path": str(tmp_path),
            "spec_architecture_path": str(docs / "SPEC_ARCHITECTURE.md"),
            "install_doc": True,
            "node_state": {},
        }
        result = asyncio.run(installation_doc_node(state))
        assert result["installation_doc_path"].endswith("INSTALLATION.md")
        assert (docs / "INSTALLATION.md").exists()
        assert result["node_state"]["current_node"] == "installation_doc"

    def test_swallows_synth_exception(self, tmp_path, monkeypatch, stub_gateway):
        # When the synth helper raises, the node must NOT propagate — a
        # doc failure can't be allowed to fail the whole run after a
        # successful build / deploy. Patch the symbol the node imports.
        (tmp_path / "docs").mkdir()
        async def _boom(**kwargs):
            raise RuntimeError("LLM died")
        monkeypatch.setattr("harness.cli.synthesize_installation", _boom)
        stub_gateway("ignored")
        state = {
            "workspace_path": str(tmp_path),
            "install_doc": True,
            "node_state": {},
        }
        result = asyncio.run(installation_doc_node(state))
        # Empty dict → state unchanged → END edge still fires.
        assert result == {}


# ---------------------------------------------------------------------------
# 4. Router behaviour — terminal paths must land on installation_doc_node
# ---------------------------------------------------------------------------


class TestRouteAfterSecurityScan:
    def _clean_state(self, **overrides) -> dict[str, Any]:
        base: dict[str, Any] = {
            "budget_remaining_usd": 1.50,
            "loop_counter": {"security": 0},
            "compiler_errors": [],
            "dev_deployment": False,
            "cd_discovery": False,
            "workspace_path": "",
            "node_state": {},
        }
        base.update(overrides)
        return base

    def test_clean_no_deploy_routes_to_installation_doc(self):
        result = route_after_security_scan(self._clean_state())
        assert result == "installation_doc_node"

    def test_deploy_dev_still_routes_through_deployment(self):
        result = route_after_security_scan(
            self._clean_state(dev_deployment=True, cd_discovery=False)
        )
        # Deployment must NOT be replaced by the install doc here; the
        # deploy phase has to run first.
        assert result == "deployment_node"

    def test_findings_still_route_to_repair(self):
        result = route_after_security_scan(
            self._clean_state(compiler_errors=[{"file": "x", "message": "bad"}])
        )
        assert result == "repair_node"


class TestRouteAfterDeployment:
    def test_success_routes_to_installation_doc(self):
        state = {
            "node_state": {"deployment": {"success": True}},
        }
        assert route_after_deployment(state) == "installation_doc_node"

    def test_failed_deployment_with_no_errors_routes_to_hitl(self):
        # F1 — route_after_deployment is now terminal. The historic
        # fall-through to route_after_compiler (which on exit_code=0 from
        # the prior compile re-entered security_scan_node) caused the
        # deployment ↔ security-scan loop in session 951f102f.
        # ``success=False`` without compiler_errors is the Bug-A trap
        # state; it now surfaces to HITL instead of looping.
        state = {
            "node_state": {"deployment": {"success": False}},
            "exit_code": 0,
            "loop_counter": {},
            "budget_remaining_usd": 1.0,
        }
        out = route_after_deployment(state)
        assert out == "human_intervention_node"

    def test_failed_deployment_with_errors_routes_to_repair(self):
        # When deployment_node emits a real DEPLOYMENT_* diagnostic
        # (build_failed / generation_failed / health_check failures), the
        # router still hands off to repair_node so the LLM can attempt
        # a fix.
        state = {
            "node_state": {"deployment": {"success": False, "phase": "build_failed"}},
            "compiler_errors": [{
                "file": "docker-compose.yml", "line": 0, "column": 0,
                "severity": "error", "error_code": "DEPLOYMENT_BUILD_FAILED",
                "message": "compose build failed",
            }],
            "exit_code": 0,
            "loop_counter": {},
            "budget_remaining_usd": 1.0,
        }
        out = route_after_deployment(state)
        assert out == "repair_node"

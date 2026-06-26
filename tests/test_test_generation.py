"""Tests for harness/test_generation.py — the auto test-generation + deterministic
sandbox execution node wired between speculative_node and lintgate_node.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.test_generation import (
    _PRIMARY_STACK_PRIORITY,
    _STACK_TEST_COMMANDS,
    _is_test_file,
    _pick_primary_stack,
    _stack_test_command,
    _inside_workspace,
    route_after_test_generation,
)
# Imported under a non-test_ alias so pytest's auto-collection doesn't try
# to invoke this graph node as if it were a test function.
from harness.test_generation import test_generation_node as run_test_generation


# ---------------------------------------------------------------------------
# Helpers — stubs for the gateway + sandbox so the node runs without touching
# the network or spinning up docker.
# ---------------------------------------------------------------------------

class _StubUsage:
    input_tokens = 100
    output_tokens = 80
    cached_tokens = 0
    cost_usd = 0.001
    model = "stub"


class _StubResponse:
    def __init__(self, content: str):
        self.content = content
        self.usage = _StubUsage()


class _StubGateway:
    """Records every dispatch + returns canned patch content."""

    def __init__(self, content: str):
        self._content = content
        self.dispatched = []

    class config:
        repair_fallback = ""
        planning_fallback = ""

    async def dispatch(self, *, messages, role, budget_remaining_usd, **kwargs):
        self.dispatched.append({"messages": list(messages), "role": role})
        return _StubResponse(self._content), budget_remaining_usd - 0.001

    def aggregate_tokens(self, tracker, usage, role=None):
        out = dict(tracker or {})
        out["total_cost_usd"] = out.get("total_cost_usd", 0.0) + float(usage.cost_usd)
        return out


class _StubBuildResult:
    def __init__(self, exit_code: int, raw_output: str):
        self.exit_code = exit_code
        self.raw_output = raw_output
        self.diagnostics = []
        self.elapsed_seconds = 0.01
        self.timed_out = False
        self.log_truncated = False


class _StubSandboxExecutor:
    """Records every run() invocation and returns a pre-canned BuildResult."""
    last_command: str = ""
    canned: _StubBuildResult = _StubBuildResult(0, "")

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def run(self, build_command: str):
        _StubSandboxExecutor.last_command = build_command
        return _StubSandboxExecutor.canned


@pytest.fixture
def stub_sandbox(monkeypatch):
    """Replace harness.sandbox.SandboxExecutor with the stub above and return
    a setter the test can use to pre-can the build result."""
    import harness.sandbox as sandbox_mod
    monkeypatch.setattr(sandbox_mod, "SandboxExecutor", _StubSandboxExecutor)

    def _set(exit_code: int, raw_output: str = "") -> None:
        _StubSandboxExecutor.canned = _StubBuildResult(exit_code, raw_output)
        _StubSandboxExecutor.last_command = ""

    _set(0, "")  # default to passing sandbox
    return _set


@pytest.fixture
def stub_gateway(monkeypatch):
    """Install a stub LLM gateway for the duration of the test."""
    from harness import graph as graph_mod

    holder: dict[str, _StubGateway] = {}

    def _set(content: str) -> _StubGateway:
        gw = _StubGateway(content)
        graph_mod.set_gateway(gw)
        holder["gw"] = gw
        return gw

    yield _set

    graph_mod.set_gateway(None)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_is_test_file_python(self):
        assert _is_test_file("tests/test_foo.py") is True
        assert _is_test_file("src/foo.py") is False

    def test_is_test_file_javascript(self):
        assert _is_test_file("src/foo.test.ts") is True
        assert _is_test_file("__tests__/foo.spec.js") is True
        assert _is_test_file("src/foo.ts") is False

    def test_is_test_file_java(self):
        assert _is_test_file("src/test/java/com/x/FooTest.java") is True
        assert _is_test_file("src/main/java/com/x/Foo.java") is False

    def test_pick_primary_stack_prefers_specific_over_generic(self):
        # typescript should win over node when both are present
        assert _pick_primary_stack({"node", "typescript"}) == "typescript"
        assert _pick_primary_stack({"python", "node"}) in ("node", "python")

    def test_pick_primary_stack_none_when_unknown(self):
        assert _pick_primary_stack({"cobol"}) is None
        assert _pick_primary_stack(set()) is None

    def test_stack_test_command_runs_pytest_for_python(self):
        # pytest is pre-baked into the builder image so the per-run install
        # step is gone — the command is now just the pytest invocation.
        cmd = _stack_test_command("python")
        assert cmd is not None
        assert "pytest" in cmd
        # Regression: the legacy `pip install -q pytest && ...` prefix must
        # NOT come back; that round-tripped to PyPI on every single test run.
        assert "pip install" not in cmd

    def test_stack_test_command_runs_jest_for_javascript(self):
        # jest is also pre-baked; `npx --no-install` resolves it from PATH.
        cmd = _stack_test_command("javascript")
        assert cmd is not None
        assert "jest" in cmd
        assert "npm install" not in cmd

    def test_every_priority_stack_has_a_test_command(self):
        # If we add a new stack to the priority list we must also add its
        # test command, otherwise the node silently skips the deterministic
        # run. Catch the omission in CI.
        for tag in _PRIMARY_STACK_PRIORITY:
            assert tag in _STACK_TEST_COMMANDS, (
                f"stack {tag!r} in _PRIMARY_STACK_PRIORITY but missing from "
                f"_STACK_TEST_COMMANDS"
            )

    def test_inside_workspace_accepts_relative(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("")
        assert _inside_workspace("tests/test_x.py", str(tmp_path)) is True

    def test_inside_workspace_rejects_absolute(self, tmp_path):
        assert _inside_workspace("/etc/passwd", str(tmp_path)) is False

    def test_inside_workspace_rejects_traversal(self, tmp_path):
        assert _inside_workspace("../outside.py", str(tmp_path)) is False


# ---------------------------------------------------------------------------
# Skip / gate behaviours
# ---------------------------------------------------------------------------

class TestSkipBehaviour:

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, tmp_path, stub_sandbox, stub_gateway):
        # enabled: false → no work, no LLM call
        gw = stub_gateway("")
        result = await run_test_generation({
            "workspace_path": str(tmp_path),
            "modified_files": ["foo.py"],
            "test_generation_config": {"enabled": False},
        })
        assert result == {}
        assert gw.dispatched == []

    @pytest.mark.asyncio
    async def test_skips_when_modified_files_empty(self, tmp_path, stub_sandbox, stub_gateway):
        gw = stub_gateway("")
        result = await run_test_generation({
            "workspace_path": str(tmp_path),
            "modified_files": [],
        })
        assert result == {}
        assert gw.dispatched == []

    @pytest.mark.asyncio
    async def test_skips_when_only_test_files_modified(
        self, tmp_path, stub_sandbox, stub_gateway,
    ):
        # modified files are themselves tests → nothing to do
        gw = stub_gateway("")
        result = await run_test_generation({
            "workspace_path": str(tmp_path),
            "modified_files": ["tests/test_foo.py", "src/foo.test.ts"],
        })
        assert result == {}
        assert gw.dispatched == []

    @pytest.mark.asyncio
    async def test_skips_when_no_supported_stack(
        self, tmp_path, stub_sandbox, stub_gateway,
    ):
        gw = stub_gateway("")
        # Unknown extension → no stack tag inferred
        (tmp_path / "x.cobol").write_text("DISPLAY 'hi'.\n")
        result = await run_test_generation({
            "workspace_path": str(tmp_path),
            "modified_files": ["x.cobol"],
        })
        assert result == {}
        assert gw.dispatched == []

    @pytest.mark.asyncio
    async def test_routes_to_hitl_when_no_gateway(self, tmp_path, stub_sandbox):
        # gateway is None → env_misconfig diagnostic + route to HITL
        from harness import graph as graph_mod
        graph_mod.set_gateway(None)

        (tmp_path / "foo.py").write_text("def x(): return 1\n")
        result = await run_test_generation({
            "workspace_path": str(tmp_path),
            "modified_files": ["foo.py"],
        })
        assert result["node_state"]["env_misconfig"] is True
        assert result["node_state"]["env_misconfig_symbol"] == "llm_api_key"
        # The synthetic diagnostic must spell out the fix the operator needs.
        msg = result["compiler_errors"][0]["message"]
        assert "LLM API key" in msg
        assert "ANTHROPIC_API_KEY" in msg
        # And the router must send it to HITL.
        assert route_after_test_generation(result) == "human_intervention_node"


# ---------------------------------------------------------------------------
# Happy path: LLM emits a CREATE_FILE block, sandbox passes
# ---------------------------------------------------------------------------

class TestHappyPath:

    @pytest.mark.asyncio
    async def test_python_writes_test_runs_pytest_routes_to_lintgate(
        self, tmp_path, stub_sandbox, stub_gateway,
    ):
        # Workspace with a Python source file
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "calculator.py").write_text(
            "def divide(a, b):\n"
            "    if b == 0:\n"
            "        raise ZeroDivisionError('cannot divide by zero')\n"
            "    return a // b\n"
        )

        # Stub the LLM to return one CREATE_FILE block for a test file
        gw = stub_gateway(
            "<<<CREATE_FILE>>>\n"
            "file: tests/test_calculator.py\n"
            "content:\n"
            "from calculator import divide\n"
            "def test_divide():\n"
            "    assert divide(10, 2) == 5\n"
            "<<<END_CREATE_FILE>>>\n"
        )

        result = await run_test_generation({
            "workspace_path": str(tmp_path),
            "modified_files": ["calculator.py"],
            "messages": [],
            "budget_remaining_usd": 1.5,
            "token_tracker": {},
        })

        # 1. LLM was called exactly once
        assert len(gw.dispatched) == 1
        sent = gw.dispatched[0]["messages"]
        # 2. The Python test guide was injected into the system messages
        guide_sys = [m for m in sent if m.get("role") == "system" and "monkeypatch" in m.get("content", "")]
        assert guide_sys, "system prompt should include the python test guide"
        # 3. The prompt explicitly forbids mocks
        user_msgs = [m for m in sent if m.get("role") == "user"]
        joined_user = "\n".join(m.get("content", "") for m in user_msgs)
        assert "Do NOT generate mocks" in joined_user or "do not generate mocks" in joined_user.lower()
        # 4. The deterministic test command ran in the sandbox. pytest is
        # pre-baked into the builder image so there's no install prefix —
        # just the bare pytest invocation.
        assert "pytest" in _StubSandboxExecutor.last_command
        assert "pip install" not in _StubSandboxExecutor.last_command
        # 5. The result reports a pass and lists the generated test
        assert result["node_state"]["test_generation"]["status"] == "passed"
        assert result["generated_tests"] == ["tests/test_calculator.py"]
        # 6. The router would proceed to lintgate
        assert route_after_test_generation(result) == "lintgate_node"
        # 7. The test file landed inside the workspace, not anywhere else
        assert (tmp_path / "tests" / "test_calculator.py").is_file()

    @pytest.mark.asyncio
    async def test_no_tests_generated_skips_sandbox_call(
        self, tmp_path, stub_sandbox, stub_gateway,
    ):
        # LLM returns nothing parseable → 0 generated tests → skip sandbox
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "foo.py").write_text("def foo(): pass\n")
        stub_gateway("no patch blocks here, just prose")
        stub_sandbox(99, "this should not be observed because sandbox should not run")

        result = await run_test_generation({
            "workspace_path": str(tmp_path),
            "modified_files": ["foo.py"],
            "messages": [],
            "budget_remaining_usd": 1.5,
            "token_tracker": {},
        })
        # No deterministic run happened
        assert _StubSandboxExecutor.last_command == ""
        # Status is a pass with the no_tests_generated reason
        assert result["node_state"]["test_generation"]["status"] == "passed"
        assert result["node_state"]["test_generation"]["reason"] == "no_tests_generated"
        assert route_after_test_generation(result) == "lintgate_node"


# ---------------------------------------------------------------------------
# Failure path: sandbox exits non-zero → repair_node
# ---------------------------------------------------------------------------

class TestFailurePath:

    @pytest.mark.asyncio
    async def test_test_failure_routes_to_repair_with_test_failure_code(
        self, tmp_path, stub_sandbox, stub_gateway,
    ):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "foo.py").write_text("def foo(): return 1\n")
        stub_gateway(
            "<<<CREATE_FILE>>>\n"
            "file: tests/test_foo.py\n"
            "content:\n"
            "from foo import foo\n"
            "def test_foo(): assert foo() == 2  # wrong on purpose\n"
            "<<<END_CREATE_FILE>>>\n"
        )
        stub_sandbox(1, "tests/test_foo.py:2: assert 1 == 2\nFAILED tests/test_foo.py::test_foo")

        result = await run_test_generation({
            "workspace_path": str(tmp_path),
            "modified_files": ["foo.py"],
            "messages": [],
            "budget_remaining_usd": 1.5,
            "token_tracker": {},
        })

        assert result["node_state"]["test_generation"]["status"] == "failed"
        assert result["compiler_errors"], "must populate compiler_errors on failure"
        # Every diagnostic must carry the TEST_FAILURE prefix so repair_node's
        # framing tweak knows these came from the test runner.
        codes = [d["error_code"] for d in result["compiler_errors"]]
        assert all(c.upper().startswith("TEST_FAILURE") for c in codes), codes
        # Router sends to repair.
        assert route_after_test_generation(result) == "repair_node"


# ---------------------------------------------------------------------------
# Graph wiring smoke test
# ---------------------------------------------------------------------------

class TestGraphWiring:

    def test_graph_includes_test_generation_node(self):
        # The build_graph must register the new node and the edge from
        # speculative_node to it. We don't execute the graph here, just
        # confirm the wiring via the graph's compiled structure.
        from harness.graph import build_graph
        try:
            g = build_graph(checkpointer=None)
        except Exception as exc:
            pytest.skip(f"build_graph requires extra deps in this env: {exc}")
        # LangGraph compiled graph exposes node names via .nodes
        node_names = set(g.nodes.keys()) if hasattr(g, "nodes") else set()
        # build_graph returns a compiled graph; the source StateGraph has
        # different introspection. Either path is acceptable — just confirm
        # the node name appears somewhere in repr.
        graph_repr = repr(g)
        assert (
            "test_generation_node" in node_names
            or "test_generation_node" in graph_repr
        )


# ---------------------------------------------------------------------------
# repair_node framing for TEST_FAILURE diagnostics
# ---------------------------------------------------------------------------

class TestRepairFraming:

    @pytest.mark.asyncio
    async def test_repair_node_uses_test_failure_framing(self, tmp_path):
        # Feed repair_node a state whose only diagnostic carries TEST_FAILURE.
        # The prompt sent to the LLM must contain the new framing sentence,
        # NOT the generic "build failed" framing or the security framing.
        from harness import graph as graph_mod

        captured: dict[str, Any] = {}

        class StubResp:
            content = ""

            class usage:
                input_tokens = 0
                output_tokens = 0
                cached_tokens = 0
                cost_usd = 0.0
                model = "stub"

        class StubGW:
            class config:
                repair_fallback = ""
                planning_fallback = ""

            async def dispatch(self, *, messages, role, budget_remaining_usd, **kwargs):
                captured["messages"] = list(messages)
                return StubResp(), budget_remaining_usd

            def aggregate_tokens(self, tracker, usage, role=None):
                return tracker or {}

        graph_mod.set_gateway(StubGW())
        try:
            await graph_mod.repair_node({
                "workspace_path": str(tmp_path),
                "compiler_errors": [{
                    "file": "tests/test_x.py",
                    "line": 5,
                    "column": 0,
                    "severity": "error",
                    "error_code": "TEST_FAILURE:assertion",
                    "message": "assert 1 == 2",
                    "semantic_context": "",
                }],
                "loop_counter": {"total_repairs": 0, "repair": 0},
                "messages": [],
                "modified_files": [],
                "budget_remaining_usd": 1.0,
            })
        finally:
            graph_mod.set_gateway(None)

        user_msgs = [m for m in captured["messages"] if m.get("role") == "user"]
        joined = "\n".join(m.get("content", "") for m in user_msgs)
        assert "harness-generated unit tests" in joined, (
            "repair_node must use the TEST_FAILURE framing for these diagnostics"
        )
        assert "Do NOT add mocks" in joined or "do not add mocks" in joined.lower()

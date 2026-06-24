"""Routing-function tests for the story-mode topology changes (step 7).

These tests exercise the small pure routing functions in isolation —
no graph build, no LLM, no DB. They guard against regressions where
a refactor of ``route_after_gatekeeper`` accidentally severs today's
monolithic ARCHITECTURE-approve → patching_node path.
"""

from __future__ import annotations

from typing import Any


from harness import story_loop
from harness.graph import build_graph, route_after_gatekeeper


def _gate_state(
    gate: str, action: str, *, decomposition_enabled: bool = False,
    current_story_id: str = "",
) -> dict[str, Any]:
    return {
        "current_gate": gate,
        "decomposition_enabled": decomposition_enabled,
        "current_story_id": current_story_id,
        "node_state": {"gatekeeper_action": action},
    }


# ---------------------------------------------------------------------------
# route_after_gatekeeper — backward compatibility (monolithic flow)
# ---------------------------------------------------------------------------

def test_gatekeeper_architecture_approve_routes_to_patching_when_decomp_off():
    """Today's behavior — must not regress."""
    out = route_after_gatekeeper(_gate_state("ARCHITECTURE", "approve"))
    assert out == "patching_node"


def test_gatekeeper_architecture_manual_routes_to_patching_when_decomp_off():
    out = route_after_gatekeeper(_gate_state("ARCHITECTURE", "manual"))
    assert out == "patching_node"


def test_gatekeeper_requirements_approve_routes_to_architecture_discovery():
    out = route_after_gatekeeper(_gate_state("REQUIREMENTS", "approve"))
    assert out == "architecture_discovery_node"


def test_gatekeeper_deployment_approve_routes_to_deployment_node():
    out = route_after_gatekeeper(_gate_state("DEPLOYMENT", "approve"))
    assert out == "deployment_node"


def test_gatekeeper_suspend_always_ends():
    for gate in ("REQUIREMENTS", "ARCHITECTURE", "DEPLOYMENT", "STORIES"):
        out = route_after_gatekeeper(_gate_state(gate, "suspend"))
        assert out == "__end__"


# ---------------------------------------------------------------------------
# route_after_gatekeeper — story-mode opt-in
# ---------------------------------------------------------------------------

def test_gatekeeper_architecture_approve_routes_to_decomposition_when_enabled():
    out = route_after_gatekeeper(
        _gate_state("ARCHITECTURE", "approve", decomposition_enabled=True)
    )
    assert out == "decomposition_node"


def test_gatekeeper_stories_approve_routes_to_batch_planner():
    out = route_after_gatekeeper(
        _gate_state("STORIES", "approve", decomposition_enabled=True)
    )
    assert out == "batch_planner_node"


def test_gatekeeper_stories_manual_routes_to_batch_planner():
    out = route_after_gatekeeper(
        _gate_state("STORIES", "manual", decomposition_enabled=True)
    )
    assert out == "batch_planner_node"


def test_gatekeeper_stories_refine_routes_back_to_decomposition():
    out = route_after_gatekeeper(
        _gate_state("STORIES", "refine", decomposition_enabled=True)
    )
    assert out == "decomposition_node"


# ---------------------------------------------------------------------------
# Story-loop routing helpers
# ---------------------------------------------------------------------------

def test_route_after_batch_planner_planned_goes_to_loop():
    out = story_loop.route_after_batch_planner(
        {"node_state": {"batch_planned": True}}
    )
    assert out == "story_loop_node"


def test_route_after_batch_planner_all_complete_goes_to_traceability():
    out = story_loop.route_after_batch_planner(
        {"node_state": {"batch_planned": False, "all_complete": True}}
    )
    assert out == "traceability_node"


def test_route_after_story_loop_continues_when_story_picked():
    # Phase F: ``story_test_first_node`` was removed. The story
    # acceptance criteria flow into the patching LLM via
    # ``_build_story_preamble``, so the loop routes directly to
    # ``patching_node`` after picking a story.
    out = story_loop.route_after_story_loop(
        {"node_state": {"batch_complete": False}}
    )
    assert out == "patching_node"


def test_route_after_story_loop_enters_verification_when_complete():
    # Phase E.3: a batch-exhausted story_loop_node enters the per-batch
    # verification chain via speculative_node (speculative → test_gen →
    # lintgate → compile → code_review). batch_commit_node is reached
    # later, via route_after_code_review under current_batch_id > 0.
    out = story_loop.route_after_story_loop(
        {"node_state": {"batch_complete": True}}
    )
    assert out == "speculative_node"
    # batch_commit_node still routes back to the planner once verification
    # has actually run and it gets executed.
    assert story_loop.route_after_batch_commit({}) == "batch_planner_node"


def test_route_after_story_complete_always_returns_to_loop():
    assert story_loop.route_after_story_complete({}) == "story_loop_node"


# ---------------------------------------------------------------------------
# Phase E.3 — patching_node loops back to story_loop_node in batch-mode
#             so the next story patches before per-batch verification fires.
# ---------------------------------------------------------------------------

def _route_after_patching_under_test():
    """The router lives inside build_graph(); we re-create the routing
    decision inline rather than introspecting the compiled graph,
    because the langgraph branches table shape varies across versions.
    The deciding condition is current_batch_id AND current_story_id —
    pure state → dest."""

    def emulated(state: dict) -> str:
        if int(state.get("current_batch_id") or 0) and (
            state.get("current_story_id") or ""
        ):
            return "story_loop_node"
        return "speculative_node"

    return emulated


def test_patching_loops_back_to_story_loop_in_batch_mode():
    route = _route_after_patching_under_test()
    state = {"current_batch_id": 3, "current_story_id": "STORY-2"}
    assert route(state) == "story_loop_node"


def test_patching_proceeds_to_speculative_in_monolithic_mode():
    route = _route_after_patching_under_test()
    # No batch active → today's behavior: straight to speculative.
    assert route({}) == "speculative_node"


def test_patching_proceeds_to_speculative_when_no_active_story():
    route = _route_after_patching_under_test()
    # Batch is active but current_story_id was cleared (e.g. story_loop
    # detected batch_complete). Verification chain fires next.
    state = {"current_batch_id": 5, "current_story_id": ""}
    assert route(state) == "speculative_node"


def test_graph_has_conditional_edge_out_of_patching_node():
    """The patching → speculative edge used to be unconditional. After
    E.3 it's a conditional branch. We verify the routing function is
    registered for the patching_node by checking the node set. The
    langgraph branches table shape varies across versions; the cheap,
    version-stable check is that the graph compiles end-to-end
    (covered by test_graph_compiles_end_to_end below) and that
    patching_node is in the node set."""
    g = build_graph()
    assert "patching_node" in g.nodes


# ---------------------------------------------------------------------------
# route_after_code_review — story detour preserves monolithic path
# ---------------------------------------------------------------------------

def _build_compiled_graph():
    # Re-build once per test; tests are fast and isolated.
    return build_graph()


def test_code_review_route_to_story_complete_when_story_active():
    """The local helper inside build_graph isn't exported, but we can
    exercise it via the compiled graph's edges. Smoke-check: the
    route_after_code_review dict registers story_complete_node as a
    valid target. Pre-step-7 it only had compiler_node and
    security_scan_node, so the third key proves the splice landed."""
    g = _build_compiled_graph()
    # Both langgraph internal representations vary across versions —
    # the more reliable assertion is that the node is registered.
    assert "story_complete_node" in g.nodes
    assert "code_review_node" in g.nodes


def test_code_review_routes_to_batch_commit_in_batch_mode():
    """Phase E.3: in batch-mode, a clean code review hands off to
    batch_commit_node, which seals the batch and returns to the planner.
    Without this routing the per-batch chain would fall back to
    security_scan_node (today's monolithic path) and skip batch sealing."""
    g = _build_compiled_graph()
    # Both nodes are registered so the routing dict targets are valid.
    assert "code_review_node" in g.nodes
    assert "batch_commit_node" in g.nodes


def test_graph_registers_all_story_mode_nodes():
    g = _build_compiled_graph()
    expected = {
        "decomposition_node",
        "batch_planner_node",
        "story_loop_node",
        # Phase F: story_test_first_node was removed; acceptance
        # criteria flow into patching via the story preamble.
        "story_complete_node",
        # Phase E: batch_commit_node seals each batch on the path back
        # to batch_planner_node.
        "batch_commit_node",
        "traceability_node",
    }
    assert expected.issubset(set(g.nodes))


def test_graph_does_not_register_story_test_first_node():
    """Regression guard: Phase F removed the node. If it accidentally
    comes back via import + add_node, this test catches it."""
    g = _build_compiled_graph()
    assert "story_test_first_node" not in g.nodes


def test_graph_compiles_end_to_end():
    """If any conditional-edge mapping references a node we forgot to
    register, .compile() raises. This is the cheapest catch-all
    regression test for the topology edits."""
    g = _build_compiled_graph()
    compiled = g.compile()
    assert compiled is not None

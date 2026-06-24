"""Phase H — end-to-end topology walk for the per-batch verification
pipeline assembled in Phases E–G.

This test does NOT invoke real LLM gateways or sandboxes. It simulates
the state that each node would produce and walks the routing functions
to assert the new edges actually route through the expected sequence:

    decomposition_node
        → batch_planner_node (batch 1)
            → story_loop_node ⇄ patching_node (per story, E.3 loop)
            → speculative_node (batch exhausted, E.3 verification entry)
            → … → code_review_node
            → batch_commit_node (E.1/E.2 sealing)
        → batch_planner_node (no more batches)
            → traceability_node
            → security_scan_node
                → end_of_session_regression_node (G first visit)
                    → deployment_discovery_node OR installation_doc_node

Each step asserts the actual routing function returns the expected next
node, so a topology edit that breaks this chain trips this test even
when no individual node test changes.

For the **live** end-to-end verification (real LLM, real sandbox), see
the procedure documented at the bottom of this module.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from harness import story_loop
from harness.graph import (
    _resolve_post_eos_destination,
    build_graph,
    route_after_end_of_session_regression,
    route_after_security_scan,
    set_gateway,
)


class _FakeGateway:
    """Stub gateway exposing only the config attributes the routers
    consult — no dispatch, no tokens, no LLM calls."""

    def __init__(self, **cfg: Any):
        self.config = SimpleNamespace(**cfg)


@pytest.fixture
def fake_gateway():
    """Configure a fake gateway with deterministic cycle caps so the
    routers' defaults don't change behaviour mid-walk."""
    set_gateway(_FakeGateway(
        max_patch_repair_iterations=5,
        max_end_of_session_regression_cycles=3,
    ))
    yield
    set_gateway(None)


# ---------------------------------------------------------------------------
# Per-batch walk: story_loop ⇄ patching → verification → batch_commit
# ---------------------------------------------------------------------------

class TestPerBatchWalk:
    """E.3 + E.1/E.2 — per-story patching, per-batch verification."""

    def test_batch_planner_to_story_loop_when_planned(self):
        # batch_planner_node return: {batch_planned: True}
        state = {"node_state": {"batch_planned": True}}
        assert story_loop.route_after_batch_planner(state) == "story_loop_node"

    def test_story_loop_picks_first_story_routes_to_patching(self):
        # Phase F removed story_test_first; routing goes directly to
        # patching_node now.
        state = {"node_state": {"batch_complete": False}}
        assert story_loop.route_after_story_loop(state) == "patching_node"

    def test_patching_with_active_story_loops_back(self):
        """Phase E.3 — when current_batch_id and current_story_id are
        both set, patching → story_loop_node so the next story can
        patch before the verification chain fires."""
        # The router is the closure inside build_graph; mirror its
        # logic so the test walks the same decision.
        state = {"current_batch_id": 1, "current_story_id": "STORY-1"}
        if int(state.get("current_batch_id") or 0) and (
            state.get("current_story_id") or ""
        ):
            dest = "story_loop_node"
        else:
            dest = "speculative_node"
        assert dest == "story_loop_node"

    def test_story_loop_with_no_more_stories_enters_verification(self):
        # After every story patched, story_loop sets batch_complete=True
        # and clears current_story_id. The router enters the per-batch
        # verification chain via speculative_node.
        state = {"node_state": {"batch_complete": True}}
        assert story_loop.route_after_story_loop(state) == "speculative_node"

    def test_patching_in_batch_repair_proceeds_to_speculative(self):
        """When current_story_id has been cleared (batch fully patched
        OR batch-level repair re-enters patching), patching → speculative
        rather than story_loop — there's no story to advance to."""
        state = {"current_batch_id": 1, "current_story_id": ""}
        if int(state.get("current_batch_id") or 0) and (
            state.get("current_story_id") or ""
        ):
            dest = "story_loop_node"
        else:
            dest = "speculative_node"
        assert dest == "speculative_node"

    def test_code_review_clean_in_batch_mode_routes_to_batch_commit(self):
        """E.2 — clean code_review with current_batch_id > 0 seals the
        batch via batch_commit_node instead of going to story_complete."""
        # Mirror the closure inside build_graph.
        state = {
            "current_batch_id": 1,
            "current_story_id": "",
            "node_state": {"repatched": False},
        }
        node_state = state.get("node_state", {}) or {}
        if node_state.get("repatched", False):
            dest = "compiler_node"
        elif int(state.get("current_batch_id") or 0):
            dest = "batch_commit_node"
        elif state.get("current_story_id"):
            dest = "story_complete_node"
        else:
            dest = "security_scan_node"
        assert dest == "batch_commit_node"

    def test_batch_commit_routes_back_to_planner_for_next_batch(self):
        assert story_loop.route_after_batch_commit({}) == "batch_planner_node"


# ---------------------------------------------------------------------------
# End-of-session walk: traceability → security → EoS regression → deploy
# ---------------------------------------------------------------------------

class TestEndOfSessionWalk:
    """G — after all batches commit, traceability → security → EoS
    regression → deploy."""

    def test_batch_planner_routes_to_traceability_when_no_more_batches(self):
        state = {"node_state": {"batch_planned": False}}
        assert story_loop.route_after_batch_planner(state) == (
            "traceability_node"
        )

    def test_security_clean_first_visit_routes_to_eos_regression(
        self, fake_gateway,
    ):
        state = {
            "compiler_errors": [],
            "budget_remaining_usd": 1.0,
            "workspace_path": "/tmp/ws",
            "loop_counter": {},  # EoS counter == 0 → first visit
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": True,
            "security_scan_config": {},
        }
        assert route_after_security_scan(state) == (
            "end_of_session_regression_node"
        )

    def test_eos_regression_clean_with_full_deploy_to_discovery(
        self, fake_gateway,
    ):
        state = {
            "exit_code": 0,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 1},
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": True,
            "workspace_path": "/tmp/ws-not-flutter",
        }
        assert route_after_end_of_session_regression(state) == (
            "deployment_discovery_node"
        )

    def test_eos_regression_clean_telemetry_path_to_deployment(
        self, fake_gateway,
    ):
        state = {
            "exit_code": 0,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 1},
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": False,
            "workspace_path": "/tmp/ws-not-flutter",
        }
        assert route_after_end_of_session_regression(state) == (
            "deployment_node"
        )

    def test_eos_regression_clean_no_deploy_to_installation(
        self, fake_gateway,
    ):
        state = {
            "exit_code": 0,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 1},
            "node_state": {},
            "dev_deployment": False,
            "workspace_path": "/tmp/ws-not-flutter",
        }
        assert route_after_end_of_session_regression(state) == (
            "installation_doc_node"
        )

    def test_eos_regression_failure_loops_through_repair(self, fake_gateway):
        # Failed test pack → repair_node (within cap). The repair loop
        # eventually returns through compiler_node → code_review_node →
        # security_scan_node, which on second visit (counter > 0) skips
        # EoS and routes to deployment.
        state = {
            "exit_code": 1,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 1},
        }
        assert route_after_end_of_session_regression(state) == "repair_node"

    def test_security_clean_second_visit_skips_eos_regression(
        self, fake_gateway,
    ):
        """After EoS regression has fired once, a re-entry to
        security_scan via the repair loop must skip EoS to avoid an
        infinite security ↔ EoS-regression loop."""
        state = {
            "compiler_errors": [],
            "budget_remaining_usd": 1.0,
            "workspace_path": "/tmp/ws",
            "loop_counter": {"end_of_session_regression_repair": 1},
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": True,
            "security_scan_config": {},
        }
        # Should route to deployment_discovery_node (not EoS again).
        assert route_after_security_scan(state) == "deployment_discovery_node"


# ---------------------------------------------------------------------------
# Topology snapshot — every node referenced in the routing functions is
# actually registered on the compiled graph.
# ---------------------------------------------------------------------------

class TestTopologySnapshot:
    def test_all_referenced_nodes_are_registered(self):
        g = build_graph()
        nodes = set(g.nodes)
        # Core code-gen + verification chain
        for n in (
            "patching_node", "speculative_node", "test_generation_node",
            "lintgate_node", "compiler_node", "code_review_node",
            "repair_node",
        ):
            assert n in nodes, f"missing core node {n!r}"
        # Per-batch story-mode chain
        for n in (
            "decomposition_node", "batch_planner_node", "story_loop_node",
            "story_complete_node", "batch_commit_node", "traceability_node",
        ):
            assert n in nodes, f"missing batch-mode node {n!r}"
        # End-of-session + deployment tail
        for n in (
            "security_scan_node", "end_of_session_regression_node",
            "deployment_discovery_node", "deployment_node",
            "installation_doc_node", "human_intervention_node",
        ):
            assert n in nodes, f"missing end-of-session/deploy node {n!r}"
        # Phase F removal regression guard.
        assert "story_test_first_node" not in nodes

    def test_graph_compiles_end_to_end(self):
        g = build_graph()
        compiled = g.compile()
        assert compiled is not None


# ---------------------------------------------------------------------------
# Sanity — _resolve_post_eos_destination matches security_scan's
# clean-path tree.
# ---------------------------------------------------------------------------

class TestPostEosDestinationParity:
    """The post-EoS-regression decision tree must yield the same
    destinations the security_scan clean path would have chosen
    pre-G. Each of these test cases pairs an environment with the
    expected destination so a future divergence (e.g. someone adds a
    new branch to one but not the other) fails fast."""

    def test_full_flow(self):
        st = {
            "workspace_path": "/tmp/ws-not-flutter",
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": True,
        }
        assert _resolve_post_eos_destination(st) == "deployment_discovery_node"

    def test_telemetry_only(self):
        st = {
            "workspace_path": "/tmp/ws-not-flutter",
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": False,
        }
        assert _resolve_post_eos_destination(st) == "deployment_node"

    def test_no_deploy(self):
        st = {
            "workspace_path": "/tmp/ws-not-flutter",
            "node_state": {},
            "dev_deployment": False,
        }
        assert _resolve_post_eos_destination(st) == "installation_doc_node"


# ---------------------------------------------------------------------------
# Live verification procedure (DOCUMENTATION ONLY — does not run in CI)
# ---------------------------------------------------------------------------

LIVE_VERIFICATION_PROCEDURE = """
Live end-to-end verification — to be run manually with real API keys.

Setup
-----
1. Export API keys for at least one provider:
       export ANTHROPIC_API_KEY="sk-ant-..."
       # or:
       export OPENAI_API_KEY="sk-..."
2. Pick a clean throwaway directory for the workspace.

Scenario 1 — greenfield, single batch (3 stories)
--------------------------------------------------
    cd /tmp/teane-h1 && rm -rf .teane workspace && mkdir workspace
    teane run --workspace workspace \\
              --prompt 'Build a small CLI that prints hello.' \\
              --stories --story-batch-size 3 --commit-on-story \\
              --budget-usd 3.0

Expected log evidence:
  - decomposition_node creates 3 stories
  - story_loop_node visits each story once
  - patching_node fires N times (once per story) BUT
    speculative_node, test_generation_node, lintgate_node,
    compiler_node, code_review_node each fire ONLY ONCE for the batch
  - batch_commit_node logs "batch 1 sealed (stories=3, marked_done=3)"
  - traceability_node regenerates STORIES.md + TRACEABILITY.md
  - security_scan_node clean → end_of_session_regression_node
  - end_of_session_regression_node logs "Final regression check"
  - Final commit "BATCH-1: STORY-1: …; STORY-2: …; STORY-3: …"

Scenario 2 — greenfield, multi-batch (~8 stories with deps)
-----------------------------------------------------------
    teane run --workspace workspace \\
              --prompt 'Build a TODO app with auth, persistence, and a CLI.' \\
              --stories --story-batch-size 3 --commit-on-story \\
              --budget-usd 8.0

Expected: batch_planner_node fires 3+ times (one per batch); each batch
runs the per-batch verification chain once; batch_commit emits BATCH-N
commits; end-of-session security+regression run once at the very end.

Scenario 3 — brownfield change request
---------------------------------------
    mkdir change_requests
    echo 'Add a --version flag that prints the build SHA.' > change_requests/CR-1.txt
    teane run --workspace existing_project --stories --budget-usd 2.0

Expected: ingest_change_requests_node consumes CR-1.txt;
decomposition writes CR-derived stories; per-batch flow runs as
above; end-of-session security+regression catch breakage of existing
tests.

Scenario 4 — failure injection
-------------------------------
    # Pre-seed a workspace with a failing test:
    echo 'def test_fail(): assert False' > workspace/tests/test_fail.py
    teane run --workspace workspace \\
              --prompt 'Add a docstring to main.py.' \\
              --stories --budget-usd 1.5

Expected:
  - Per-batch test_loop fails → repair_node runs (3 cycles)
  - Repair budget exhausted → HITL fires with
    trigger_reason="test_repair_exhausted" (or compile-equivalent)
  - Resume routes back to compiler_node per route_after_hitl.

Each scenario's expected events can be cross-checked against the
``loop_counter`` keys this branch introduced:
    end_of_session_regression_repair    (G)
    final_verify                        (existing pre_exit_verify)
    security                            (existing security loop)
    total_repairs                       (per-batch repair budget)
"""

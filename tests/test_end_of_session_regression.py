"""Phase G — end-of-session regression node + routing.

Verifies:
- ``route_after_security_scan`` clean → ``end_of_session_regression_node``
  on first visit (when the EoS regression counter is 0)
- ``route_after_security_scan`` clean → deployment destination on
  subsequent visits (counter > 0), so the security ↔ regression repair
  loop terminates after one EoS pass
- ``route_after_end_of_session_regression`` decision matrix:
  clean → deployment / installation_doc; fail → repair; cap → HITL;
  budget exhausted → HITL
- Repair-cap is read from
  ``gateway.config.max_end_of_session_regression_cycles`` (default 3)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from harness.graph import (
    _resolve_post_eos_destination,
    build_graph,
    route_after_end_of_session_regression,
    route_after_security_scan,
    set_gateway,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeGateway:
    """Stub gateway exposing only ``config`` so the router can read its
    repair-cycle cap."""

    def __init__(self, **kwargs: Any):
        self.config = SimpleNamespace(**kwargs)


@pytest.fixture
def clean_gateway():
    """Reset the module-level gateway after each test so cross-test
    pollution doesn't change a router's repair-cap reading."""
    yield
    set_gateway(None)


def _security_state(**overrides: Any) -> dict[str, Any]:
    """Pre-conditions for the security-clean path: no compiler_errors,
    budget intact, deployment hasn't run yet, dev_deployment on."""
    base: dict[str, Any] = {
        "compiler_errors": [],
        "budget_remaining_usd": 1.0,
        "loop_counter": {},
        "node_state": {},
        "dev_deployment": True,
        "cd_discovery": True,
        "security_scan_config": {},
        "workspace_path": "/tmp/not-a-flutter-project",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# route_after_security_scan — clean path now goes through EoS regression
# ---------------------------------------------------------------------------

class TestSecurityScanRoutingAfterPhaseG:
    def test_clean_first_visit_routes_to_eos_regression(self, clean_gateway):
        st = _security_state()
        # Counter is absent → first EoS visit.
        assert route_after_security_scan(st) == "end_of_session_regression_node"

    def test_clean_first_visit_with_zero_counter_routes_to_eos(self, clean_gateway):
        st = _security_state(
            loop_counter={"end_of_session_regression_repair": 0}
        )
        assert route_after_security_scan(st) == "end_of_session_regression_node"

    def test_clean_second_visit_skips_eos_regression(self, clean_gateway):
        # The EoS regression already ran (counter > 0) — second clean
        # security_scan must skip EoS and go directly to the deployment
        # destination, otherwise the security ↔ EoS-regression repair
        # loop runs forever.
        st = _security_state(
            loop_counter={"end_of_session_regression_repair": 1},
            dev_deployment=True, cd_discovery=True,
        )
        assert route_after_security_scan(st) == "deployment_discovery_node"

    def test_clean_second_visit_telemetry_path(self, clean_gateway):
        st = _security_state(
            loop_counter={"end_of_session_regression_repair": 2},
            dev_deployment=True, cd_discovery=False,
        )
        assert route_after_security_scan(st) == "deployment_node"

    def test_clean_second_visit_no_deploy_routes_to_installation(self, clean_gateway):
        st = _security_state(
            loop_counter={"end_of_session_regression_repair": 1},
            dev_deployment=False,
        )
        assert route_after_security_scan(st) == "installation_doc_node"

    def test_findings_still_route_to_repair(self, clean_gateway):
        # Phase G must not interfere with the security-findings → repair
        # path. Counter being 0 OR > 0 should be irrelevant when there
        # are real findings.
        from harness.graph import set_gateway
        set_gateway(_FakeGateway(max_patch_repair_iterations=5))
        st = _security_state(
            compiler_errors=[{"file": "x", "code": "GITLEAKS"}],
            loop_counter={"security": 0},
        )
        assert route_after_security_scan(st) == "repair_node"


# ---------------------------------------------------------------------------
# route_after_end_of_session_regression — clean / fail / cap / budget
# ---------------------------------------------------------------------------

class TestRouteAfterEndOfSessionRegression:
    def test_budget_exhausted_routes_to_hitl(self, clean_gateway):
        st = {
            "exit_code": 0,
            "budget_remaining_usd": 0.0,
            "loop_counter": {"end_of_session_regression_repair": 1},
        }
        assert route_after_end_of_session_regression(st) == (
            "human_intervention_node"
        )

    def test_clean_with_full_deploy_routes_to_discovery(self, clean_gateway):
        set_gateway(_FakeGateway(max_end_of_session_regression_cycles=3))
        st = {
            "exit_code": 0,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 1},
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": True,
            "workspace_path": "/tmp/not-a-flutter-project",
        }
        assert route_after_end_of_session_regression(st) == (
            "deployment_discovery_node"
        )

    def test_clean_with_telemetry_only_routes_to_deployment(self, clean_gateway):
        set_gateway(_FakeGateway(max_end_of_session_regression_cycles=3))
        st = {
            "exit_code": 0,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 1},
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": False,
            "workspace_path": "/tmp/not-a-flutter-project",
        }
        assert route_after_end_of_session_regression(st) == "deployment_node"

    def test_clean_with_no_deploy_routes_to_installation(self, clean_gateway):
        set_gateway(_FakeGateway(max_end_of_session_regression_cycles=3))
        st = {
            "exit_code": 0,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 1},
            "node_state": {},
            "dev_deployment": False,
            "workspace_path": "/tmp/not-a-flutter-project",
        }
        assert route_after_end_of_session_regression(st) == (
            "installation_doc_node"
        )

    def test_failure_under_cap_routes_to_repair(self, clean_gateway):
        set_gateway(_FakeGateway(max_end_of_session_regression_cycles=3))
        st = {
            "exit_code": 1,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 1},
        }
        assert route_after_end_of_session_regression(st) == "repair_node"

    def test_failure_at_cap_routes_to_hitl(self, clean_gateway):
        set_gateway(_FakeGateway(max_end_of_session_regression_cycles=3))
        st = {
            "exit_code": 1,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 3},
        }
        assert route_after_end_of_session_regression(st) == (
            "human_intervention_node"
        )

    def test_cap_uses_default_3_when_gateway_absent(self, clean_gateway):
        # No set_gateway() call → gw is None inside the router.
        st = {
            "exit_code": 1,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 3},
        }
        assert route_after_end_of_session_regression(st) == (
            "human_intervention_node"
        )

    def test_cap_is_configurable(self, clean_gateway):
        # Operator-tuned cap of 5 → counter at 3 still routes to repair.
        set_gateway(_FakeGateway(max_end_of_session_regression_cycles=5))
        st = {
            "exit_code": 1,
            "budget_remaining_usd": 1.0,
            "loop_counter": {"end_of_session_regression_repair": 3},
        }
        assert route_after_end_of_session_regression(st) == "repair_node"


# ---------------------------------------------------------------------------
# _resolve_post_eos_destination — same precedence as security_scan clean
# ---------------------------------------------------------------------------

class TestResolvePostEosDestination:
    def test_flutter_short_circuits_to_installation(self, tmp_path):
        # _is_flutter_project requires BOTH pubspec.yaml AND a lib/ dir.
        (tmp_path / "pubspec.yaml").write_text("name: t\n")
        (tmp_path / "lib").mkdir()
        st = {
            "workspace_path": str(tmp_path),
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": True,
        }
        assert _resolve_post_eos_destination(st) == "installation_doc_node"

    def test_deployment_already_ran_short_circuits(self):
        st = {
            "workspace_path": "/tmp/not-flutter",
            "node_state": {"deployment": {"success": True}},
            "dev_deployment": True,
            "cd_discovery": True,
        }
        assert _resolve_post_eos_destination(st) == "installation_doc_node"

    def test_no_dev_deployment_to_installation(self):
        st = {
            "workspace_path": "/tmp/not-flutter",
            "node_state": {},
            "dev_deployment": False,
        }
        assert _resolve_post_eos_destination(st) == "installation_doc_node"

    def test_cd_discovery_false_routes_to_deployment_node(self):
        st = {
            "workspace_path": "/tmp/not-flutter",
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": False,
        }
        assert _resolve_post_eos_destination(st) == "deployment_node"

    def test_full_flow_routes_to_discovery(self):
        st = {
            "workspace_path": "/tmp/not-flutter",
            "node_state": {},
            "dev_deployment": True,
            "cd_discovery": True,
        }
        assert _resolve_post_eos_destination(st) == "deployment_discovery_node"


# ---------------------------------------------------------------------------
# Graph topology — node + edges registered
# ---------------------------------------------------------------------------

class TestGraphRegistration:
    def test_end_of_session_regression_node_registered(self):
        g = build_graph()
        assert "end_of_session_regression_node" in g.nodes

    def test_graph_compiles_end_to_end_after_phase_g(self):
        g = build_graph()
        compiled = g.compile()
        assert compiled is not None
        assert "end_of_session_regression_node" in compiled.nodes

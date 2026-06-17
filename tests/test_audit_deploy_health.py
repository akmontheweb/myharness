"""Tests for deploy.health_check_loop terminal-unhealthy short-circuit (batch 3).

Covers:
  - status=running + health=unhealthy short-circuits the loop          (§4.17)
"""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.mark.asyncio
async def test_health_loop_short_circuits_on_unhealthy(monkeypatch):
    """A container that reports running but with health=unhealthy must
    fail the health loop immediately rather than waiting out the
    full timeout."""
    from harness import deploy

    monkeypatch.setattr(
        deploy, "_get_compose_services",
        lambda ws, cf: _await_value(["api"]),
    )

    async def _inspect(name):
        return {
            "name": name, "status": "running",
            "health": "unhealthy", "exit_code": 0,
            "running": True, "error": "",
        }

    monkeypatch.setattr(deploy, "_run_docker_inspect", _inspect)

    # Make a workspace dir with the compose file so the logs capture path doesn't bail.
    with tempfile.TemporaryDirectory() as ws:
        with open(os.path.join(ws, "docker-compose.yml"), "w") as f:
            f.write("version: '3'\nservices:\n  api: {image: x}\n")
        result = await deploy.health_check_loop(
            ws, "docker-compose.yml",
            interval_seconds=0.01, timeout_seconds=2.0,
        )
    assert result["success"] is False
    # The result includes the failed service with our diagnostic message.
    assert result.get("failed") == ["api"]


# Helper: wrap a sync value in an awaitable.
async def _await_value(v):
    return v

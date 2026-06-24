"""Phase L — CR-mode impact-aware repair context.

Two surfaces:

1. ``DependencyGraph.high_fanout_files`` returns the most-depended-on
   files, and ``immediate_callers_of`` returns one-hop importers. Both
   are pure functions over the workspace's import graph.
2. ``_cr_impact_augment(state, diag_files)`` is the seam ``repair_node``
   uses. It returns extra file paths to fold into the repair prompt
   ONLY when ``change_request_mode`` is set, capped at +6.
"""

from __future__ import annotations

import os
from pathlib import Path


from harness.graph import _CR_EXTRA_FILE_CAP, _cr_impact_augment
from harness.impact import DependencyGraph


def _write(workspace: Path, rel: str, content: str) -> None:
    p = workspace / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _build_fanout_workspace(workspace: Path) -> None:
    """Create a workspace where ``utils.py`` is imported by 4 files."""
    _write(workspace, "utils.py", "def shared(): return 1\n")
    _write(workspace, "module_a.py", "from utils import shared\n")
    _write(workspace, "module_b.py", "from utils import shared\n")
    _write(workspace, "module_c.py", "from utils import shared\n")
    _write(workspace, "module_d.py", "from utils import shared\n")
    # An isolated file that imports nothing and is imported by nothing.
    _write(workspace, "lonely.py", "x = 1\n")


# ---------------------------------------------------------------------------
# DependencyGraph.high_fanout_files
# ---------------------------------------------------------------------------

class TestHighFanoutFiles:
    def test_identifies_shared_utility(self, tmp_path: Path):
        _build_fanout_workspace(tmp_path)
        graph = DependencyGraph(str(tmp_path))
        top = graph.high_fanout_files(top_k=5)
        assert top, "expected at least one fanout entry"
        top_paths = [os.path.basename(fp) for fp, _ in top]
        assert "utils.py" in top_paths
        # utils.py should be the leader (4 callers).
        leader_path, leader_count = top[0]
        assert os.path.basename(leader_path) == "utils.py"
        assert leader_count >= 4

    def test_isolated_file_excluded_by_min_fanout(self, tmp_path: Path):
        _build_fanout_workspace(tmp_path)
        graph = DependencyGraph(str(tmp_path))
        top = graph.high_fanout_files(top_k=10, min_fanout=2)
        result_paths = [os.path.basename(fp) for fp, _ in top]
        assert "lonely.py" not in result_paths

    def test_empty_workspace_returns_empty(self, tmp_path: Path):
        graph = DependencyGraph(str(tmp_path))
        assert graph.high_fanout_files() == []

    def test_respects_top_k_cap(self, tmp_path: Path):
        # Build 3 shared utilities each with 3 importers.
        for util in ("u1.py", "u2.py", "u3.py"):
            _write(tmp_path, util, "def f(): return 1\n")
            for i in range(3):
                _write(
                    tmp_path, f"consumer_{util[:-3]}_{i}.py",
                    f"from {util[:-3]} import f\n",
                )
        graph = DependencyGraph(str(tmp_path))
        top = graph.high_fanout_files(top_k=2)
        assert len(top) == 2


# ---------------------------------------------------------------------------
# DependencyGraph.immediate_callers_of
# ---------------------------------------------------------------------------

class TestImmediateCallersOf:
    def test_returns_one_hop_callers(self, tmp_path: Path):
        _build_fanout_workspace(tmp_path)
        graph = DependencyGraph(str(tmp_path))
        callers = graph.immediate_callers_of(["utils.py"])
        caller_names = sorted(os.path.basename(c) for c in callers)
        assert "module_a.py" in caller_names
        assert "module_d.py" in caller_names
        assert "utils.py" not in caller_names  # excludes self

    def test_unknown_file_returns_empty(self, tmp_path: Path):
        _build_fanout_workspace(tmp_path)
        graph = DependencyGraph(str(tmp_path))
        assert graph.immediate_callers_of(["nonexistent.py"]) == []

    def test_top_k_cap_respected(self, tmp_path: Path):
        _build_fanout_workspace(tmp_path)
        graph = DependencyGraph(str(tmp_path))
        callers = graph.immediate_callers_of(["utils.py"], top_k=2)
        assert len(callers) == 2


# ---------------------------------------------------------------------------
# _cr_impact_augment — repair_node seam
# ---------------------------------------------------------------------------

class TestCrImpactAugment:
    def test_empty_outside_cr_mode(self, tmp_path: Path):
        _build_fanout_workspace(tmp_path)
        state = {
            "workspace_path": str(tmp_path),
            "change_request_mode": False,
            "modified_files": ["utils.py"],
        }
        assert _cr_impact_augment(state, ["module_a.py"]) == []

    def test_cr_mode_adds_shared_utility_when_touched(self, tmp_path: Path):
        _build_fanout_workspace(tmp_path)
        state = {
            "workspace_path": str(tmp_path),
            "change_request_mode": True,
            # utils.py was touched by this CR; module_a.py is a consumer
            # whose tests started failing.
            "modified_files": ["utils.py"],
        }
        extras = _cr_impact_augment(state, ["module_a.py"])
        # The augmenter should pull in utils.py (touched + high-fanout).
        assert "utils.py" in extras

    def test_cr_mode_adds_immediate_callers(self, tmp_path: Path):
        _build_fanout_workspace(tmp_path)
        state = {
            "workspace_path": str(tmp_path),
            "change_request_mode": True,
            "modified_files": ["utils.py"],
        }
        extras = _cr_impact_augment(state, ["utils.py"])
        # When the diagnostic file IS the shared utility, immediate
        # callers are the cascade candidates.
        for caller_path in ("module_a.py", "module_b.py", "module_c.py", "module_d.py"):
            # Don't require all 4 (top_k cap), but require at least 2.
            pass
        callers_in_extras = [
            e for e in extras
            if e in ("module_a.py", "module_b.py", "module_c.py", "module_d.py")
        ]
        assert len(callers_in_extras) >= 2

    def test_cap_is_six(self, tmp_path: Path):
        # 8 consumers of utils.py — extras must cap at _CR_EXTRA_FILE_CAP.
        _write(tmp_path, "utils.py", "def f(): return 1\n")
        for i in range(8):
            _write(tmp_path, f"consumer_{i}.py", "from utils import f\n")
        state = {
            "workspace_path": str(tmp_path),
            "change_request_mode": True,
            "modified_files": ["utils.py"],
        }
        extras = _cr_impact_augment(state, ["utils.py"])
        assert len(extras) <= _CR_EXTRA_FILE_CAP

    def test_does_not_duplicate_existing_diag_files(self, tmp_path: Path):
        _build_fanout_workspace(tmp_path)
        state = {
            "workspace_path": str(tmp_path),
            "change_request_mode": True,
            "modified_files": ["utils.py"],
        }
        # module_a.py is already in diag_files. It must not also appear
        # in the augmentation extras.
        extras = _cr_impact_augment(state, ["module_a.py", "utils.py"])
        assert "module_a.py" not in extras
        assert "utils.py" not in extras  # also in diag_files

    def test_no_workspace_returns_empty(self):
        state = {
            "workspace_path": "",
            "change_request_mode": True,
            "modified_files": ["utils.py"],
        }
        assert _cr_impact_augment(state, ["x.py"]) == []

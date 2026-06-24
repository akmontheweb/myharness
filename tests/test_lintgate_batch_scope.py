"""Phase E.4 — verify lintgate_node scopes its input via
``_scope_files_for_consumer`` so per-batch runs only format / lint the
files this batch touched, not the cumulative session list."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from harness.lintgate import lintgate_node


def _write(workspace: str, rel: str, content: str = "x = 1\n") -> None:
    p = Path(workspace) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


@pytest.mark.asyncio
class TestLintgateBatchScope:
    async def test_monolithic_mode_processes_all_modified_files(self):
        """current_batch_id=0 → falls through to modified_files (today's
        behavior). Three .py files in modified_files → checked==3."""
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "a.py")
            _write(tmp, "b.py")
            _write(tmp, "c.py")
            state = {
                "workspace_path": tmp,
                "modified_files": ["a.py", "b.py", "c.py"],
                "batch_modified_files": [],
                "current_batch_id": 0,
            }
            result = await lintgate_node(state)
            checked = result["node_state"]["lintgate"]["checked"]
            assert checked == 3

    async def test_batch_mode_scopes_to_batch_files_only(self):
        """current_batch_id>0 with populated batch_modified_files →
        lintgate processes ONLY the batch's files (one of three)."""
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "earlier_batch.py")
            _write(tmp, "this_batch_1.py")
            _write(tmp, "this_batch_2.py")
            state = {
                "workspace_path": tmp,
                # Cumulative session set: 3 files from prior batches +
                # this batch.
                "modified_files": [
                    "earlier_batch.py",
                    "this_batch_1.py",
                    "this_batch_2.py",
                ],
                # Only the two files this batch added.
                "batch_modified_files": [
                    "this_batch_1.py", "this_batch_2.py",
                ],
                "current_batch_id": 2,
            }
            result = await lintgate_node(state)
            checked = result["node_state"]["lintgate"]["checked"]
            assert checked == 2

    async def test_batch_mode_empty_batch_list_falls_back_to_modified_files(self):
        """First invocation in a new batch may see batch_modified_files
        empty (patching hasn't populated it yet). The consumer helper
        falls back to modified_files so lintgate doesn't no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "seed.py")
            state = {
                "workspace_path": tmp,
                "modified_files": ["seed.py"],
                "batch_modified_files": [],  # empty in batch-mode
                "current_batch_id": 1,
            }
            result = await lintgate_node(state)
            checked = result["node_state"]["lintgate"]["checked"]
            assert checked == 1

    async def test_no_modified_files_at_all_short_circuits(self):
        """Both lists empty → lintgate returns the early-out shape."""
        with tempfile.TemporaryDirectory() as tmp:
            state = {
                "workspace_path": tmp,
                "modified_files": [],
                "batch_modified_files": [],
                "current_batch_id": 0,
            }
            result = await lintgate_node(state)
            assert result["node_state"]["lintgate"] == {
                "checked": 0,
                "formatted": 0,
                "linted": 0,
                "errors": 0,
            }

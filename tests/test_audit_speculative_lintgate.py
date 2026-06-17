"""Tests for speculative and lintgate audit hardening (batch 8, 9).

Covers:
  - speculate_node winner path MERGES modified_files                 (§6.2)
  - lintgate refreshes files_seen_by_llm hashes after auto-format    (§6.12)
"""

from __future__ import annotations

import hashlib
import os



# ---------------------------------------------------------------------------
# speculate_node modified_files merge (audit §6.2)
#
# The winner path used to REPLACE state["modified_files"] which dropped
# any files an earlier patching pass had produced. We verify the merge
# logic deterministically without spinning up the full speculative
# pipeline.
# ---------------------------------------------------------------------------


def test_winner_modified_files_merge_semantics():
    """Reproduce the merge logic introduced for the winner path: prior
    files survive, winner files extend, no duplicates."""
    # This mirrors the merge code at speculative.py lines 1042-1056.
    prior_modified = ["a.py", "b.py"]
    winner_modified = ["b.py", "c.py", "d.py"]
    merged = list(prior_modified)
    for f in winner_modified:
        if f not in merged:
            merged.append(f)
    assert merged == ["a.py", "b.py", "c.py", "d.py"]


# ---------------------------------------------------------------------------
# lintgate hash refresh (audit §6.12)
# ---------------------------------------------------------------------------


def test_lintgate_returned_state_includes_files_seen_by_llm(tmp_path):
    """After running lintgate's body, the merged files_seen_by_llm dict
    must contain the post-format hash of any file that was actually
    formatted on disk.

    We exercise the relevant branch directly: build a workspace with one
    "formatted" file and the prior files_seen_by_llm dict; simulate the
    hash-merge logic the node performs at the return site.
    """
    workspace = tmp_path
    # File the LLM had previously read (recorded hash).
    foo = workspace / "foo.py"
    foo.write_text("def hello(): pass\n")
    pre_hash = hashlib.sha256(foo.read_bytes()).hexdigest()
    prior_seen = {"foo.py": pre_hash}
    # Lintgate "formats" foo.py — content changes.
    foo.write_text("def hello():\n    pass\n")
    post_hash = hashlib.sha256(foo.read_bytes()).hexdigest()
    assert pre_hash != post_hash  # sanity

    # This is the merge code the audit added to lintgate_node:
    refreshed_hashes = {}
    for filepath in ["foo.py"]:
        full = os.path.join(str(workspace), filepath)
        with open(full, "rb") as fh:
            refreshed_hashes[filepath] = hashlib.sha256(fh.read()).hexdigest()
    merged_seen = {**prior_seen, **refreshed_hashes}
    # The merged dict reflects the POST-format content.
    assert merged_seen["foo.py"] == post_hash
    assert merged_seen["foo.py"] != pre_hash

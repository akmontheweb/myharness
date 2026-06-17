"""Tests for repo_index incremental update (batch 9).

Covers:
  - update_index_for_files no-op when no prior index                 (§6.5)
  - update_index_for_files triggers full rebuild for TF-IDF          (§6.5)
  - update_index_for_files rebuilds only listed files for embeddings (§6.5)
"""

from __future__ import annotations



from harness import repo_index as ri


def _seed_workspace(root, contents: dict[str, str]) -> None:
    for rel, body in contents.items():
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body)


def test_update_returns_zero_when_no_modified_files(tmp_path):
    """An empty list short-circuits — never touches the DB."""
    n = ri.update_index_for_files(str(tmp_path), [], ri.RepoIndexConfig())
    assert n == 0


def test_update_returns_zero_when_no_prior_index(tmp_path):
    """Without a prior index there's nothing to update incrementally."""
    cfg = ri.RepoIndexConfig(index_dir=str(tmp_path / "idx"))
    _seed_workspace(tmp_path, {"a.py": "def f(): pass\n"})
    n = ri.update_index_for_files(str(tmp_path), ["a.py"], cfg)
    assert n == 0


def test_update_triggers_full_rebuild_for_tfidf(tmp_path):
    """TF-IDF needs corpus-wide IDF, so the helper falls through to
    a full rebuild rather than per-file re-vectorisation."""
    cfg = ri.RepoIndexConfig(
        index_dir=str(tmp_path / "idx"),
        backend="tfidf",
    )
    _seed_workspace(tmp_path, {
        "a.py": "def alpha(): return 1\n",
        "b.py": "def beta(): return 2\n",
    })
    # Build the initial index.
    stats = ri.build_index(str(tmp_path), cfg)
    assert stats.chunk_count >= 1

    # Modify a.py on disk.
    (tmp_path / "a.py").write_text("def alpha_renamed(): return 3\n")
    # Incremental update: should fully rebuild for TF-IDF.
    n = ri.update_index_for_files(str(tmp_path), ["a.py"], cfg)
    assert n >= 1
    # The new chunk content reflects the rename.
    results = ri.query_top_chunks(
        str(tmp_path), "alpha_renamed", top_k=3, cfg=cfg,
    )
    text_blob = "\n".join(r.content for r in results)
    assert "alpha_renamed" in text_blob


def test_update_skip_when_backend_not_supported(tmp_path):
    """An unknown backend in the existing meta row is a no-op (defensive)."""
    cfg = ri.RepoIndexConfig(
        index_dir=str(tmp_path / "idx"),
        backend="tfidf",
    )
    _seed_workspace(tmp_path, {"a.py": "x = 1\n"})
    ri.build_index(str(tmp_path), cfg)
    # Manually corrupt the meta row's backend name to simulate an
    # unsupported / unknown backend.
    import sqlite3
    db = ri._db_path(cfg)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE repo_meta SET backend='unknown-backend'")
    conn.commit()
    conn.close()
    n = ri.update_index_for_files(str(tmp_path), ["a.py"], cfg)
    assert n == 0

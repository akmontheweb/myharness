"""Tests for harness/impact.py:_detect_source_root.

The helper picks the dominant top-level directory containing source files,
so the LLM and the patcher can constrain new code to the workspace's
existing layout. Returns None when the layout is flat or ambiguous.
"""

from __future__ import annotations

import os
from pathlib import Path

from harness.impact import _detect_source_root


def _touch(path: Path) -> None:
    """Create an empty file at ``path``, including parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


class TestPreferredNameBias:

    def test_app_dominant_workspace_returns_app(self, tmp_path):
        for name in ("calculator.py", "auth.py", "models.py"):
            _touch(tmp_path / "app" / name)
        _touch(tmp_path / "pyproject.toml")
        assert _detect_source_root(str(tmp_path)) == "app"

    def test_src_dominant_workspace_returns_src(self, tmp_path):
        for name in ("foo.ts", "bar.ts", "baz.ts"):
            _touch(tmp_path / "src" / name)
        _touch(tmp_path / "package.json")
        assert _detect_source_root(str(tmp_path)) == "src"

    def test_lib_dominant_workspace_returns_lib(self, tmp_path):
        for name in ("foo.rs", "bar.rs"):
            _touch(tmp_path / "lib" / name)
        _touch(tmp_path / "Cargo.toml")
        assert _detect_source_root(str(tmp_path)) == "lib"

    def test_preferred_name_beats_larger_non_preferred(self, tmp_path):
        # vendored 'random_thing/' has more files, but 'app/' is the
        # idiomatic location — we MUST prefer it. Otherwise the LLM
        # would be told to write code into a vendored tree.
        for i in range(10):
            _touch(tmp_path / "random_thing" / f"f{i}.py")
        for name in ("a.py", "b.py"):
            _touch(tmp_path / "app" / name)
        assert _detect_source_root(str(tmp_path)) == "app"


class TestNonPreferredFallback:

    def test_dominant_non_preferred_directory_wins(self, tmp_path):
        # Go monorepo with `internal/` would already be preferred, so use
        # an unusual name to exercise the fallback path: only `core/`
        # has any source. The dominance test (≥80% or >3-vs-0) fires.
        for i in range(5):
            _touch(tmp_path / "core" / f"f{i}.py")
        _touch(tmp_path / "Makefile")
        assert _detect_source_root(str(tmp_path)) == "core"


class TestAmbiguousLayouts:

    def test_flat_workspace_returns_none(self, tmp_path):
        # All source at root → no source-root to constrain to.
        for name in ("a.py", "b.py", "c.py"):
            _touch(tmp_path / name)
        _touch(tmp_path / "pyproject.toml")
        assert _detect_source_root(str(tmp_path)) is None

    def test_evenly_split_returns_none(self, tmp_path):
        # 2 in foo/, 2 in bar/, neither preferred → ambiguous.
        for name in ("a.py", "b.py"):
            _touch(tmp_path / "foo" / name)
            _touch(tmp_path / "bar" / name)
        assert _detect_source_root(str(tmp_path)) is None

    def test_empty_workspace_returns_none(self, tmp_path):
        # No source files anywhere.
        _touch(tmp_path / "README.md")
        assert _detect_source_root(str(tmp_path)) is None

    def test_only_tests_returns_none(self, tmp_path):
        # tests/ is in _NEVER_SOURCE_DIRS, so it doesn't count.
        for name in ("test_a.py", "test_b.py"):
            _touch(tmp_path / "tests" / name)
        assert _detect_source_root(str(tmp_path)) is None

    def test_only_docs_returns_none(self, tmp_path):
        # docs/ ignored even if it contains .py files.
        _touch(tmp_path / "docs" / "conf.py")
        assert _detect_source_root(str(tmp_path)) is None


class TestRobustness:

    def test_handles_hidden_dirs(self, tmp_path):
        # .git, .venv, .cache must be skipped without breaking detection.
        for name in ("a.py", "b.py"):
            _touch(tmp_path / "app" / name)
        _touch(tmp_path / ".venv" / "site-packages" / "ignored.py")
        _touch(tmp_path / ".git" / "hooks" / "stuff.py")
        assert _detect_source_root(str(tmp_path)) == "app"

    def test_returns_none_for_nonexistent_path(self, tmp_path):
        assert _detect_source_root(str(tmp_path / "does-not-exist")) is None
        assert _detect_source_root("") is None

    def test_multilanguage_workspace(self, tmp_path):
        # Polyglot Python+TS under src/ — counts both, preferred-name
        # bias still picks src.
        for name in ("a.py", "b.ts", "c.tsx"):
            _touch(tmp_path / "src" / name)
        assert _detect_source_root(str(tmp_path)) == "src"

    def test_excludes_node_modules(self, tmp_path):
        # node_modules is huge but must be ignored — otherwise every
        # JS workspace would land there.
        for i in range(50):
            _touch(tmp_path / "node_modules" / "vendor" / f"f{i}.js")
        for name in ("app.ts", "utils.ts"):
            _touch(tmp_path / "src" / name)
        assert _detect_source_root(str(tmp_path)) == "src"

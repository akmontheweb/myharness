"""Smoke tests for scripts/release.py — pure-function logic only."""

import importlib.util
import tempfile
from pathlib import Path

import pytest


def _load_release_module():
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("release_script", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBumpVersion:
    def test_patch_bump(self):
        release = _load_release_module()
        assert release.bump_version((1, 1, 0), "patch") == (1, 1, 1)

    def test_minor_bump(self):
        release = _load_release_module()
        assert release.bump_version((1, 1, 5), "minor") == (1, 2, 0)

    def test_major_bump(self):
        release = _load_release_module()
        assert release.bump_version((1, 2, 3), "major") == (2, 0, 0)


class TestRewritePyproject:
    def test_rewrites_only_version_line(self, monkeypatch):
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_pyproject = Path(tmpdir) / "pyproject.toml"
            fake_pyproject.write_text(
                '[project]\nname = "teane"\nversion = "1.1.0"\nrequires-python = ">=3.11"\n'
            )
            monkeypatch.setattr(release, "PYPROJECT", fake_pyproject)
            release.rewrite_pyproject("1.2.0")
            text = fake_pyproject.read_text()
            assert 'version = "1.2.0"' in text
            assert 'name = "teane"' in text  # unchanged


class TestRewriteChangelog:
    def test_inserts_new_section(self, monkeypatch):
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_changelog = Path(tmpdir) / "CHANGELOG.md"
            fake_changelog.write_text(
                "# Changelog\n\n## [Unreleased]\n\n### Added\n- new thing\n\n## [1.0.0] - 2026-01-01\n\nInitial.\n"
            )
            monkeypatch.setattr(release, "CHANGELOG", fake_changelog)
            release.rewrite_changelog("1.1.0", "2026-06-08")
            text = fake_changelog.read_text()
            assert "## [Unreleased]" in text
            assert "## [1.1.0] - 2026-06-08" in text
            # Both sections present, with the new one immediately after Unreleased
            assert text.index("## [Unreleased]") < text.index("## [1.1.0]")
            assert text.index("## [1.1.0]") < text.index("## [1.0.0]")

    def test_refuses_empty_unreleased(self, monkeypatch):
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_changelog = Path(tmpdir) / "CHANGELOG.md"
            fake_changelog.write_text(
                "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n\nInitial.\n"
            )
            monkeypatch.setattr(release, "CHANGELOG", fake_changelog)
            with pytest.raises(SystemExit):
                release.rewrite_changelog("1.1.0", "2026-06-08")

    def test_refuses_when_no_unreleased(self, monkeypatch):
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_changelog = Path(tmpdir) / "CHANGELOG.md"
            fake_changelog.write_text("# Changelog\n\n## [1.0.0] - 2026-01-01\n\nInitial.\n")
            monkeypatch.setattr(release, "CHANGELOG", fake_changelog)
            with pytest.raises(SystemExit):
                release.rewrite_changelog("1.1.0", "2026-06-08")

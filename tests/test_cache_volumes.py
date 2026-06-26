"""Tests for sandbox.cache_volumes + teane cache clear + corruption hint.

These cover the new pieces only (the existing test_harness.py block covers
the unchanged :ro-bind-mount path and the rest of the docker-cmd builder).
"""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Volume name derivation
# ---------------------------------------------------------------------------

class TestCacheVolumeName:
    def test_basic_tool_dirs(self):
        from harness.sandbox import _cache_volume_name
        assert _cache_volume_name("~/.cache/pip", "abc123") == "harness-pip-abc123"
        assert _cache_volume_name("~/.npm", "abc123") == "harness-npm-abc123"
        assert _cache_volume_name("~/.cargo", "abc123") == "harness-cargo-abc123"

    def test_basename_cache_uses_parent(self):
        # ~/.cache itself should not collapse to "cache"; the parent ".harness"
        # would be informative, but for our use case operators point at
        # tool-specific subdirs already. Bare /tmp/cache → harness-cache-x.
        from harness.sandbox import _cache_volume_name
        assert _cache_volume_name("/var/cache", "x") == "harness-var-x"

    def test_empty_session_falls_back_to_global(self):
        from harness.sandbox import _cache_volume_name
        assert _cache_volume_name("~/.cache/pip", None) == "harness-pip-global"
        assert _cache_volume_name("~/.cache/pip", "") == "harness-pip-global"
        assert _cache_volume_name("~/.cache/pip", "   ") == "harness-pip-global"

    def test_session_id_is_sanitised(self):
        # Volume names allow [A-Za-z0-9_-] only. Non-conforming session ids
        # get sanitised so docker doesn't reject the create.
        from harness.sandbox import _cache_volume_name
        assert _cache_volume_name("~/.npm", "sess with $") == "harness-npm-sess-with"

    def test_custom_prefix(self):
        from harness.sandbox import _cache_volume_name
        assert _cache_volume_name("~/.cache/pip", "sid", prefix="myorg") == "myorg-pip-sid"


# ---------------------------------------------------------------------------
# Docker cmd emission: flag off vs on, with vs without ensured volumes
# ---------------------------------------------------------------------------

class TestDockerCacheMountEmission:
    def test_flag_off_emits_readonly_host_bind(self, monkeypatch, tmp_path):
        # Regression: when cache_volumes is off, behaviour is byte-for-byte
        # the existing :ro host bind-mount.
        from harness.sandbox import DockerBackend
        # Make the cache path exist so the isdir check passes.
        cache_dir = tmp_path / "fake-pip-cache"
        cache_dir.mkdir()
        monkeypatch.setattr(os, "getuid", lambda: 0)
        backend = DockerBackend(image="python:3.12-slim")
        cmd = backend._build_docker_command(
            "pytest -q", "/work", allow_network=False,
            cache_mounts=[str(cache_dir)], extra_env={}, timeout_seconds=60,
        )
        # Joined for substring asserts on the mount triple.
        joined = " ".join(cmd)
        assert f"{cache_dir}:{cache_dir}:ro" in joined
        assert "type=volume" not in joined

    def test_flag_on_with_ensured_emits_writable_named_volume(self, monkeypatch, tmp_path):
        from harness.sandbox import DockerBackend, _cache_volume_name
        cache_dir = tmp_path / "fake-pip-cache"
        cache_dir.mkdir()
        monkeypatch.setattr(os, "getuid", lambda: 0)
        backend = DockerBackend(
            image="python:3.12-slim",
            cache_volumes_enabled=True,
            cache_volumes_session_id="sess-abc",
        )
        # Pre-populate the ensured set so we don't have to mock docker.
        vol = _cache_volume_name(str(cache_dir), "sess-abc")
        backend._ensured_volumes.add(vol)
        cmd = backend._build_docker_command(
            "pytest -q", "/work", allow_network=False,
            cache_mounts=[str(cache_dir)], extra_env={}, timeout_seconds=60,
        )
        joined = " ".join(cmd)
        # Writable named volume mount, NOT a :ro bind.
        assert f"type=volume,source={vol},target={cache_dir}" in joined
        assert f"{cache_dir}:{cache_dir}:ro" not in joined

    def test_flag_on_with_failed_ensure_falls_back_to_ro_bind(self, monkeypatch, tmp_path):
        # When _ensure_cache_volumes failed for a particular path, the
        # build should still proceed against the :ro host bind so cold-fill
        # is at least possible. (Better than dropping the cache entirely.)
        from harness.sandbox import DockerBackend
        cache_dir = tmp_path / "fake-pip-cache"
        cache_dir.mkdir()
        monkeypatch.setattr(os, "getuid", lambda: 0)
        backend = DockerBackend(
            image="python:3.12-slim",
            cache_volumes_enabled=True,
            cache_volumes_session_id="sess-abc",
        )
        # _ensured_volumes deliberately left empty.
        cmd = backend._build_docker_command(
            "pytest -q", "/work", allow_network=False,
            cache_mounts=[str(cache_dir)], extra_env={}, timeout_seconds=60,
        )
        joined = " ".join(cmd)
        assert f"{cache_dir}:{cache_dir}:ro" in joined
        assert "type=volume" not in joined


# ---------------------------------------------------------------------------
# SandboxExecutor threading
# ---------------------------------------------------------------------------

class TestSandboxExecutorThreadsCacheVolumesConfig:
    def test_executor_defaults_to_global_scope(self):
        # Default scope is "global" → the docker backend gets no session_id,
        # so _cache_volume_name collapses to a "global" slug shared across
        # sessions. This is the new default since the per-session isolation
        # forced every session to re-download wheels for no gain on the
        # single-tenant workstation case.
        from harness.sandbox import SandboxExecutor, DockerBackend
        executor = SandboxExecutor(
            workspace_path="/work",
            session_id="sess-xyz",
            sandbox_config={
                "backend": "docker",
                "docker_image": "python:3.12-slim",
                "cache_volumes": True,
            },
        )
        assert isinstance(executor.backend, DockerBackend)
        assert executor.backend.cache_volumes_enabled is True
        assert executor.backend.cache_volumes_session_id is None

    def test_executor_session_scope_forwards_session_id(self):
        # Operators who need per-tenant isolation set
        # ``sandbox.cache_volumes_scope = "session"`` — the session id then
        # gets baked into the volume name.
        from harness.sandbox import SandboxExecutor, DockerBackend
        executor = SandboxExecutor(
            workspace_path="/work",
            session_id="sess-xyz",
            sandbox_config={
                "backend": "docker",
                "docker_image": "python:3.12-slim",
                "cache_volumes": True,
                "cache_volumes_scope": "session",
            },
        )
        assert isinstance(executor.backend, DockerBackend)
        assert executor.backend.cache_volumes_enabled is True
        assert executor.backend.cache_volumes_session_id == "sess-xyz"

    def test_executor_defaults_cache_volumes_on(self):
        # cache_volumes default flipped from False to True. Operators don't
        # need to opt in; the cache just works.
        from harness.sandbox import SandboxExecutor
        executor = SandboxExecutor(
            workspace_path="/work",
            session_id="sess-xyz",
            sandbox_config={
                "backend": "docker",
                "docker_image": "python:3.12-slim",
            },
        )
        assert executor.backend.cache_volumes_enabled is True

    def test_executor_skips_cache_volumes_when_flag_off(self):
        from harness.sandbox import SandboxExecutor
        executor = SandboxExecutor(
            workspace_path="/work",
            session_id="sess-xyz",
            sandbox_config={
                "backend": "docker",
                "docker_image": "python:3.12-slim",
                "cache_volumes": False,
            },
        )
        assert executor.backend.cache_volumes_enabled is False


# ---------------------------------------------------------------------------
# Corruption-signature hint
# ---------------------------------------------------------------------------

class TestCacheCorruptionHint:
    @pytest.mark.parametrize("sample", [
        "ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE",
        "could not match the hash of foo-1.0-py3-none-any.whl",
        "npm ERR! code EINTEGRITY\nnpm ERR! sha512-deadbeef integrity mismatch",
        "cacache: integrity check failed for sha512-",
    ])
    def test_known_signatures_trigger_hint(self, sample):
        from harness.sandbox import _cache_corruption_hint
        hint = _cache_corruption_hint(sample)
        assert hint is not None
        assert "teane cache clear" in hint

    @pytest.mark.parametrize("sample", [
        "Successfully installed numpy-1.26.0",
        "",
        "just some sha512- mention in source code unrelated to integrity",
        "Build OK",
    ])
    def test_clean_output_no_hint(self, sample):
        from harness.sandbox import _cache_corruption_hint
        assert _cache_corruption_hint(sample) is None


# ---------------------------------------------------------------------------
# Variant cache env: package-cache routing on/off
# ---------------------------------------------------------------------------

class TestVariantCacheEnvSharedPackageCache:
    def test_default_per_variant_package_caches(self, tmp_path):
        from harness.speculative import _build_variant_cache_env
        env = _build_variant_cache_env(str(tmp_path))
        # Package caches present.
        assert "PIP_CACHE_DIR" in env
        assert "npm_config_cache" in env
        # Build-output caches present.
        assert "PYTHONPYCACHEPREFIX" in env
        assert "MYPY_CACHE_DIR" in env

    def test_shared_skips_package_caches_keeps_build_outputs(self, tmp_path):
        from harness.speculative import _build_variant_cache_env
        env = _build_variant_cache_env(
            str(tmp_path), use_shared_package_cache=True,
        )
        # Package caches OMITTED — tools use the shared named volume via
        # container default paths.
        for key in ("PIP_CACHE_DIR", "npm_config_cache", "MAVEN_OPTS"):
            assert key not in env, f"{key} should be omitted when shared cache is on"
        # Build-output caches still per-variant.
        for key in ("PYTHONPYCACHEPREFIX", "MYPY_CACHE_DIR", "RUFF_CACHE_DIR",
                    "PYTEST_ADDOPTS",
                    "GRADLE_USER_HOME", "XDG_CACHE_HOME"):
            assert key in env, f"{key} must remain per-variant"


# ---------------------------------------------------------------------------
# cmd_cache_clear: happy path, dry-run, idempotency, no docker
# ---------------------------------------------------------------------------

def _make_args(**kw):
    defaults = dict(
        workspace=None, session_id=None, yes=True, dry_run=False,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestCmdCacheClear:
    def _stub_docker(
        self, ls_output: str, rm_results: dict[str, int],
    ):
        """Returns a subprocess.run replacement for the cache-clear path."""
        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["docker", "volume", "ls"]:
                return subprocess.CompletedProcess(
                    cmd, returncode=0, stdout=ls_output, stderr="",
                )
            if cmd[:3] == ["docker", "volume", "rm"]:
                name = cmd[3]
                rc = rm_results.get(name, 0)
                return subprocess.CompletedProcess(
                    cmd, returncode=rc, stdout="",
                    stderr=("no such volume" if rc else ""),
                )
            raise AssertionError(f"Unexpected subprocess.run: {cmd!r}")
        return fake_run

    @pytest.mark.asyncio
    async def test_happy_path_removes_all_harness_volumes(self, monkeypatch, capsys):
        from harness import cli as cli_mod
        ls = "harness-pip-sess1\nharness-npm-sess1\nother-volume\n"
        monkeypatch.setattr(cli_mod, "shutil",
                            SimpleNamespace(which=lambda _: "/usr/bin/docker"))
        monkeypatch.setattr(cli_mod.subprocess, "run",
                            self._stub_docker(ls, {}))
        monkeypatch.setattr(cli_mod, "discover_config", lambda _: {})
        rc = await cli_mod.cmd_cache_clear(_make_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "harness-pip-sess1" in out
        assert "harness-npm-sess1" in out
        assert "other-volume" not in out  # not harness-prefixed
        assert "Removed 2 volume(s)." in out

    @pytest.mark.asyncio
    async def test_session_id_filters_volumes(self, monkeypatch, capsys):
        from harness import cli as cli_mod
        ls = "harness-pip-sess1\nharness-npm-sess1\nharness-pip-sess2\n"
        monkeypatch.setattr(cli_mod, "shutil",
                            SimpleNamespace(which=lambda _: "/usr/bin/docker"))
        monkeypatch.setattr(cli_mod.subprocess, "run",
                            self._stub_docker(ls, {}))
        monkeypatch.setattr(cli_mod, "discover_config", lambda _: {})
        rc = await cli_mod.cmd_cache_clear(_make_args(session_id="sess2"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "harness-pip-sess2" in out
        # Other sessions' volumes left alone.
        assert "harness-pip-sess1" not in out
        assert "harness-npm-sess1" not in out
        assert "Removed 1 volume(s)." in out

    @pytest.mark.asyncio
    async def test_dry_run_lists_but_does_not_remove(self, monkeypatch, capsys):
        from harness import cli as cli_mod
        ls = "harness-pip-sess1\n"

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["docker", "volume", "ls"]:
                return subprocess.CompletedProcess(cmd, 0, ls, "")
            raise AssertionError(
                f"Dry run must not invoke rm; got {cmd!r}"
            )
        monkeypatch.setattr(cli_mod, "shutil",
                            SimpleNamespace(which=lambda _: "/usr/bin/docker"))
        monkeypatch.setattr(cli_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(cli_mod, "discover_config", lambda _: {})
        rc = await cli_mod.cmd_cache_clear(_make_args(dry_run=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "dry run" in out
        assert "harness-pip-sess1" in out

    @pytest.mark.asyncio
    async def test_no_volumes_is_idempotent_success(self, monkeypatch, capsys):
        from harness import cli as cli_mod
        monkeypatch.setattr(cli_mod, "shutil",
                            SimpleNamespace(which=lambda _: "/usr/bin/docker"))
        monkeypatch.setattr(cli_mod.subprocess, "run",
                            self._stub_docker("", {}))
        monkeypatch.setattr(cli_mod, "discover_config", lambda _: {})
        rc = await cli_mod.cmd_cache_clear(_make_args())
        assert rc == 0
        assert "No teane cache volumes found" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_no_docker_binary_returns_zero(self, monkeypatch, capsys):
        # Cache-clear must not blow up on hosts without Docker; just say so.
        from harness import cli as cli_mod
        monkeypatch.setattr(cli_mod, "shutil",
                            SimpleNamespace(which=lambda _: None))
        monkeypatch.setattr(cli_mod, "discover_config", lambda _: {})
        rc = await cli_mod.cmd_cache_clear(_make_args())
        assert rc == 0
        assert "not found on PATH" in capsys.readouterr().err

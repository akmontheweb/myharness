"""Regression tests for the user skills directory loader (#5).

Covers:
    - ``load_user_skills_directory`` walks the configured directory,
      imports every ``*.py`` file (skipping ``_`` prefixed and non-Python
      files), and lets each module register skills via the global
      ``register`` helper.
    - Bad files (syntax errors, raising imports) log + skip without
      taking down the rest of the load.
    - The default directory (``~/.harness/user_skills``) silently no-ops
      when it doesn't exist.
    - ``register_builtin_skills(config=…)`` invokes the loader so user
      skills land in the registry on the runtime path that ``cmd_run``
      uses.
"""

from __future__ import annotations

import os
import textwrap

from harness.skills import (
    SkillRegistry,
    load_user_skills_directory,
    register_builtin_skills,
)


def _wipe_registry_prefix(prefix: str) -> None:
    reg = SkillRegistry()
    for name in list(reg._skills.keys()):  # type: ignore[attr-defined]
        if name.startswith(prefix):
            reg._skills.pop(name, None)  # type: ignore[attr-defined]


def _write_skill_file(directory: str, filename: str, body: str) -> str:
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(body))
    return path


def test_loader_returns_zero_when_directory_missing(tmp_path):
    cfg = {"skills": {"user_skills_dir": str(tmp_path / "nope")}}
    assert load_user_skills_directory(cfg) == 0


def test_loader_imports_user_skill_and_registers(tmp_path):
    _wipe_registry_prefix("user_demo")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill_file(
        str(skills_dir),
        "demo.py",
        """
        from harness.skills import (
            SkillParameter, SkillSchema, SkillType, ToolSkill, register,
        )

        async def _demo_call(value: str = "hi") -> str:
            return f"demo says {value}"

        register(ToolSkill(
            SkillSchema(
                name="user_demo_tool",
                description="A demonstrator tool installed via user skills directory.",
                skill_type=SkillType.TOOL,
                parameters=[SkillParameter("value", "string", "the payload", required=False)],
            ),
            fn=_demo_call,
        ))
        """,
    )
    cfg = {"skills": {"user_skills_dir": str(skills_dir)}}
    n = load_user_skills_directory(cfg)
    assert n == 1
    reg = SkillRegistry()
    assert reg.get("user_demo_tool") is not None
    _wipe_registry_prefix("user_demo")


def test_loader_skips_underscore_prefixed_and_non_py(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    # underscore-prefixed → skipped
    _write_skill_file(
        str(skills_dir),
        "_helper.py",
        "raise RuntimeError('this file should not be imported')",
    )
    # non-.py → skipped
    _write_skill_file(
        str(skills_dir),
        "notes.txt",
        "just a doc file",
    )
    cfg = {"skills": {"user_skills_dir": str(skills_dir)}}
    assert load_user_skills_directory(cfg) == 0


def test_loader_bad_file_does_not_abort_remaining_loads(tmp_path, caplog):
    _wipe_registry_prefix("user_after_bad")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    # File that crashes at import time
    _write_skill_file(
        str(skills_dir),
        "broken.py",
        "raise RuntimeError('intentional failure')",
    )
    # A good file that comes alphabetically later — proves the loader
    # didn't abort after the bad one.
    _write_skill_file(
        str(skills_dir),
        "zlater.py",
        """
        from harness.skills import (
            SkillParameter, SkillSchema, SkillType, ToolSkill, register,
        )
        async def _f(**_kw):
            return "ok"
        register(ToolSkill(
            SkillSchema(
                name="user_after_bad",
                description="Loaded after a broken file.",
                skill_type=SkillType.TOOL,
                parameters=[],
            ),
            fn=_f,
        ))
        """,
    )
    cfg = {"skills": {"user_skills_dir": str(skills_dir)}}
    with caplog.at_level("WARNING"):
        n = load_user_skills_directory(cfg)
    # one good import; the bad one logged and was skipped
    assert n == 1
    reg = SkillRegistry()
    assert reg.get("user_after_bad") is not None
    assert any("broken.py" in r.message for r in caplog.records)
    _wipe_registry_prefix("user_after_bad")


def test_register_builtin_skills_invokes_user_loader(tmp_path):
    _wipe_registry_prefix("user_via_builtin")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill_file(
        str(skills_dir),
        "viabuiltin.py",
        """
        from harness.skills import (
            SkillParameter, SkillSchema, SkillType, ToolSkill, register,
        )
        async def _f(**_kw):
            return "ok"
        register(ToolSkill(
            SkillSchema(
                name="user_via_builtin",
                description="Loaded by register_builtin_skills.",
                skill_type=SkillType.TOOL,
                parameters=[],
            ),
            fn=_f,
        ))
        """,
    )
    cfg = {"skills": {"user_skills_dir": str(skills_dir)}}
    register_builtin_skills(config=cfg)
    reg = SkillRegistry()
    assert reg.get("user_via_builtin") is not None
    _wipe_registry_prefix("user_via_builtin")


def test_loader_default_directory_when_missing_no_crash():
    """No config + no ~/.harness/user_skills (or legacy ~/.harness/skills)
    → returns 0, no exception."""
    # The default path likely doesn't exist on the CI host; even if it
    # does, loading it must not crash the test. We don't assert on the
    # return value beyond "non-negative int".
    n = load_user_skills_directory(None)
    assert isinstance(n, int)
    assert n >= 0


# ---------------------------------------------------------------------------
# Default-path resolution + legacy fallback
# ---------------------------------------------------------------------------

def test_default_user_skills_dir_is_user_skills_not_skills():
    """The default path moved from ~/.harness/skills to ~/.harness/user_skills
    to disambiguate from the bundled markdown directory inside the
    installed package. Pin the new default so a future refactor that
    reverts this rename fails CI immediately."""
    from harness.skills import _DEFAULT_USER_SKILLS_DIR
    assert _DEFAULT_USER_SKILLS_DIR == "~/.harness/user_skills"


def test_resolver_honours_explicit_config_over_default(tmp_path):
    """An operator who pins user_skills_dir always wins — no fallback
    logic kicks in for an explicit value, even if that value points at
    the legacy path."""
    from harness.skills import _resolve_user_skills_dir
    legacy_like = tmp_path / "my-pinned-path"
    legacy_like.mkdir()
    resolved = _resolve_user_skills_dir(
        {"skills": {"user_skills_dir": str(legacy_like)}}
    )
    assert resolved == str(legacy_like)


def test_resolver_uses_new_default_when_both_dirs_missing(monkeypatch, tmp_path):
    """Fresh install: neither directory exists. The resolver must return
    the new default so an operator who creates the directory afterward
    lands on the modern path."""
    from harness import skills as _skills_module
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Reset the warned flag so a prior test run doesn't suppress the
    # log we'd want to see if the fallback path fired (it shouldn't here).
    monkeypatch.setattr(_skills_module, "_legacy_fallback_warned", False)
    resolved = _skills_module._resolve_user_skills_dir(None)
    assert resolved == str(fake_home / ".harness" / "user_skills")


def test_resolver_falls_back_to_legacy_when_only_legacy_exists(
    monkeypatch, tmp_path, caplog,
):
    """Existing install: the operator has files at ~/.harness/skills but
    never set the config key. The resolver must return the legacy path
    so their skills keep loading, AND emit a one-time INFO so they know
    to migrate."""
    import logging
    from harness import skills as _skills_module
    fake_home = tmp_path / "home"
    legacy = fake_home / ".harness" / "skills"
    legacy.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(_skills_module, "_legacy_fallback_warned", False)
    with caplog.at_level(logging.INFO, logger="harness.skills"):
        resolved = _skills_module._resolve_user_skills_dir(None)
    assert resolved == str(legacy)
    assert any("legacy user-skills directory" in r.message for r in caplog.records)
    assert any("~/.harness/user_skills" in r.message
               or "user_skills" in r.message for r in caplog.records)


def test_resolver_prefers_new_default_when_both_dirs_exist(monkeypatch, tmp_path):
    """When BOTH directories exist, the new default wins — no fallback
    log fires. Lets an operator stage the migration by creating the new
    directory before deleting the old one."""
    from harness import skills as _skills_module
    fake_home = tmp_path / "home"
    (fake_home / ".harness" / "user_skills").mkdir(parents=True)
    (fake_home / ".harness" / "skills").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(_skills_module, "_legacy_fallback_warned", False)
    resolved = _skills_module._resolve_user_skills_dir(None)
    assert resolved == str(fake_home / ".harness" / "user_skills")
    # And no deprecation log when the operator already migrated.
    assert _skills_module._legacy_fallback_warned is False


def test_legacy_fallback_warning_fires_only_once(monkeypatch, tmp_path, caplog):
    """The deprecation log must fire ONCE per process — not on every
    harness run inside a long-lived dashboard. Mirrors the once-per-
    process discipline used elsewhere in the harness."""
    import logging
    from harness import skills as _skills_module
    fake_home = tmp_path / "home"
    (fake_home / ".harness" / "skills").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(_skills_module, "_legacy_fallback_warned", False)
    with caplog.at_level(logging.INFO, logger="harness.skills"):
        _skills_module._resolve_user_skills_dir(None)
        first_count = sum(
            1 for r in caplog.records if "legacy user-skills" in r.message
        )
        _skills_module._resolve_user_skills_dir(None)
        _skills_module._resolve_user_skills_dir(None)
        final_count = sum(
            1 for r in caplog.records if "legacy user-skills" in r.message
        )
    assert first_count == 1
    assert final_count == 1

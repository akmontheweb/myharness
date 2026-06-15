"""Regression tests for the user skills directory loader (#5).

Covers:
    - ``load_user_skills_directory`` walks the configured directory,
      imports every ``*.py`` file (skipping ``_`` prefixed and non-Python
      files), and lets each module register skills via the global
      ``register`` helper.
    - Bad files (syntax errors, raising imports) log + skip without
      taking down the rest of the load.
    - The default directory (``~/.harness/skills``) silently no-ops when
      it doesn't exist.
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
    """No config + no ~/.harness/skills → returns 0, no exception."""
    # The default path likely doesn't exist on the CI host; even if it
    # does, loading it must not crash the test. We don't assert on the
    # return value beyond "non-negative int".
    n = load_user_skills_directory(None)
    assert isinstance(n, int)
    assert n >= 0

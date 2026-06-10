"""P1.2 regression: the patcher's atomic write must refuse to write through
a symlink at the destination path.

Without this guard, an attacker (or a stale dev-environment symlink) at
e.g. ``pyproject.toml -> ~/.ssh/authorized_keys`` could ride the first LLM
patch onto a sensitive target. ``safe_resolve`` rejects symlinks that
escape the workspace, but in-workspace symlinks to dotfiles bypass it.
"""

from __future__ import annotations

import os
import sys

import pytest


@pytest.mark.asyncio
async def test_awrite_refuses_through_symlink(tmp_path):
    from harness.patcher import _awrite

    if not hasattr(os, "symlink"):
        pytest.skip("os.symlink unavailable on this platform")

    target = tmp_path / "sensitive_file.txt"
    target.write_text("ORIGINAL — must not be overwritten\n")

    link = tmp_path / "decoy.py"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        if sys.platform == "win32":
            pytest.skip(f"Windows symlinks require dev mode / privileges: {exc}")
        raise

    with pytest.raises(PermissionError, match="symlink"):
        await _awrite(str(link), "MALICIOUS WRITE")

    # Critical: the symlink target must be unmodified.
    assert target.read_text() == "ORIGINAL — must not be overwritten\n"


@pytest.mark.asyncio
async def test_awrite_allows_normal_file(tmp_path):
    """Sanity check: the symlink guard must not break the normal write path."""
    from harness.patcher import _awrite

    target = tmp_path / "regular.txt"
    target.write_text("before\n")
    await _awrite(str(target), "after\n")
    assert target.read_text() == "after\n"
    assert not target.is_symlink()


@pytest.mark.asyncio
async def test_awrite_allows_new_file(tmp_path):
    """When the destination doesn't exist, the symlink check is a no-op."""
    from harness.patcher import _awrite

    target = tmp_path / "fresh.txt"
    assert not target.exists()
    await _awrite(str(target), "hello\n")
    assert target.read_text() == "hello\n"

"""OS-dispatch primitives.

Every place in the harness that needs to branch on the host OS goes
through this module. Centralising the dispatch gives us:

  - one place to look when something OS-specific breaks
  - one place to mock from Linux-side tests
    (``monkeypatch.setattr("harness._platform.is_windows", lambda: True)``)
  - one convention — instead of inline ``platform.system() == "Windows"``
    scattered through sandbox.py and elsewhere

The module is scoped to **dispatch primitives only** — detect, shell,
paths, temp dir. Higher-level helpers (file locking) live in their own
modules. Business logic stays out of here on purpose.

POSIX behaviour is preserved byte-identically: ``harness_temp_dir()``
returns ``/tmp/.harness`` on Linux and macOS, not the per-user
``/var/folders/<hash>/T/.harness`` that ``tempfile.gettempdir()`` would
give on macOS. Anything currently watching ``/tmp/.harness`` continues
to work.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import tempfile
from typing import Optional


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------
# These call ``platform.system()`` on every invocation rather than caching
# the result at import time. The cost is a single dict lookup inside the
# stdlib (it's already memoised internally) and the benefit is testability:
# Linux-side tests can monkeypatch ``harness._platform.platform.system`` (or
# patch ``is_windows`` directly) to exercise the Windows branches.

def is_windows() -> bool:
    return platform.system() == "Windows"


def is_linux() -> bool:
    return platform.system() == "Linux"


def is_macos() -> bool:
    return platform.system() == "Darwin"


# ---------------------------------------------------------------------------
# Temp directories
# ---------------------------------------------------------------------------

def harness_temp_dir(subdir: str = ".harness") -> str:
    """Return the harness's per-machine temp directory.

    On Linux/macOS this is ``/tmp/<subdir>`` — byte-identical to the
    pre-existing hardcoded ``/tmp/.harness`` defaults. On Windows it
    resolves to ``%TEMP%\\<subdir>`` (typically
    ``C:\\Users\\<u>\\AppData\\Local\\Temp\\<subdir>``).

    Naive use of ``tempfile.gettempdir()`` everywhere would silently
    relocate macOS users from ``/tmp`` to ``/var/folders/<hash>/T``,
    breaking anything that watches the legacy location — hence the
    explicit POSIX branch.

    Passing an empty ``subdir`` returns just the parent temp dir
    (used by dashboard's path-containment root).
    """
    if is_windows():
        base = tempfile.gettempdir()
        return os.path.join(base, subdir) if subdir else base
    return f"/tmp/{subdir}" if subdir else "/tmp"


# ---------------------------------------------------------------------------
# Shell dispatch
# ---------------------------------------------------------------------------

_SH_PATH: Optional[str] = None
_SH_PROBED = False


def posix_shell_path() -> Optional[str]:
    """Return the path to a POSIX ``sh`` if one is on PATH, else ``None``.

    Cached after the first probe so we don't pay ``shutil.which`` per
    schedule hook fire. Use ``reset_posix_shell_probe()`` from tests
    that need to force re-detection.
    """
    global _SH_PATH, _SH_PROBED
    if not _SH_PROBED:
        _SH_PATH = shutil.which("sh")
        _SH_PROBED = True
    return _SH_PATH


def reset_posix_shell_probe() -> None:
    """Test hook — clear the cached ``sh`` probe result."""
    global _SH_PATH, _SH_PROBED
    _SH_PATH = None
    _SH_PROBED = False


def shell_argv(command: str, *, workdir: Optional[str] = None) -> list[str]:
    """Build the argv that runs ``command`` under a shell.

    POSIX (Linux/macOS): ``["sh", "-c", "cd <workdir> && <command>"]`` —
    workdir shlex-quoted. This matches the existing ``BareBackend.run``
    POSIX branch byte-for-byte.

    Windows: prefer ``sh`` if Git Bash / WSL exposed one on PATH (so
    operator-authored POSIX hooks behave as documented). Otherwise fall
    back to ``cmd /c`` with the ``/d`` switch so ``cd`` can cross drive
    letters. The Windows fallback matches the existing ``BareBackend.run``
    Windows branch byte-for-byte.

    The caller is responsible for any warning when the Windows fallback
    is taken — this helper just dispatches.
    """
    if is_windows():
        sh = posix_shell_path()
        if sh:
            if workdir:
                inner = f"cd {shlex.quote(workdir)} && {command}"
            else:
                inner = command
            return [sh, "-c", inner]
        # cmd.exe fallback — /d lets `cd` cross drive letters.
        if workdir:
            inner = f'cd /d "{workdir}" && {command}'
        else:
            inner = command
        return ["cmd", "/c", inner]
    # POSIX branch.
    if workdir:
        inner = f"cd {shlex.quote(workdir)} && {command}"
    else:
        inner = command
    return ["sh", "-c", inner]


# ---------------------------------------------------------------------------
# Path components
# ---------------------------------------------------------------------------

def split_path_components(p: str) -> list[str]:
    """Split a path into ``/``-delimited components.

    On Windows, normalise ``\\`` to ``/`` first so validators built
    around POSIX-shaped paths still detect traversal / absolute prefixes
    when a user-supplied path uses backslashes.

    On POSIX, **do not** normalise — backslash is a legal filename
    character on Linux/macOS, and a file named ``weird\\dir/file.py``
    must still split as ``["weird\\dir", "file.py"]`` (one directory
    with a backslash in its name + one file), not as three components.
    """
    if is_windows():
        p = p.replace("\\", "/")
    return p.split("/")

"""
Pre-build relative-import resolver.

Catches the failure mode where generated code imports `./components/X`
but `X.jsx` (or any extension variant) does not exist anywhere — the
class of bug that produced the CIOD session's "Could not resolve
./components/Dashboard" build failure.

Runs as ``link_check_node`` BEFORE compiler_node. The build only fires
when every relative import resolves. Unresolved imports surface as
structured ``LINK_BROKEN`` diagnostics that flow into the same repair
loop as any other build error — the LLM can either correct the import
path or generate the missing file.

Coverage:
    - JavaScript / TypeScript: ``import X from './Y'``,
      ``import { X } from './Y'``, ``import './Y'``,
      ``require('./Y')``, dynamic ``import('./Y')``.
      Resolution rules match Node/Vite: try the literal path, then
      add .js / .jsx / .ts / .tsx / .mjs / .cjs, then try
      ``./Y/index.{js,jsx,ts,tsx}``.
    - Python: ``from .module import X`` and ``from ..pkg.mod import X``.
      Resolution: walks the dotted path from the source file's package,
      tries both ``mod.py`` and ``mod/__init__.py``.

Out of scope (handed to the build):
    - Bare-name imports (``import React from 'react'``) — those resolve
      via ``node_modules``; missing-dep autofix handles them.
    - Path aliases (``@/components/X``, ``~/...``, tsconfig ``paths``) —
      resolver would need to read the project's tsconfig / vite alias
      config. v1 skips: the LLM repair loop catches these as before.

Design notes:
    - All file I/O is best-effort. A scan that hits a broken symlink or
      a permission-denied file simply logs and continues. Never raise
      out of this module — link checking is advisory.
    - Diagnostics carry enough context for both the autofix path and
      the repair-LLM prompt: the importing file's relative path, the
      literal import string, and a hint at where the resolver looked.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# Directories the scan never descends into. Mirrors harness.impact's
# never-source list; duplicated here to avoid the circular import that
# importing impact at module load time would create.
_NEVER_SOURCE_DIRS: frozenset[str] = frozenset({
    "node_modules", "__pycache__", "dist", "build", "target", "out",
    ".git", ".tox", ".venv", "venv", ".next", ".nuxt", ".svelte-kit",
    "coverage", "htmlcov", ".pytest_cache", ".mypy_cache", ".ruff_cache",
})

_JS_EXTS: tuple[str, ...] = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_JS_SOURCE_EXTS: tuple[str, ...] = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_PY_EXTS: tuple[str, ...] = (".py", ".pyi")

# JS/TS import shapes the scanner picks up. The captured group is the
# import string (everything inside the quotes).
#
#   import X from './path'
#   import { X } from './path'
#   import * as X from './path'
#   import './path'                   (side-effect)
#   export { X } from './path'
#   const X = require('./path')
#   const X = await import('./path')
#
# We deliberately keep the regex coarse — false-negatives on
# multi-line or unusually-formatted imports just mean fewer
# diagnostics, never a wrong one. Each pattern requires the import
# string to start with `./` or `../` or `/` so bare-name imports
# (`'react'`) don't match here.
_JS_IMPORT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # static `import ... from '...'` / `export ... from '...'`
    re.compile(
        r"""(?:^|\s)(?:import|export)\b[^'"]*?\bfrom\s+['"](?P<spec>\.{1,2}/[^'"]+|/[^'"]+)['"]""",
        re.MULTILINE,
    ),
    # side-effect import: `import '...'`
    re.compile(
        r"""(?:^|\s)import\s+['"](?P<spec>\.{1,2}/[^'"]+|/[^'"]+)['"]""",
        re.MULTILINE,
    ),
    # CommonJS require + dynamic import
    re.compile(
        r"""\b(?:require|import)\s*\(\s*['"](?P<spec>\.{1,2}/[^'"]+|/[^'"]+)['"]\s*\)""",
    ),
)

# Python relative imports — `from .x import y` or `from ..pkg.mod import y`.
# We don't match absolute-package imports (`from foo import x`) because the
# resolver here only catches LOCAL drift; missing third-party packages are
# the missing-dep autofix's job.
_PY_RELATIVE_IMPORT_RE = re.compile(
    r"^\s*from\s+(?P<dots>\.+)(?P<tail>[a-zA-Z_][\w.]*)?\s+import\s+",
    re.MULTILINE,
)


@dataclass(frozen=True)
class BrokenLink:
    """A relative import that did not resolve to any file on disk.

    Attributes:
        source_file: Workspace-relative path of the importing file.
        import_spec: The literal string inside the import quotes
            (e.g. ``"./components/Dashboard"``).
        searched_paths: The candidate absolute paths the resolver tried
            (helpful in the LLM repair prompt — tells the model
            exactly which extensions were checked).
        language: ``"js"`` or ``"python"``.
    """
    source_file: str
    import_spec: str
    searched_paths: tuple[str, ...]
    language: str


def scan_workspace_for_broken_imports(
    workspace_path: str,
    *,
    src_subdirs: Optional[Iterable[str]] = None,
) -> list[BrokenLink]:
    """Walk ``workspace_path`` and return every unresolved relative import.

    Args:
        workspace_path: Project root. Never-source dirs (node_modules,
            dist, build, ...) are pruned during walk.
        src_subdirs: Optional whitelist of subdirectories to scan.
            ``None`` means scan everything under workspace_path.

    Returns:
        A list of :class:`BrokenLink` records, one per unresolved import.
        Empty when the workspace is clean (or has no source files).

    Never raises — best-effort I/O. A broken symlink, permission denied,
    or undecodable file simply logs at debug level and skips.
    """
    if not workspace_path or not os.path.isdir(workspace_path):
        return []

    broken: list[BrokenLink] = []
    roots: list[str] = []
    if src_subdirs:
        for sub in src_subdirs:
            cand = os.path.join(workspace_path, sub)
            if os.path.isdir(cand):
                roots.append(cand)
    if not roots:
        roots = [workspace_path]

    for root in roots:
        for sub_root, sub_dirs, sub_files in os.walk(root):
            sub_dirs[:] = [
                d for d in sub_dirs
                if not d.startswith(".") and d not in _NEVER_SOURCE_DIRS
            ]
            for fname in sub_files:
                src_abs = os.path.join(sub_root, fname)
                src_rel = os.path.relpath(src_abs, workspace_path)
                ext = os.path.splitext(fname)[1].lower()
                if ext in _JS_SOURCE_EXTS:
                    broken.extend(_scan_js_file(src_abs, src_rel, workspace_path))
                elif ext in _PY_EXTS:
                    broken.extend(_scan_python_file(src_abs, src_rel, workspace_path))
    return broken


# ---------------------------------------------------------------------------
# JS / TS resolver
# ---------------------------------------------------------------------------

def _scan_js_file(
    src_abs: str, src_rel: str, workspace_path: str,
) -> list[BrokenLink]:
    """Return broken-link records for relative imports in one JS/TS file."""
    try:
        with open(src_abs, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
    except OSError as exc:
        logger.debug("[link_check] Could not read %s: %s", src_abs, exc)
        return []

    seen_specs: set[str] = set()
    broken: list[BrokenLink] = []
    src_dir = os.path.dirname(src_abs)

    for pattern in _JS_IMPORT_PATTERNS:
        for match in pattern.finditer(body):
            spec = match.group("spec")
            if spec in seen_specs:
                continue
            seen_specs.add(spec)
            # Resolve the spec relative to the importing file's dir.
            # An absolute spec ("/x") is rooted in workspace_path — Vite's
            # default, also what most tsconfig/jsconfig path:'/'-style aliases
            # produce. v1 keeps it simple: treat / as workspace-relative.
            if spec.startswith("/"):
                target_base = os.path.join(workspace_path, spec.lstrip("/"))
            else:
                target_base = os.path.normpath(os.path.join(src_dir, spec))
            candidates = _js_resolution_candidates(target_base)
            if not any(os.path.isfile(c) for c in candidates):
                broken.append(BrokenLink(
                    source_file=src_rel,
                    import_spec=spec,
                    searched_paths=tuple(candidates),
                    language="js",
                ))
    return broken


def _js_resolution_candidates(base_abs: str) -> list[str]:
    """Return the ordered list of absolute paths a Node/Vite resolver would
    try for an import that points at ``base_abs``.

    1. Literal path with whatever extension was written.
    2. Append each JS/TS extension.
    3. Treat as directory: ``base_abs/index.{js,jsx,ts,tsx,mjs,cjs}``.
    """
    candidates: list[str] = []
    # 1. literal — captures users who write the extension explicitly.
    candidates.append(base_abs)
    # 2. extension fallbacks
    if not any(base_abs.endswith(ext) for ext in _JS_EXTS):
        for ext in _JS_EXTS:
            candidates.append(base_abs + ext)
    # 3. directory + index
    for ext in _JS_EXTS:
        candidates.append(os.path.join(base_abs, f"index{ext}"))
    return candidates


# ---------------------------------------------------------------------------
# Python resolver
# ---------------------------------------------------------------------------

def _scan_python_file(
    src_abs: str, src_rel: str, workspace_path: str,
) -> list[BrokenLink]:
    """Return broken-link records for relative imports in one Python file.

    Python relative imports ascend the package tree by leading dots:
    ``from . import x`` → same package; ``from ..pkg.mod import y`` →
    parent package, then walk ``pkg.mod``. We resolve dots against the
    file's directory and try ``<target>.py`` or ``<target>/__init__.py``.
    """
    try:
        with open(src_abs, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
    except OSError as exc:
        logger.debug("[link_check] Could not read %s: %s", src_abs, exc)
        return []

    broken: list[BrokenLink] = []
    src_dir = os.path.dirname(src_abs)
    seen_specs: set[str] = set()

    for match in _PY_RELATIVE_IMPORT_RE.finditer(body):
        dots = match.group("dots")
        tail = match.group("tail") or ""
        spec = dots + tail
        if spec in seen_specs:
            continue
        seen_specs.add(spec)

        # Ascend one directory per dot beyond the first.
        cur = src_dir
        for _ in range(len(dots) - 1):
            parent = os.path.dirname(cur)
            if parent == cur:
                # Walked past workspace root — the import can't resolve.
                cur = ""
                break
            cur = parent
        if not cur:
            broken.append(BrokenLink(
                source_file=src_rel,
                import_spec=spec,
                searched_paths=("<above workspace root>",),
                language="python",
            ))
            continue
        # tail is dotted; walk it as path segments.
        segments = tail.split(".") if tail else []
        target = os.path.join(cur, *segments) if segments else cur
        candidates = [target + ".py", os.path.join(target, "__init__.py")]
        if not any(os.path.isfile(c) for c in candidates):
            broken.append(BrokenLink(
                source_file=src_rel,
                import_spec=spec,
                searched_paths=tuple(candidates),
                language="python",
            ))
    return broken


# ---------------------------------------------------------------------------
# Diagnostic shaping (for compiler_node interop)
# ---------------------------------------------------------------------------

def broken_links_to_diagnostics(
    broken: list[BrokenLink],
) -> list[dict[str, object]]:
    """Convert :class:`BrokenLink` records to the diagnostic-dict shape the
    repair loop already consumes (matches ``compiler_errors``).

    Each diagnostic carries ``error_code="LINK_BROKEN"`` so downstream
    routing can recognize it without re-parsing the message.
    """
    out: list[dict[str, object]] = []
    for link in broken:
        searched = "\n".join(f"    - {p}" for p in link.searched_paths)
        out.append({
            "file": link.source_file,
            "line": 0,
            "column": 0,
            "severity": "error",
            "error_code": "LINK_BROKEN",
            "message": (
                f"Relative import {link.import_spec!r} in "
                f"{link.source_file} does not resolve to any file on "
                f"disk. The build will fail. Either correct the import "
                f"path or create the missing file."
            ),
            "semantic_context": (
                f"Resolver tried these paths:\n{searched}"
            ),
            "missing_symbol": link.import_spec,
            "language": link.language,
        })
    return out

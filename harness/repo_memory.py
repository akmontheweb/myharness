"""Per-repository session memory (#7).

Persists a short, accumulating markdown file per repository at
``~/.harness/memory/<repo_id>.md``. The file accumulates a structured
log of past sessions on the same repository (prompt summary, modified
files, exit status, date). The planner reads it at the start of every
``harness run`` and injects the recent entries as an extra system
message so subsequent sessions have context from prior work — a
poor-person's persistent memory, complementing the (single-thread)
LangGraph checkpoint.

Repository identity
===================
The file name is derived from a stable identifier:

  1. ``git remote get-url origin`` of the workspace, when a remote is
     configured. This gives the same identity across machines / clones
     so the same engineer benefits from cross-machine continuity.
  2. The absolute workspace path otherwise (greenfield / no-remote
     projects).

A 16-character SHA-256 hex prefix of whichever string we picked is
used as the filename. The prefix is short enough to type, long enough
to avoid collisions across realistic repo counts.

File shape
==========
A flat markdown log. Each entry is one ``## Session <session_id> —
<iso8601 date>`` heading followed by a few bullets. New entries
append. A FIFO trim drops the oldest entries when the file exceeds
``memory.max_bytes`` so the read path's prepended context stays
bounded.

Security
========
The memory file may contain workspace path fragments and session
metadata; it does NOT contain raw LLM transcripts. The harness's
existing secret redactor runs on conversation content before the
gateway dispatches it; we don't add another redaction pass here
because nothing this module writes touches LLM output directly.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Config
# ---------------------------------------------------------------------------

_DEFAULT_MEMORY_DIR = "~/.harness/memory"
_DEFAULT_MAX_BYTES = 100_000  # ~25K tokens — small enough to inject as context
_DEFAULT_MAX_INJECT_BYTES = 8_000  # cap on what we read back as planner context


@dataclass
class RepoMemoryConfig:
    enabled: bool = True
    dir: str = _DEFAULT_MEMORY_DIR
    max_bytes: int = _DEFAULT_MAX_BYTES
    # Separate cap for what gets injected into the planner context.
    # Smaller than max_bytes because file content includes full history;
    # injection only needs the recent tail.
    inject_max_bytes: int = _DEFAULT_MAX_INJECT_BYTES

    @classmethod
    def from_config(cls, config: Optional[dict[str, Any]]) -> "RepoMemoryConfig":
        section = ((config or {}).get("memory") or {})
        return cls(
            enabled=bool(section.get("enabled", True)),
            dir=str(section.get("dir", _DEFAULT_MEMORY_DIR)),
            max_bytes=int(section.get("max_bytes", _DEFAULT_MAX_BYTES)),
            inject_max_bytes=int(
                section.get("inject_max_bytes", _DEFAULT_MAX_INJECT_BYTES)
            ),
        )


# ---------------------------------------------------------------------------
# 2. Repo identity
# ---------------------------------------------------------------------------

def repo_identity(workspace_path: str) -> str:
    """Return a stable 16-char hex identity for ``workspace_path``.

    Prefers the configured ``origin`` remote URL (stable across clones);
    falls back to the absolute workspace path. Never raises — failures
    fall back silently to the path-based identity.
    """
    workspace_abs = os.path.abspath(workspace_path)
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=workspace_abs,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            seed = result.stdout.strip()
        else:
            seed = workspace_abs
    except (OSError, subprocess.TimeoutExpired):
        seed = workspace_abs
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def memory_file_path(workspace_path: str, cfg: RepoMemoryConfig) -> str:
    """Build the absolute path to the memory file for this workspace."""
    return os.path.join(
        os.path.expanduser(cfg.dir),
        f"{repo_identity(workspace_path)}.md",
    )


# ---------------------------------------------------------------------------
# 3. Read path
# ---------------------------------------------------------------------------

def read_repo_memory(
    workspace_path: str, cfg: Optional[RepoMemoryConfig] = None,
) -> str:
    """Read the memory file for ``workspace_path``.

    Returns the file content (UTF-8) capped at ``cfg.inject_max_bytes``;
    when the file exceeds the cap, the *tail* is returned (most recent
    entries) and a one-line truncation marker is prepended. Returns ``""``
    when the file does not exist or the read fails. Never raises.
    """
    cfg = cfg or RepoMemoryConfig()
    if not cfg.enabled:
        return ""
    path = memory_file_path(workspace_path, cfg)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        logger.debug("[repo_memory] read failed for %s: %s", path, exc)
        return ""
    if len(content.encode("utf-8")) <= cfg.inject_max_bytes:
        return content
    # Trim to the tail; align on section headings so we don't cut
    # mid-sentence. Build backwards from the newest section so the most
    # recent activity is always kept even when the byte cap is tight.
    sections = content.split("\n## ")
    body_sections = sections[1:]  # entries; sections[0] is the top header
    kept: list[str] = []
    size = 0
    for sec in reversed(body_sections):
        sec_full = "\n## " + sec
        sec_size = len(sec_full.encode("utf-8"))
        if size + sec_size > cfg.inject_max_bytes:
            break
        kept.append(sec_full)
        size += sec_size
    if not kept:
        # Even the newest section overflows the cap — truncate it.
        if body_sections:
            last = "\n## " + body_sections[-1]
            return last.encode("utf-8")[: cfg.inject_max_bytes].decode(
                "utf-8", errors="replace",
            )
        return content.encode("utf-8")[: cfg.inject_max_bytes].decode(
            "utf-8", errors="replace",
        )
    kept.reverse()
    return "".join(kept).lstrip("\n")


# ---------------------------------------------------------------------------
# 4. Append path
# ---------------------------------------------------------------------------

_HEADER_TEMPLATE = (
    "# myharness session memory\n"
    "<!-- Append-only log of past sessions on this repository. -->\n"
    "<!-- Trimmed FIFO at memory.max_bytes; oldest sections drop first. -->\n"
)


def append_session_note(
    workspace_path: str,
    *,
    session_id: str,
    prompt_summary: str,
    modified_files: list[str],
    exit_code: int,
    cfg: Optional[RepoMemoryConfig] = None,
    extra_notes: Optional[str] = None,
) -> Optional[str]:
    """Append one session entry to the per-repo memory file.

    The entry is a single ``## Session <id> — <iso8601 date>`` section
    with bullets for prompt, status, modified-file count, and (optional)
    extra notes. After appending, the file is trimmed FIFO to
    ``cfg.max_bytes`` by dropping the oldest sections — never the
    just-written entry.

    Returns the absolute memory file path on success, or ``None`` when
    memory is disabled or the write fails (failures log; the caller
    must not crash on a memory write failure).
    """
    cfg = cfg or RepoMemoryConfig()
    if not cfg.enabled:
        return None
    path = memory_file_path(workspace_path, cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    short_id = session_id.split("-")[0] if "-" in session_id else session_id
    status = "success" if exit_code == 0 else f"failed (exit {exit_code})"
    summary = (prompt_summary or "").strip().replace("\n", " ")
    if len(summary) > 200:
        summary = summary[:197] + "..."
    files_line = (
        f"{len(modified_files)} file(s) modified"
        if modified_files
        else "no files modified"
    )
    section = (
        f"\n## Session {short_id} — {now}\n"
        f"- Prompt: {summary or '(no prompt summary)'}\n"
        f"- Status: {status}\n"
        f"- Modified: {files_line}\n"
    )
    if modified_files:
        # Cap the file listing — the planner doesn't need every name,
        # just enough flavour to recognise recent work.
        preview = modified_files[:8]
        section += "  - " + "\n  - ".join(preview) + "\n"
        if len(modified_files) > len(preview):
            section += f"  - ... ({len(modified_files) - len(preview)} more)\n"
    if extra_notes:
        section += f"- Notes: {extra_notes.strip()}\n"

    try:
        prior = ""
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                prior = f.read()
        if not prior:
            prior = _HEADER_TEMPLATE
        combined = prior + section
        combined = _trim_to_max_bytes(combined, cfg.max_bytes)
        _atomic_write_text(path, combined)
    except OSError as exc:
        logger.warning("[repo_memory] write failed for %s: %s", path, exc)
        return None
    logger.info("[repo_memory] appended session %s to %s", short_id, path)
    return path


# ---------------------------------------------------------------------------
# 5. Helpers
# ---------------------------------------------------------------------------

def _trim_to_max_bytes(text: str, max_bytes: int) -> str:
    """FIFO-trim ``text`` by dropping the oldest ``## `` sections until
    it fits under ``max_bytes`` (UTF-8). Always preserves the top
    header and the final section."""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    parts = text.split("\n## ")
    if len(parts) <= 2:
        return text  # only header + one entry — leave alone
    head = parts[0]
    sections = parts[1:]
    # Always keep the last section (just-written entry).
    while len(sections) > 1:
        candidate_sections = sections[1:]
        candidate = head + "\n## " + "\n## ".join(candidate_sections)
        if len(candidate.encode("utf-8")) <= max_bytes:
            return candidate
        sections = candidate_sections
    # Single section + header still too large — return as-is, the
    # injection path will cap it again on read.
    return head + "\n## " + sections[0]


def _atomic_write_text(path: str, content: str) -> None:
    """Write text to ``path`` via ``<path>.tmp`` + ``os.replace`` so
    readers never see a half-written file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)

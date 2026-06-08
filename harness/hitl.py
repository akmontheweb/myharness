"""
Pluggable Human-in-the-Loop (HITL) transport.

All interactive prompts in the harness — gatekeeper approvals, repair hints,
deploy previews, purge confirmations, discovery Q&A — are routed through a
HitlChannel so the I/O surface can be swapped without touching call sites.

Built-in implementations:
  StdinChannel  — current stdin/print behaviour (default when no HARNESS_HITL_FILE)
  FileChannel   — pre-recorded answers loaded from a JSON file specified by
                  the HARNESS_HITL_FILE environment variable. Used for scripted
                  integration tests and CI runs without a TTY.

Extension path: implement HitlChannel and register it via set_channel().
A webhook implementation, for example, is a single new file.

Usage in call sites::

    from harness.hitl import get_channel

    choice = get_channel().prompt("Select action", ["a", "e", "m", "s"])
    ok = get_channel().confirm("Proceed?")
    hint = get_channel().notes("Enter feedback")
    get_channel().wait_for_manual_edit("/path/to/file.md")
"""

from __future__ import annotations

import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. HitlChannel ABC
# ---------------------------------------------------------------------------

class HitlChannel(ABC):
    """Abstract base for all HITL I/O transports."""

    @abstractmethod
    def prompt(
        self,
        message: str,
        options: list[str],
        default: Optional[str] = None,
    ) -> str:
        """
        Present a menu prompt and return the user's selection.

        Args:
            message: The prompt text (shown once before option list).
            options: Valid single-character (or short) answer strings.
            default: Answer returned automatically in non-interactive mode.
                     If None and the channel is non-interactive, raises.

        Returns:
            The selected option string (lowercased).
        """

    @abstractmethod
    def confirm(self, message: str, default: bool = False) -> bool:
        """Present a y/N confirmation. Returns True if confirmed."""

    @abstractmethod
    def notes(self, message: str) -> str:
        """
        Prompt for multi-word free-text input (e.g., a repair hint).
        Returns the text entered by the user (may be empty string).
        """

    @abstractmethod
    def wait_for_manual_edit(self, filepath: str) -> None:
        """
        Block until the user signals they have finished editing ``filepath``.
        In non-interactive channels, returns immediately.
        """

    def is_interactive(self) -> bool:
        """Return True when the channel is connected to a live human."""
        return False


# ---------------------------------------------------------------------------
# 2. StdinChannel
# ---------------------------------------------------------------------------

def _auto_approve() -> bool:
    """True when the environment requests non-interactive execution."""
    return (
        os.environ.get("CI", "").lower() == "true"
        or os.environ.get("HARNESS_AUTO_APPROVE", "").lower() == "true"
        or not sys.stdin.isatty()
    )


class StdinChannel(HitlChannel):
    """
    Interactive stdin/stdout channel — the default.

    Respects HARNESS_AUTO_APPROVE=true, CI=true, and non-TTY stdin by
    returning the ``default`` value without blocking. If no default is
    provided in auto-approve mode, the first option is used.
    """

    def is_interactive(self) -> bool:
        return not _auto_approve()

    def prompt(
        self,
        message: str,
        options: list[str],
        default: Optional[str] = None,
    ) -> str:
        opts_str = "/".join(options)
        if _auto_approve():
            chosen = default if default is not None else (options[0] if options else "")
            logger.info("[hitl] Auto-approved prompt %r → %r", message[:60], chosen)
            return chosen

        while True:
            try:
                answer = input(f"{message} [{opts_str}]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n[HITL] Input interrupted.", file=sys.stderr)
                return default if default is not None else (options[0] if options else "")
            if not options or answer in [o.lower() for o in options]:
                return answer

    def confirm(self, message: str, default: bool = False) -> bool:
        if _auto_approve():
            logger.info("[hitl] Auto-confirmed: %r → %s", message[:60], default)
            return default

        hint = "[Y/n]" if default else "[y/N]"
        try:
            answer = input(f"{message} {hint}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n[HITL] Input interrupted.", file=sys.stderr)
            return default
        if not answer:
            return default
        return answer in ("y", "yes")

    def notes(self, message: str) -> str:
        if _auto_approve():
            return ""
        try:
            return input(f"{message}\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[HITL] Input interrupted.", file=sys.stderr)
            return ""

    def wait_for_manual_edit(self, filepath: str) -> None:
        if _auto_approve():
            logger.info("[hitl] Auto-skipping wait_for_manual_edit: %s", filepath)
            return
        try:
            input(f"[HITL] Edit {filepath} then press Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            print("\n[HITL] Continuing.", file=sys.stderr)


# ---------------------------------------------------------------------------
# 3. FileChannel
# ---------------------------------------------------------------------------

class FileChannel(HitlChannel):
    """
    Pre-recorded answers loaded from a JSON file.

    The file path is taken from the HARNESS_HITL_FILE environment variable.
    File format::

        [
          {"prompt": "REQUIREMENTS", "answer": "a"},
          {"prompt": "deploy preview", "answer": "y"},
          {"prompt": "refine", "answer": "add more detail about auth"}
        ]

    Matching is by substring — the first entry whose ``prompt`` value is a
    substring of the actual prompt message (case-insensitive) is used.

    Unmatched prompts raise ``RuntimeError`` (fail-closed). This is
    intentional: a script that doesn't pre-record all prompts should fail
    loudly rather than silently proceeding or hanging.
    """

    def __init__(self, answers_path: str) -> None:
        with open(answers_path, "r", encoding="utf-8") as f:
            raw: list[dict[str, str]] = json.load(f)
        self._answers: list[tuple[str, str]] = [
            (entry["prompt"], entry["answer"]) for entry in raw
        ]
        self._used: set[int] = set()
        logger.info("[hitl:file] Loaded %d pre-recorded answers from %s", len(self._answers), answers_path)

    def _lookup(self, message: str) -> str:
        message_lower = message.lower()
        for i, (prompt, answer) in enumerate(self._answers):
            if prompt.lower() in message_lower:
                if i not in self._used:
                    self._used.add(i)
                    logger.info("[hitl:file] Matched prompt %r → %r", prompt, answer)
                    return answer
        raise RuntimeError(
            f"[hitl:file] No pre-recorded answer for prompt: {message[:120]!r}. "
            f"Add an entry to the HARNESS_HITL_FILE to cover this prompt."
        )

    def is_interactive(self) -> bool:
        return False

    def prompt(
        self,
        message: str,
        options: list[str],
        default: Optional[str] = None,
    ) -> str:
        answer = self._lookup(message)
        logger.info("[hitl:file] prompt → %r", answer)
        return answer

    def confirm(self, message: str, default: bool = False) -> bool:
        answer = self._lookup(message)
        return answer.lower() in ("y", "yes", "true", "1")

    def notes(self, message: str) -> str:
        return self._lookup(message)

    def wait_for_manual_edit(self, filepath: str) -> None:
        logger.info("[hitl:file] Skipping wait_for_manual_edit: %s", filepath)


# ---------------------------------------------------------------------------
# 4. Module-level channel registry
# ---------------------------------------------------------------------------

_channel: Optional[HitlChannel] = None


def get_channel() -> HitlChannel:
    """
    Return the active HITL channel.

    Selection order:
      1. A channel explicitly installed via set_channel().
      2. FileChannel when HARNESS_HITL_FILE env var is set.
      3. StdinChannel (default).
    """
    global _channel
    if _channel is not None:
        return _channel
    hitl_file = os.environ.get("HARNESS_HITL_FILE", "").strip()
    if hitl_file:
        _channel = FileChannel(hitl_file)
        return _channel
    _channel = StdinChannel()
    return _channel


def set_channel(channel: HitlChannel) -> None:
    """Install a specific channel — useful in tests and embeddings."""
    global _channel
    _channel = channel


def reset_channel() -> None:
    """Reset the channel to auto-detect on next call — use in tests."""
    global _channel
    _channel = None

"""
Interactive setup wizard for bare ``harness run`` invocations.

When the user types ``harness run`` with no flags, ``cmd_run`` calls
:func:`run_setup_wizard` to walk them through the minimum set of choices
needed to start a run: API keys (a prerequisite for any LLM call), then
workspace, prompt, --git mode, --new-build, and --discover. Everything
else falls back to the argparse defaults.

The wizard does NOT persist anything. Each bare ``harness run`` re-asks
every question. Model routing, sandbox backend, lintgate, deployment,
and budget all stay in ``config/config.json``.

Reuses :func:`harness.hitl.get_channel` for every non-secret prompt so
the wizard inherits the existing HITL infrastructure (file-replay for
tests, HTTP webhook for IDE plugins). API key prompts bypass HitlChannel
and use :func:`getpass.getpass` so keys never echo to the terminal.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

logger = logging.getLogger(__name__)


def run_setup_wizard(args: argparse.Namespace) -> argparse.Namespace:
    """Drive the interactive setup flow and mutate ``args`` in place.

    Order:
        0. API key check — prompt for any missing ``{PROVIDER}_API_KEY``
           env vars for models referenced by ``model_routing``.
        1. Workspace path.
        2. Engineering prompt / task.
        3. ``--git enable|disable``.
        4. ``--new-build true|false``.
        5. ``--discover true|false``.
        6. Summary + confirm. ``n`` loops back into the wizard from step 1.

    Returns ``args`` for caller convenience. Raises ``SystemExit(2)`` when
    invoked non-interactively or when the user declines to provide a
    required API key.
    """
    # Imported lazily so the wizard module doesn't drag cli.py's import
    # graph into every script that just wants to query argparse defaults.
    from harness.cli import ConfigError, load_raw_config
    from harness.hitl import get_channel

    channel = get_channel()
    if not channel.is_interactive():
        print(
            "\nInteractive setup required: `harness run` was invoked with no\n"
            "--workspace or --prompt, but stdin is not a terminal (or auto-approve\n"
            "is set). Either run from a TTY, or pass --workspace and --prompt\n"
            "explicitly.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    print()
    print("=" * 72)
    print("harness run — interactive setup")
    print("=" * 72)
    print(
        "You ran `harness run` without any flags. The wizard will walk you\n"
        "through the minimum settings needed to start a run. None of your\n"
        "answers will be persisted — every bare `harness run` re-asks.\n"
    )

    # ------------------------------------------------------------------
    # Step 0: API key prerequisite check
    # ------------------------------------------------------------------
    try:
        config = load_raw_config()
    except ConfigError as exc:
        print(f"\n{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    _check_and_prompt_api_keys(config)

    # ------------------------------------------------------------------
    # Steps 1-5: runtime choices, with a summary-confirm loop
    # ------------------------------------------------------------------
    while True:
        workspace = _ask_workspace(channel)
        prompt = _ask_prompt(channel)
        git_mode = _ask_git(channel)
        new_build = _ask_new_build(channel)
        discover = _ask_discover(channel)

        _print_summary(workspace, prompt, git_mode, new_build, discover)
        if channel.confirm("Run with these settings?", default=True):
            break
        print("\nLet's go through the choices again.\n")

    args.workspace = workspace
    args.prompt = prompt
    args.git = git_mode
    args.new_build = new_build
    args.discover = discover
    # If the operator picks --new-build via the wizard, they've already
    # given explicit consent — skip the secondary --yes prompt that
    # cmd_run would otherwise show.
    if new_build:
        args.assume_yes = True
    return args


# ---------------------------------------------------------------------------
# Step 0 — API keys
# ---------------------------------------------------------------------------

def _check_and_prompt_api_keys(config: dict) -> None:
    """Scan ``config["models"]`` for any provider whose ``{PROVIDER}_API_KEY``
    env var is unset, and prompt the user to enter each missing key. Sets
    ``os.environ[env_var]`` so downstream code (gateway dispatch, sandbox
    subprocesses) sees the value. Keys are NOT written to disk."""
    from harness.cli import find_missing_api_keys

    missing = find_missing_api_keys(config)
    if not missing:
        return

    print(
        "\nThe harness needs API keys for the LLM models in your "
        "config.json,\nbut some are not set in the environment. Enter "
        "them now — your\ninput is hidden and not written to disk."
    )
    for env_var in sorted(missing):
        models_using = ", ".join(missing[env_var])
        for attempt in range(2):
            try:
                value = getpass.getpass(
                    f"  {env_var} (for {models_using}): "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\nSetup interrupted.", file=sys.stderr)
                raise SystemExit(2)
            if value:
                os.environ[env_var] = value
                print("    set (in this process only).")
                break
            if attempt == 0:
                print("    Empty — try again.")
        else:
            print(
                f"\nNo value provided for {env_var}. The harness can't run "
                f"without it. Aborting.",
                file=sys.stderr,
            )
            raise SystemExit(2)


# ---------------------------------------------------------------------------
# Steps 1-5 — runtime prompts
# ---------------------------------------------------------------------------

def _ask_workspace(channel) -> str:
    default = os.getcwd()
    while True:
        print("\nStep 1 of 5: Workspace path (the target repo to operate on).")
        print(f"  Default: {default}")
        raw = channel.notes(
            "Enter a path, or press Enter to accept the default"
        ).strip()
        candidate = raw or default
        resolved = os.path.abspath(os.path.expanduser(candidate))
        if not os.path.isdir(resolved):
            print(f"  Not a directory: {resolved}. Try again.")
            continue
        return resolved


def _ask_prompt(channel) -> str:
    while True:
        print("\nStep 2 of 5: Engineering task / prompt.")
        print("  Example: \"Refactor the auth module to use JWT.\"")
        raw = channel.notes("Enter the task description").strip()
        if raw:
            return raw
        print("  The prompt can't be empty. Try again.")


def _ask_git(channel) -> str:
    print("\nStep 3 of 5: Is the workspace a git repository?")
    print("  e = enable  (GitGuardian stashes / branches / rolls back)")
    print("  d = disable (skip every git step — pick this if no git repo)")
    choice = channel.prompt(
        "Choose [e/d]", options=["e", "d"], default="e",
    ).strip().lower()
    return "enable" if choice == "e" else "disable"


def _ask_new_build(channel) -> bool:
    print("\nStep 4 of 5: Treat this as a brand-new build?")
    print(
        "  Deletes every file at the workspace root EXCEPT product_spec/\n"
        "  and .git/. Defaults to no (preserve existing files)."
    )
    return channel.confirm("New build?", default=False)


def _ask_discover(channel) -> bool:
    print("\nStep 5 of 5: Run the full discovery pipeline?")
    print(
        "  Discovery walks through requirements / architecture / deployment\n"
        "  Q&A before code generation. Recommended for greenfield projects;\n"
        "  skip for incremental patching. Defaults to no."
    )
    return channel.confirm("Run discovery?", default=False)


def _print_summary(
    workspace: str, prompt: str, git_mode: str, new_build: bool, discover: bool,
) -> None:
    print()
    print("-" * 72)
    print("Summary")
    print("-" * 72)
    print(f"  Workspace : {workspace}")
    short_prompt = prompt if len(prompt) <= 60 else prompt[:57] + "..."
    print(f"  Prompt    : {short_prompt}")
    print(f"  --git     : {git_mode}")
    print(f"  --new-build: {'true' if new_build else 'false'}")
    print(f"  --discover: {'true' if discover else 'false'}")
    print("-" * 72)

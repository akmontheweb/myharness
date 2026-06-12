"""
Speculative Patch Branching — Multi-Variant Compilation.

This module implements:
    - speculate_node: Replaces single-patch flow with 3 parallel variants.
      Each variant gets an isolated git worktree, is compiled simultaneously,
      and the first passing variant is merged back. Reduces debugging cycles
      and increases first-pass build success rates.

    - Selector strategies: "first_success", "fewest_changes", "all_pass".

Integration:
    - Placed as speculative_node between patching_node and lintgate_node.
    - If enabled, patching_node routes to speculative_node instead of lintgate.
    - Falls back to sequential single-patch flow if all variants fail.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from harness.gateway import NodeRole
from harness.patcher import process_llm_patch_output, PatchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Types
# ---------------------------------------------------------------------------

@dataclass
class VariantResult:
    """Result of a single speculative variant."""
    index: int
    variant_id: str
    worktree_path: str
    llm_response: Optional[Any] = None
    patch_results: list[PatchResult] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    exit_code: int = -1
    raw_output: str = ""
    timed_out: bool = False
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.error

    @property
    def total_lines_changed(self) -> int:
        return sum(r.lines_changed for r in self.patch_results if r.success)


@dataclass
class SpeculativeResult:
    """Aggregate result of speculative branching."""
    total_variants: int = 0
    passed_variants: int = 0
    winner_index: int = -1
    variant_results: list[VariantResult] = field(default_factory=list)
    strategy: str = "first_success"
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# 2. Speculative Node
# ---------------------------------------------------------------------------

async def speculate_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    Speculative execution node: generates N variants, compiles them in parallel,
    and selects the best passing variant.

    Workflow:
        1. Call the LLM N times with temperature > 0 for diverse solutions
        2. Create isolated git worktrees for each variant
        3. Apply patches to each worktree
        4. Run lintgate + compiler on each worktree in parallel
        5. Select the first passing variant (or best by strategy)
        6. Copy winning files back to main workspace
        7. Clean up temporary worktrees

    Configuration via the `speculative` section of config/config.json
    (threaded onto state via cli.py → run_graph(speculative_config=...)).
    Strict validation lives in cli.py:validate_config_strict; ranges:
    num_variants ∈ [1, 10], temperature ∈ [0.0, 1.5], selection_strategy
    ∈ {first_success, fewest_changes, all_pass}.
        {
          "speculative": {
            "enabled": true,
            "num_variants": 3,
            "temperature": 0.3,
            "selection_strategy": "first_success",
            "worktree_base_dir": "/tmp/.harness/speculative"
          }
        }

    Returns:
        State update dict with winning variant data.
    """
    import time as time_module

    # --- Config ---
    spec_cfg = state.get("speculative_config", {}) or {}
    # Honour the `enabled` flag. Default is False: across the recent log set
    # (sessions ae01ec25, c498b865, dd47480a, 8b7f7d52) the winner case
    # never fired — every speculative round fell through to salvage, the
    # salvage merge created workspace coherence problems (incoherent
    # directory layouts, missing entry points), and the downstream repair
    # loop couldn't recover. The 3× LLM cost bought negative ROI. Operators
    # who want speculative on a workload where it earns its cost (e.g.
    # steady-state repos with bounded micro-fixes) flip the flag in
    # config.json. See speculative.md (when documented) for details.
    if not spec_cfg.get("enabled", False):
        logger.info(
            "[speculative] Disabled (speculative.enabled is false). "
            "Passing through to standard patching flow."
        )
        return _fallback_result()
    num_variants = spec_cfg.get("num_variants", 3)
    temperature = spec_cfg.get("temperature", 0.3)
    strategy = spec_cfg.get("selection_strategy", "first_success")
    worktree_base = spec_cfg.get("worktree_base_dir", "/tmp/.harness/speculative")

    workspace_path = state.get("workspace_path", os.getcwd())
    build_command = state.get("build_command", "make build")
    sandbox_config = dict(state.get("sandbox_config", {}) or {})
    allow_network = state.get("allow_network", False)
    messages = state.get("messages", [])
    budget = state.get("budget_remaining_usd", 2.00)

    # Late-bind the build command + toolchain image the same way
    # ``compiler_node`` does. Without this, speculative variants run with
    # the historical ``make build`` default in ``ubuntu:22.04`` against
    # workspaces the LLM just populated (e.g. Python sources with no
    # Makefile), every variant exits 127, and the whole speculative round
    # is guaranteed budget waste. Keeping this inline rather than
    # importing ``compiler_node``'s block verbatim because we don't need
    # the loop-counter / token-tracker plumbing — just the resolved
    # build_command + sandbox_config + allow_network.
    adapted_build_cmd: Optional[str] = None
    if build_command.strip() == "make build" and not any(
        os.path.exists(os.path.join(workspace_path, name))
        for name in ("Makefile", "makefile", "GNUmakefile")
    ):
        try:
            from harness.cli import _detect_default_build_command
            late = _detect_default_build_command(workspace_path)
            if late and late != "make build":
                logger.info(
                    "[speculative] Workspace has no Makefile; adapting build command "
                    "from default 'make build' to detected: %s", late,
                )
                adapted_build_cmd = late
                build_command = late
        except Exception as exc:  # noqa: BLE001
            logger.debug("[speculative] build-command late-bind failed: %s", exc)
    try:
        from harness.graph import _apply_toolchain_adaptation
        (
            sandbox_config,
            allow_network,
            image_was_adapted,
            network_was_adapted,
            _ro_was_adapted,
        ) = _apply_toolchain_adaptation(
            build_command,
            sandbox_config,
            allow_network,
            command_is_adapter_synthesised=adapted_build_cmd is not None,
        )
        if image_was_adapted:
            logger.info(
                "[speculative] Adapting sandbox docker_image to %r to match toolchain implied by: %s",
                sandbox_config.get("docker_image"), build_command,
            )
        if network_was_adapted:
            logger.info(
                "[speculative] Auto-enabling network for adapter-synthesised install step: %s",
                build_command,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[speculative] toolchain adaptation failed: %s", exc)

    start_time = time_module.monotonic()

    logger.info("[speculative] Starting speculative branching: %d variants, temp=%.2f, strategy=%s",
                 num_variants, temperature, strategy)

    # --- Get gateway ---
    from harness.graph import get_gateway
    gateway = get_gateway()
    if gateway is None:
        logger.error("[speculative] No gateway configured. Falling back to single patch.")
        return _fallback_result()

    # --- Step 1: Generate N variants in parallel ---
    variant_responses: list[Any] = []
    try:
        tasks = [
            gateway.dispatch(
                messages=list(messages),
                role=NodeRole.PATCHING,
                budget_remaining_usd=budget,
                temperature=temperature,
            )
            for _ in range(num_variants)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.warning("[speculative] Variant %d LLM call failed: %s", i, result)
                variant_responses.append(None)
            else:
                response, new_budget = result  # (LLMResponse, new_budget)
                variant_responses.append(response)
                logger.info("[speculative] Variant %d: %d tokens (in=%d out=%d)",
                             i, response.usage.input_tokens + response.usage.output_tokens,
                             response.usage.input_tokens, response.usage.output_tokens)
    except Exception as exc:
        logger.exception("[speculative] Variant generation failed: %s", exc)
        return _fallback_result()

    # Count successful LLM calls
    valid_variants = [r for r in variant_responses if r is not None]
    if not valid_variants:
        logger.error("[speculative] All variant LLM calls failed.")
        return _fallback_result()

    # Speculative needs HEAD to exist (worktree add uses HEAD as the source ref).
    # On a freshly `git init`'d repo with zero commits, skip cleanly instead
    # of letting `git worktree add HEAD` fail N times with a cryptic error.
    if not _repo_has_resolvable_head(workspace_path):
        logger.warning(
            "[speculative] Skipping speculative branching: workspace %s has no commits yet "
            "(unborn HEAD). Make an initial commit to enable speculative repair. "
            "Falling back to sequential repair.",
            workspace_path,
        )
        return _fallback_result()

    # --- Step 2: Create isolated worktrees and apply patches ---
    variant_results: list[VariantResult] = []

    for i, response in enumerate(variant_responses):
        if response is None:
            variant_results.append(VariantResult(index=i, variant_id="failed", worktree_path="", error="LLM call failed"))
            continue

        variant_id = str(uuid.uuid4())[:8]
        worktree_path = os.path.join(worktree_base, f"variant-{i}-{variant_id}")

        vr = VariantResult(index=i, variant_id=variant_id, worktree_path=worktree_path)
        vr.llm_response = response

        # Create git worktree
        if not _create_worktree(workspace_path, worktree_path):
            vr.error = "Failed to create git worktree"
            variant_results.append(vr)
            continue

        # Apply patches to the worktree
        try:
            patch_results, modified_files = await process_llm_patch_output(
                response.content,
                worktree_path,
                existing_modified_files=[],
            )
            vr.patch_results = patch_results
            vr.modified_files = modified_files

            success_count = sum(1 for r in patch_results if r.success)
            if success_count == 0:
                vr.error = f"No patches applied ({len(patch_results)} attempted)"
                variant_results.append(vr)
                continue

            logger.info("[speculative] Variant %d: %d/%d patches applied to %s",
                         i, success_count, len(patch_results), worktree_path)

        except Exception as exc:
            vr.error = f"Patch application failed: {exc}"
            variant_results.append(vr)
            continue

        variant_results.append(vr)

    # --- Step 3: Run lintgate on all variants ---
    try:
        from harness.lintgate import lintgate_node
        for vr in variant_results:
            if vr.error or not vr.worktree_path:
                continue
            lint_state = {
                "modified_files": vr.modified_files,
                "workspace_path": vr.worktree_path,
                "messages": [],
            }
            await lintgate_node(lint_state)
    except ImportError:
        pass  # lintgate not required

    # --- Step 4: Compile all variants in parallel ---
    from harness.sandbox import SandboxExecutor

    async def _compile_variant(vr: VariantResult) -> VariantResult:
        if vr.error or not vr.worktree_path:
            return vr
        try:
            # Per-variant late-bind. The workspace-time resolution above
            # sniffed an empty/greenfield workspace before any LLM call.
            # Now that this variant's patches are on disk in its worktree,
            # re-sniff against THAT tree so the toolchain reflects what
            # the variant actually produced. Without this, a greenfield
            # run keeps build_command="make build" against the bare
            # ubuntu:22.04 default and every variant exits 127 even when
            # one wrote a perfectly good Python (or Node, or Rust) project.
            # detect_default_build_command + _apply_toolchain_adaptation
            # are idempotent — re-calling them produces the same answer
            # the workspace-time pass picked when the worktree matches.
            per_variant_build = build_command
            per_variant_sandbox = sandbox_config
            per_variant_network = allow_network
            per_variant_adapted: Optional[str] = None
            try:
                from harness.cli import _detect_default_build_command
                late = _detect_default_build_command(vr.worktree_path)
                if late and late != per_variant_build:
                    logger.info(
                        "[speculative] Variant %d: build command resolved to %r "
                        "(workspace-time default was %r).",
                        vr.index, late, per_variant_build,
                    )
                    per_variant_adapted = late
                    per_variant_build = late
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[speculative] Variant %d: per-variant build-command late-bind failed: %s",
                    vr.index, exc,
                )
            try:
                from harness.graph import _apply_toolchain_adaptation
                (
                    per_variant_sandbox,
                    per_variant_network,
                    v_image_adapted,
                    v_net_adapted,
                    _,
                ) = _apply_toolchain_adaptation(
                    per_variant_build,
                    per_variant_sandbox,
                    per_variant_network,
                    command_is_adapter_synthesised=per_variant_adapted is not None,
                )
                if v_image_adapted:
                    logger.info(
                        "[speculative] Variant %d: adapting docker_image to %r to match toolchain implied by: %s",
                        vr.index, per_variant_sandbox.get("docker_image"), per_variant_build,
                    )
                if v_net_adapted:
                    logger.info(
                        "[speculative] Variant %d: auto-enabling network for adapter-synthesised install step: %s",
                        vr.index, per_variant_build,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[speculative] Variant %d: per-variant toolchain adaptation failed: %s",
                    vr.index, exc,
                )

            # Give each variant a private writable cache directory tree.
            # Multiple variants running in parallel would otherwise corrupt
            # each other's pip / npm / cargo / go / mypy / pytest caches —
            # those tools assume single-writer access to their cache dirs.
            #
            # Read-only host cache mounts (~/.cache/pip etc. via the unshare
            # backend's --bind -o ro) still serve as warm sources; the env
            # vars below redirect *writes* to per-variant locations.
            # When sandbox.cache_volumes is on, route package caches through
            # the shared named volume (warm-up has already populated it);
            # keep build-output caches variant-private.
            use_shared = bool((per_variant_sandbox or {}).get("cache_volumes"))
            variant_env = _build_variant_cache_env(
                vr.worktree_path, use_shared_package_cache=use_shared,
            )
            executor = SandboxExecutor(
                workspace_path=vr.worktree_path,
                extra_env=variant_env,
                sandbox_config=per_variant_sandbox,
                allow_network=per_variant_network,
                session_id=state.get("session_id"),
            )
            result = await executor.run(per_variant_build)
            vr.exit_code = result.exit_code
            vr.raw_output = result.raw_output
            vr.timed_out = result.timed_out
            logger.info("[speculative] Variant %d compiled: exit=%d timed_out=%s",
                         vr.index, vr.exit_code, vr.timed_out)
            # Exit 127 across variants is almost always a missing-toolchain
            # signal (the shell could not find the build binary). Append a
            # one-line hint to the raw_output so the operator — and the
            # downstream repair loop — see WHY rather than staring at a
            # bare "exit=127".
            if vr.exit_code == 127:
                hint = (
                    f"\n[speculative-hint] exit 127 typically means the shell could not "
                    f"find a binary in the build command. Resolved build_command="
                    f"{per_variant_build!r}, docker_image="
                    f"{per_variant_sandbox.get('docker_image', 'ubuntu:22.04')!r}. "
                    f"Either the toolchain isn't installed in that image or the worktree "
                    f"has no source markers the harness can detect."
                )
                vr.raw_output = (vr.raw_output or "") + hint
        except Exception as exc:
            vr.error = f"Compile failed: {exc}"
            logger.warning("[speculative] Variant %d compile error: %s", vr.index, exc)
        return vr

    # --- Cache warm-up pass ---
    # When sandbox.cache_volumes is on, the variants share a writable named
    # volume per tool (pip/npm/cargo). Without warm-up, all three variants
    # race to cold-fill the cache in parallel — 3× the network downloads,
    # cargo flock contention on the registry index. Warm-up runs the install
    # step once against the workspace (single-writer) so the variants then
    # fan out against an already-populated cache.
    #
    # Skip warm-up when:
    #   - cache_volumes is off (no shared cache to fill — variants have
    #     their own per-variant write dirs and host caches are read-only).
    #   - The build command does no install work (`make build`, plain
    #     `cargo build` against a vendored crate, etc.).
    #   - The workspace has no install markers (greenfield run — the
    #     workspace-time late-bind returned None, so build_command is still
    #     the unresolved default and warming up with it would just exit 127).
    cache_volumes_on = bool((sandbox_config or {}).get("cache_volumes"))
    if cache_volumes_on:
        try:
            from harness.graph import _build_command_needs_network
            from harness.cli import _detect_default_build_command as _detect_workspace_marker
            from harness.sandbox import SandboxExecutor
            session_id = state.get("session_id")
            needs_install = _build_command_needs_network(build_command)
            workspace_has_markers = _detect_workspace_marker(workspace_path) is not None
            if needs_install and workspace_has_markers:
                logger.info(
                    "[speculative] Warm-up: priming shared cache volume(s) with "
                    "a single install pass before %d variants fan out.",
                    len(variant_results),
                )
                warmup_exec = SandboxExecutor(
                    workspace_path=workspace_path,
                    sandbox_config=sandbox_config,
                    allow_network=allow_network,
                    session_id=session_id,
                )
                warmup_result = await warmup_exec.run(build_command)
                logger.info(
                    "[speculative] Warm-up complete: exit=%d elapsed=%.2fs.",
                    warmup_result.exit_code, warmup_result.elapsed_seconds,
                )
            else:
                logger.debug(
                    "[speculative] Warm-up skipped: needs_install=%s "
                    "workspace_has_markers=%s.", needs_install, workspace_has_markers,
                )
        except Exception as exc:  # noqa: BLE001 — warm-up is best-effort
            logger.debug("[speculative] Warm-up failed: %s", exc)

    variant_results = list(await asyncio.gather(*[
        _compile_variant(vr) for vr in variant_results
    ]))

    # --- Step 5: Select the winning variant ---
    winner = _select_winner(variant_results, strategy)
    elapsed = time_module.monotonic() - start_time

    spec_result = SpeculativeResult(
        total_variants=len(variant_results),
        passed_variants=sum(1 for vr in variant_results if vr.passed),
        winner_index=winner.index if winner else -1,
        variant_results=variant_results,
        strategy=strategy,
        elapsed_seconds=elapsed,
    )

    # --- Step 6: Merge winning variant back ---
    if winner and winner.passed and winner.worktree_path:
        logger.info("[speculative] Selected Variant %d (exit=%d, files=%d). Merging back.",
                     winner.index, winner.exit_code, len(winner.modified_files))

        # Copy winning-variant files back to main workspace.
        # Use temp files + atomic rename so a crash mid-copy doesn't leave
        # the workspace in a half-merged state.
        import tempfile as _tempfile
        from harness.trust import safe_resolve as _safe_resolve
        merge_errors: list[str] = []
        for filepath in winner.modified_files:
            # Defense: the patcher already validates paths but the winner
            # comes from a worktree — re-validate against workspace_path.
            try:
                _safe_resolve(workspace_path, filepath)
            except ValueError:
                logger.warning("[speculative] Skipping out-of-workspace path: %s", filepath)
                continue

            src = os.path.join(winner.worktree_path, filepath)
            dst = os.path.join(workspace_path, filepath)
            if not os.path.isfile(src):
                continue
            dst_dir = os.path.dirname(dst)
            try:
                os.makedirs(dst_dir, exist_ok=True)
                fd, tmp = _tempfile.mkstemp(dir=dst_dir)
                try:
                    os.close(fd)
                    shutil.copy2(src, tmp)
                    os.replace(tmp, dst)
                except Exception:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            except OSError as copy_err:
                logger.error("[speculative] Failed to merge %s: %s", filepath, copy_err)
                merge_errors.append(filepath)

        if merge_errors:
            logger.warning("[speculative] %d file(s) could not be merged: %s",
                           len(merge_errors), merge_errors)

        # --- Step 7: Cleanup worktrees ---
        _cleanup_worktrees(workspace_path, worktree_base, variant_results)

        # Build status message
        status_parts = [
            f"[Speculative] {spec_result.passed_variants}/{spec_result.total_variants} variants passed.",
            f"  Selected Variant {winner.index} (strategy: {strategy}).",
            f"  Winner: {len(winner.patch_results)} patches, {len(winner.modified_files)} files, exit {winner.exit_code}.",
        ]
        for vr in variant_results:
            if vr is not winner:
                status = "PASS" if vr.passed else f"FAIL (exit={vr.exit_code})"
                status_parts.append(f"  Variant {vr.index}: {status}")

        messages_out = list(state.get("messages", []))
        messages_out.append({"role": "system", "content": "\n".join(status_parts)})

        # Update token tracker with the winner's LLM usage. Per-stage
        # attribution: speculative variants are NodeRole.PATCHING dispatches.
        token_tracker = state.get("token_tracker", {})
        if winner.llm_response is not None:
            token_tracker = gateway.aggregate_tokens(
                token_tracker, winner.llm_response.usage, role=NodeRole.PATCHING,
            )

        logger.info("[speculative] Complete: %.2fs, winner=Variant %d.", elapsed, winner.index)

        return {
            "modified_files": winner.modified_files,
            "messages": messages_out,
            "token_tracker": token_tracker,
            "node_state": {
                "speculative": {
                    "winner_index": winner.index,
                    "total_variants": spec_result.total_variants,
                    "passed_variants": spec_result.passed_variants,
                },
            },
        }

    # --- Fallback: all variants failed ---
    # Before throwing away every variant's work, try to salvage the best
    # failing one and merge its patches back to the real workspace. Variants
    # often fail their compile not because their generated code is wrong
    # but because the sandbox is missing a pip dep or pytest had no tests
    # to collect — both of which the repair loop can resolve once the code
    # actually lives on disk. Without salvage, the repair loop starts from
    # an empty workspace and spins out on "no source to fix".
    salvage = _pick_salvage_variant(variant_results)
    if salvage is not None:
        logger.warning(
            "[speculative] All %d variants failed, but Variant %d applied "
            "%d patch(es) with a recoverable failure (exit=%d). Salvaging "
            "its patches to the workspace so the repair loop has real code "
            "to work with.",
            len(variant_results), salvage.index,
            sum(1 for r in salvage.patch_results if r.success),
            salvage.exit_code,
        )
        merge_errors = _merge_variant_into_workspace(salvage, workspace_path)
        _cleanup_worktrees(workspace_path, worktree_base, variant_results)

        # Merge salvaged files into the accumulated modified_files list rather
        # than replacing it — the downstream nodes (lintgate, test_generation,
        # repair) all read modified_files as the source of truth for "what
        # changed this session." If we replaced instead of merged we'd drop
        # any files an earlier patching pass produced.
        prior_modified: list[str] = list(state.get("modified_files", []) or [])
        merged_modified = list(prior_modified)
        for f in salvage.modified_files:
            if f not in merged_modified:
                merged_modified.append(f)

        logger.info(
            "[speculative:salvage] Merged Variant %d → workspace: %d new file(s) "
            "(prior modified=%d, merge_errors=%d). modified_files now=%d.",
            salvage.index, len(salvage.modified_files),
            len(prior_modified), len(merge_errors), len(merged_modified),
        )

        messages_out = list(state.get("messages", []))
        status_parts = [
            f"[Speculative] All {len(variant_results)} variants failed.",
            (
                f"  Salvaged Variant {salvage.index}: "
                f"{len(salvage.modified_files)} file(s) merged back. "
                f"Build failure (exit={salvage.exit_code}) appears recoverable; "
                f"routing to repair_node for follow-up fix."
            ),
        ]
        for vr in variant_results:
            if vr is not salvage:
                status_parts.append(f"  Variant {vr.index}: {vr.error or f'exit={vr.exit_code}'}")
        if merge_errors:
            status_parts.append(
                f"  Note: {len(merge_errors)} file(s) could not be merged back: {merge_errors}"
            )
        messages_out.append({"role": "system", "content": "\n".join(status_parts)})

        token_tracker = state.get("token_tracker", {})
        if salvage.llm_response is not None:
            token_tracker = gateway.aggregate_tokens(
                token_tracker, salvage.llm_response.usage, role=NodeRole.PATCHING,
            )

        return {
            "modified_files": merged_modified,
            "messages": messages_out,
            "token_tracker": token_tracker,
            "node_state": {
                "speculative": {
                    "all_failed": True,
                    "salvaged_index": salvage.index,
                    "salvaged_files": len(salvage.modified_files),
                    "total_variants": spec_result.total_variants,
                },
            },
        }

    _cleanup_worktrees(workspace_path, worktree_base, variant_results)

    logger.warning("[speculative] All %d variants failed. Falling back to sequential repair.",
                   len(variant_results))

    messages_out = list(state.get("messages", []))
    status_parts = [f"[Speculative] All {len(variant_results)} variants failed. Falling back to standard repair."]
    for vr in variant_results:
        status_parts.append(f"  Variant {vr.index}: {vr.error or f'exit={vr.exit_code}'}")
    messages_out.append({"role": "system", "content": "\n".join(status_parts)})

    return {
        "messages": messages_out,
        "node_state": {
            "speculative": {
                "all_failed": True,
                "total_variants": spec_result.total_variants,
            },
        },
    }


# ---------------------------------------------------------------------------
# 2b. Salvage helpers (rescue the best failing variant on full-fleet failure)
# ---------------------------------------------------------------------------

# Exit codes / output patterns that signal a recoverable build failure —
# the variant's patches are likely fine, but the sandbox couldn't run them
# end to end. The repair loop on the real workspace can resolve these.
_RECOVERABLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # pip-installable test runner missing
    re.compile(r"ModuleNotFoundError: No module named ['\"](?:pytest|pytest_\w+|ruff|mypy|black|isort|coverage)['\"]"),
    re.compile(r"^/[^:\s]+/python3?: No module named (pytest|pytest_\w+|ruff|mypy|black|isort)\s*$", re.MULTILINE),
    # pytest exit-5: no tests collected — handled downstream by test_generation
    re.compile(r"(?m)^=*\s*no tests ran in [\d.]+s\s*=*$"),
    re.compile(r"(?m)^no tests ran in [\d.]+s\s*$"),
    # Missing application dep (e.g. fastapi, uvicorn, sqlalchemy)
    re.compile(r"ModuleNotFoundError: No module named ['\"][^'\"]+['\"]"),
)

_SALVAGE_PYTEST_EXIT_CODES: frozenset[int] = frozenset({1, 2, 4, 5})


def _is_recoverable_failure(vr: "VariantResult") -> bool:
    """True when the variant's compile failure looks like something the
    sequential repair loop can fix once the patches live on the real
    workspace (missing deps, no tests collected, generic test failures).

    Excludes timeouts, sandbox errors, and exit codes that suggest the
    container itself is misconfigured (which the repair loop can't help with).
    """
    if vr.timed_out:
        return False
    if vr.error and not vr.patch_results:
        return False
    # Hard NO when no patches actually landed on the worktree — there's
    # nothing to merge back.
    if not any(r.success for r in vr.patch_results):
        return False
    # Permissive: any non-zero pytest-shaped exit code can be salvaged if
    # the tail of the output contains a recoverable signature.
    if vr.exit_code in _SALVAGE_PYTEST_EXIT_CODES:
        tail = (vr.raw_output or "")[-4000:]
        if any(p.search(tail) for p in _RECOVERABLE_PATTERNS):
            return True
        # Even without a signature, exit codes 1-5 from a test runner are
        # fixable by the repair LLM in most cases (assertion failures,
        # import errors in the user's own code).
        return True
    return False


def _pick_salvage_variant(variant_results: list["VariantResult"]) -> Optional["VariantResult"]:
    """Among the failed variants, pick the most-promising one to merge back.

    Ranking: most successful patches first, then fewest lines changed
    (Occam-ish — smaller diffs are less likely to drag in hallucinated code).
    Returns None when no variant qualifies for salvage.

    Quality gate (C1): even the best candidate is refused if it doesn't
    meet a minimum coherence bar. A variant where only a tiny fraction of
    its patches landed produces a half-built workspace that the repair
    loop then tries — and fails — to fix. Better to refuse salvage and let
    repair work from the pre-speculative workspace, which is internally
    coherent even if it's missing features.

    Bar (conservative, all must hold):
      - applied_patches >= 3 (variants with 1-2 successful patches are too
        thin to be worth merging — they add scaffolding without substance).
      - applied_patches / total_patches >= 0.50 (at least half of what the
        variant tried actually landed — fewer than that suggests fundamental
        confusion about workspace state).
    """
    candidates = [vr for vr in variant_results if _is_recoverable_failure(vr)]
    if not candidates:
        return None
    candidates.sort(
        key=lambda vr: (
            -sum(1 for r in vr.patch_results if r.success),
            vr.total_lines_changed,
        )
    )
    best = candidates[0]
    applied = sum(1 for r in best.patch_results if r.success)
    total = len(best.patch_results) or 1
    pct = applied / total
    MIN_APPLIED = 3
    MIN_PCT = 0.50
    if applied < MIN_APPLIED or pct < MIN_PCT:
        logger.warning(
            "[speculative:salvage] Best candidate Variant %d does not meet "
            "the quality gate (applied=%d/%d=%.0f%%, need >=%d patches and "
            ">=%.0f%%). Refusing salvage; repair will start from the "
            "pre-speculative workspace.",
            best.index, applied, total, pct * 100, MIN_APPLIED, MIN_PCT * 100,
        )
        return None
    return best


def _merge_variant_into_workspace(
    vr: "VariantResult", workspace_path: str,
) -> list[str]:
    """Copy a variant's successful patch files back into the workspace.

    Mirrors the merge step used for the winner path, but operates on a
    failing-but-salvageable variant. Returns the list of files that could
    not be merged (empty on full success).
    """
    import tempfile as _tempfile
    from harness.trust import safe_resolve as _safe_resolve

    merge_errors: list[str] = []
    for filepath in vr.modified_files:
        try:
            _safe_resolve(workspace_path, filepath)
        except ValueError:
            logger.warning(
                "[speculative:salvage] Skipping out-of-workspace path: %s", filepath
            )
            continue

        src = os.path.join(vr.worktree_path, filepath)
        dst = os.path.join(workspace_path, filepath)
        if not os.path.isfile(src):
            continue
        dst_dir = os.path.dirname(dst)
        try:
            os.makedirs(dst_dir, exist_ok=True)
            fd, tmp = _tempfile.mkstemp(dir=dst_dir)
            try:
                os.close(fd)
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as copy_err:
            logger.error(
                "[speculative:salvage] Failed to merge %s: %s", filepath, copy_err
            )
            merge_errors.append(filepath)
    return merge_errors


# ---------------------------------------------------------------------------
# 3. Selection Strategies
# ---------------------------------------------------------------------------

def _select_winner(
    variant_results: list[VariantResult],
    strategy: str = "first_success",
) -> Optional[VariantResult]:
    """
    Select the winning variant based on the configured strategy.

    Strategies:
        - "first_success": First variant with exit_code 0
        - "fewest_changes": Passing variant with fewest lines changed
        - "all_pass": Only return winner if ALL variants pass (strictest)
    """
    passing = [vr for vr in variant_results if vr.passed]

    if not passing:
        return None

    if strategy == "all_pass":
        if len(passing) == len(variant_results):
            return passing[0]
        logger.warning("[speculative] all_pass strategy: %d/%d passed. No winner selected.",
                        len(passing), len(variant_results))
        return None

    if strategy == "fewest_changes":
        return min(passing, key=lambda vr: vr.total_lines_changed)

    # Default: first_success
    return passing[0]


# ---------------------------------------------------------------------------
# 4. Worktree Management
# ---------------------------------------------------------------------------

def _build_variant_cache_env(
    worktree_path: str,
    *,
    use_shared_package_cache: bool = False,
) -> dict[str, str]:
    """
    Build environment variables that redirect every common build tool's
    *writable* cache to a variant-local directory tree.

    Without this, parallel variants run concurrent `pip install`,
    `npm install`, `cargo build`, `go build`, `pytest`, `mypy`, etc.
    against the same shared per-user cache directories — pip's lock file
    races, cargo's registry index gets corrupted, mypy's incremental
    cache gets mixed across branches, and pytest's `.pytest_cache`
    becomes meaningless.

    Each variant gets ``<worktree>/.harness-cache/<tool>/`` so writes are
    isolated. The host-level read-only cache mounts (configured via
    ``sandbox.readonly_cache_mounts``) still seed warm dependencies —
    these env vars only affect where writes land.

    When ``use_shared_package_cache`` is True (``sandbox.cache_volumes`` is
    on), the **package** caches (pip, npm, yarn, cargo registry, go mods,
    maven repo) are NOT overridden — they fall through to the tool's
    default paths inside the container, which the docker backend has
    bind-mounted to a writable named volume. Build-output / incremental
    tool caches (__pycache__, mypy, pytest, ruff, gradle, cargo target,
    go build cache) stay per-variant since different variants produce
    different code and must not share build artifacts.

    Returned env-var keys (each pointing to a per-variant subdirectory):
      - PIP_CACHE_DIR          (Python pip; omitted when shared cache is on)
      - npm_config_cache       (npm — lowercase is canonical; omitted when shared)
      - YARN_CACHE_FOLDER      (Yarn; omitted when shared)
      - CARGO_HOME             (Cargo registry + git + credentials; omitted when shared)
      - CARGO_TARGET_DIR       (Rust build artifacts — always per-variant)
      - GOCACHE                (Go build cache — always per-variant)
      - GOMODCACHE             (Go module download cache; omitted when shared)
      - GRADLE_USER_HOME       (Gradle — always per-variant)
      - MAVEN_OPTS             (-Dmaven.repo.local override; omitted when shared)
      - PYTHONPYCACHEPREFIX    (Python __pycache__ — always per-variant)
      - MYPY_CACHE_DIR         (mypy incremental — always per-variant)
      - RUFF_CACHE_DIR         (ruff — always per-variant)
      - PYTEST_ADDOPTS         (forces -o cache_dir=... — always per-variant)
      - XDG_CACHE_HOME         (generic XDG fallback — always per-variant)
    """
    base = os.path.join(worktree_path, ".harness-cache")
    os.makedirs(base, exist_ok=True)

    def _sub(name: str) -> str:
        p = os.path.join(base, name)
        os.makedirs(p, exist_ok=True)
        return p

    env: dict[str, str] = {
        # Build outputs + tool incremental state — ALWAYS per-variant,
        # regardless of cache_volumes. Different variants produce different
        # code; sharing __pycache__, mypy incremental, ruff, pytest, cargo
        # target, go build cache, or gradle home would mix branches and
        # corrupt verdicts.
        "PYTHONPYCACHEPREFIX": _sub("pycache"),
        "MYPY_CACHE_DIR": _sub("mypy"),
        "RUFF_CACHE_DIR": _sub("ruff"),
        "PYTEST_ADDOPTS": f"-o cache_dir={_sub('pytest')}",
        "CARGO_TARGET_DIR": _sub("cargo-target"),
        "GOCACHE": _sub("go-build"),
        "GRADLE_USER_HOME": _sub("gradle"),
        "XDG_CACHE_HOME": _sub("xdg"),
    }
    if not use_shared_package_cache:
        # Default: per-variant package caches so concurrent writes don't
        # race (cargo's registry index in particular). With cache_volumes
        # on, the docker backend bind-mounts a writable named volume at
        # the container's default tool paths, and the speculative warm-up
        # pass primes the volume before fan-out — leaving these env vars
        # unset lets the tools pick up the shared cache.
        maven_repo = _sub("maven-repo")
        env.update({
            "PIP_CACHE_DIR": _sub("pip"),
            "npm_config_cache": _sub("npm"),
            "YARN_CACHE_FOLDER": _sub("yarn"),
            "CARGO_HOME": _sub("cargo-home"),
            "GOMODCACHE": _sub("go-mod"),
            "MAVEN_OPTS": f"-Dmaven.repo.local={maven_repo}",
        })
    return env


def _repo_has_resolvable_head(repo_path: str) -> bool:
    """True iff the repo at repo_path has at least one commit (HEAD resolves).

    Speculative branching depends on `git worktree add ... HEAD`, which fails
    on an empty `git init`'d repo with `fatal: invalid reference: HEAD`.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--verify", "--quiet", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return result.returncode == 0


def _create_worktree(repo_path: str, worktree_path: str) -> bool:
    """Create a git worktree at the given path."""
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

    try:
        # Remove if exists from a previous run
        if os.path.exists(worktree_path):
            _remove_worktree(repo_path, worktree_path)

        result = subprocess.run(
            ["git", "-C", repo_path, "worktree", "add", "--detach", worktree_path, "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("[speculative] Failed to create worktree at %s: %s",
                           worktree_path, result.stderr.strip())
            return False

        logger.debug("[speculative] Created worktree at %s", worktree_path)
        return True
    except Exception as exc:
        logger.warning("[speculative] Worktree creation error: %s", exc)
        return False


def _remove_worktree(repo_path: str, worktree_path: str) -> None:
    """Remove a git worktree."""
    try:
        subprocess.run(
            ["git", "-C", repo_path, "worktree", "remove", "--force", worktree_path],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        pass

    # Fallback: manual cleanup
    if os.path.isdir(worktree_path):
        try:
            shutil.rmtree(worktree_path, ignore_errors=True)
        except Exception:
            pass


def _cleanup_worktrees(
    repo_path: str,
    worktree_base: str,
    variant_results: list[VariantResult],
) -> None:
    """Remove all temporary worktrees.

    Do NOT clear ``vr.modified_files`` / ``vr.patch_results`` here. Both the
    winner-merge path and the salvage path read those fields after cleanup
    (to populate the LangGraph state return) — clearing them dropped the
    list of merged files on the floor, so downstream nodes saw
    ``modified_files=[]`` even though files HAD been copied to the
    workspace. Only ``worktree_path`` is reset so callers don't try to
    touch a directory that's no longer there.
    """
    for vr in variant_results:
        if vr.worktree_path and os.path.isdir(vr.worktree_path):
            _remove_worktree(repo_path, vr.worktree_path)
            logger.debug("[speculative] Removed worktree %s", vr.worktree_path)
        vr.worktree_path = ""


# ---------------------------------------------------------------------------
# 5. Fallback
# ---------------------------------------------------------------------------

def _fallback_result() -> dict[str, Any]:
    """Return a state update that passes through to normal patching."""
    logger.info("[speculative] Passing through to standard patching flow.")
    return {
        "node_state": {
            "speculative": {
                "fallback": True,
                "reason": "speculative execution unavailable",
            },
        },
    }
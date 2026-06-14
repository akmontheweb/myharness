# Existing-project flow

For operators running `harness run` against a repository that already has code — fixing a bug, adding a feature, or refactoring — rather than scaffolding from scratch. For the greenfield (requirements → architecture → deployment) path, see [`SPEC_ARCHITECTURE.md`](SPEC_ARCHITECTURE.md).

## Summary

The greenfield path runs requirements → architecture → deployment discovery → spec → human gatekeeper → patching. The existing-project path **skips all of that** via a single boolean (`skip_discovery=True`) and lands straight in `patching_node`. From there, both flows share the same build / test / repair loop. Bug fixes and feature additions go through the **same graph** — the only thing that distinguishes them is the prompt the operator writes.

## 1. Wizard decides the mode

On bare `harness run`, the wizard (`harness/wizard.py`, wired from `harness/cli.py:38-135`) asks:

- **New session or resume?** Resume picks up a checkpointed run from SQLite.
- For a new session on existing code, in order: workspace path, the **engineering task** (the bug or feature description), `--git enable|disable`, `--new-build true|false` (defaults `false` for existing code so the harness does not clobber your files), `--discover true|false` (defaults `false` for existing code).

Choosing "no discovery" flips `skip_discovery=True` in `AgentState` (`harness/graph.py:105`, set at `:198` and `:226`).

## 2. Graph routing

`harness/graph.py:5954-5967`:

```python
def route_after_start(state):
    if state.get("skip_discovery", False):
        return "patching_node"                  # existing-project path
    return "requirements_discovery_node"        # greenfield path
```

The existing-project route **bypasses** every node in the discovery pipeline: `requirements_discovery_node` (`:4378`), `discovery_interview_loop`, `architecture_discovery_node`, `write_spec_node`, `spec_review_node`, `human_gatekeeper_node`, `generate_deployment_spec_node`. There is no separate `planning_node` for existing projects — the operator's prompt is the plan.

## 3. Workspace fingerprinting

Before patching, the harness scans the workspace once (`harness/impact.py`):

- `_detect_source_root` (`:911`) — finds the dominant top-level source directory (`app/`, `src/`, `lib/`, etc.) and uses it to constrain the patcher allowlist (`harness/graph.py:251`).
- `_detect_workspace_stack` (`:635`) — sniffs `package.json` / `pyproject.toml` / `go.mod` / etc. and emits stack tags (`python`, `fastapi`, `typescript`, `react`, …). The system prompt then loads only the relevant skills from `harness/skills/` (`harness/graph.py:682-732`).
- `_is_greenfield_workspace` (`:1044`) — returns `False` for existing projects, which keeps the source-root allowlist active so the LLM cannot drop new modules at workspace root.

No code is injected into LLM context up front. The LLM reads what it needs via `READ_FILE` blocks (or is shown a small window by the patcher's closest-match resolver).

## 4. Patching loop

`harness/graph.py:1139` (`patching_node`) → the pipeline wired from `:6042-6094`:

```
patching_node
  → speculative_node          (optional multi-variant attempt)
  → test_generation_node      (auto-generate tests if absent)
  → lintgate_node             (format / lint pre-check)
  → compiler_node             (run `make build` in sandbox)
       ├─ exit=0 → code_review_node → security_scan_node → END (+ optional deployment)
       └─ exit≠0 → repair_node ↔ compiler_node   (up to 5 repair cycles, then HITL)
```

Two correctness guards run inside the patcher (`harness/patcher.py:1860-1918`):

- **Read-before-edit.** When `enforce_read_before_edit=True`, the patcher rejects `REPLACE_BLOCK` / `DELETE_BLOCK` / `INSERT_AT_BLOCK` against any file the LLM has not been shown, telling it to emit `READ_FILE` first. The harness resolves the `READ_FILE` inline and re-dispatches the LLM in the same iteration.
- **Drift detection.** SHA-256 of each file as last shown to the LLM is compared against the current disk state. If an earlier patch in the batch (or an external editor) has changed the file, the next patch is rejected with a "file drifted" error so the LLM re-reads before retrying.

## 5. Bug fix vs feature add

There is **no branching** on intent. The graph is the same; the operator's prompt is the only signal:

- *"Fix the race condition in `app/session.py` when two writers acquire the lock simultaneously."* → LLM reads the file, patches it, the existing tests rerun, the repair loop converges.
- *"Add a `/refresh` endpoint to the auth router that issues a new JWT given a valid refresh token."* → LLM reads the router, adds the endpoint, `test_generation_node` writes tests for it, `compiler_node` runs them.

The mechanism that makes feature work tractable on a large existing codebase is the **source-root allowlist** (so new files land in the right place) plus the **stack-detected skills** (so the LLM knows the project's idioms) — not a separate "feature" path.

## Caveats & opt-in guards

- **No spec gate.** Greenfield runs go through a human gatekeeper that reviews the architecture spec before any code is generated. Existing-project runs skip that — the LLM acts on the prompt directly. If the prompt is vague, the first patching iteration may go in an unintended direction. Mitigation: be specific in the prompt, or pass `--discover true` to force the discovery interview even on existing code.
- **`enforce_read_before_edit` defaults to off.** Drift detection always runs when tracking is enabled, but the harder "must have read it" guard is opt-in via `config.json`. For risky multi-file refactors on a large codebase, turning it on prevents the LLM from blind-patching files it has not actually seen, at the cost of extra `READ_FILE` round-trips.
- **Repair budget is per-session.** The repair loop caps at 5 consecutive zero-progress cycles before escalating to HITL. On a large existing project with subtle build failures, that ceiling can hit faster than on greenfield.

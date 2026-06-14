# Existing-project flow

For operators running `harness run` against a repository that already has code — fixing a bug, adding a feature, or refactoring — rather than scaffolding from scratch. The greenfield (`--new_build=true` + `product_spec_dir`) path is covered separately in [`SPEC_ARCHITECTURE.md`](SPEC_ARCHITECTURE.md).

## Summary

Existing-project runs are file-driven. The operator drops one or more `.txt` files into a `change_requests/` folder at the workspace root; each file is a self-contained ask ("fix the race condition in `app/session.py`", "add a `/refresh` endpoint to the auth router", …). The harness assigns each one a monotonic `CR-N` ID, threads that ID through every artifact the session produces (spec revisions, source comments, test names, infra files, the commit trailer), and archives the consumed files into `change_requests/applied/<session-id>/` when the session ends.

The whole pipeline runs through the same human-in-the-loop gates that greenfield uses — requirements review, architecture review, gatekeeper approval — but the discovery nodes run in **delta mode**, asking "what's changing?" rather than "describe your system." A reader can `grep -rn "CR-7" .` after the session and see the spec wording, the implementation, the tests, the infra change, and the commit that satisfy CR-7 in one shot.

## 1. Wizard decides the mode

On bare `harness run`, the wizard (`harness/wizard.py`, wired from `harness/cli.py:38-135`) asks:

- **New session or resume?** Resume picks up a checkpointed run from SQLite.
- **`--new_build true|false`** — defaults `false` for existing code so the harness does not clobber your files. `false` routes to change-request mode; `true` is the greenfield path.
- **`--git enable|disable`** — branch / stash lifecycle.

When `--new_build=false`, the CLI enforces a **hard gate**: the `change_requests/` folder must contain at least one `.txt` file. An empty folder produces a loud error directing the operator to create the files and re-run — there is no implicit "use the existing product_spec" fallback.

When both `-p "…"` (CLI prompt) and a populated `change_requests/` folder are supplied, the folder wins and the prompt is dropped with a warning log line. The folder is the single source of truth for what the run is being asked to do.

## 2. Graph routing

`harness/graph.py` (the START router, also exported as the module-level `route_after_start`):

```python
def route_after_start(state):
    if state.get("change_request_mode", False):
        return "ingest_change_requests_node"     # existing-project path
    if state.get("skip_discovery", False):
        return "patching_node"                    # legacy bare path
    return "requirements_discovery_node"          # greenfield path
```

The existing-project pipeline:

```
START
 → ingest_change_requests_node          (parse folder, assign CR-N IDs)
 → reverse_engineer_architecture_node   (one-shot SPEC_ARCHITECTURE.md synthesis, first contact only)
 → requirements_discovery_node          (delta mode)
 → discovery_interview_loop             (HITL)
 → write_spec_node                      (delta mode — preserves prior spec)
 → spec_review_node                     (reviewer LLM, if configured)
 → human_gatekeeper_node                (HITL final approval)
 → architecture_discovery_node          (delta mode; short-circuits when nothing is architecture-significant)
 → deployment_discovery_node            (delta mode; same short-circuit)
 → generate_deployment_spec_node        (synthesizes blueprint + cr_attribution)
 → patching_node                        (CR-N marker injection)
 → speculative_node                     (optional)
 → test_generation_node                 (CR-N test naming + docstrings)
 → lintgate_node
 → compiler_node                        (build / repair loop, up to 5 cycles)
 → code_review_node → security_scan_node → deployment_node → END
```

Every HITL gate is the same code path greenfield uses; only the prompts inside the discovery nodes differ.

## 3. Change-request ingestion

`ingest_change_requests_node` (`harness/graph.py`) does four things:

1. **Sorted directory walk** of `change_requests/`, skipping the `applied/` archive subdirectory. Only `.txt` files at the top level are picked up.
2. **CR-N assignment**. The harness scans `change_requests/applied/**/CR-*.txt` to find the maximum existing `N`, then assigns `N+1, N+2, …` to the pending files in sorted order. First-ever session starts at `CR-1`.
3. **Operator-supplied IDs respected**. A filename matching `CR-<N>-<rest>.txt` keeps its `<N>`, so external trackers (Jira ticket IDs, GitHub issue numbers) map 1:1. Collisions with already-archived IDs abort the session with a clear error so the operator can rename and retry.
4. **Consolidated payload** — concatenates each file's contents under `# === CR-7: <relative-path> ===` separators and injects the result as the LLM's first user message, replacing the seed prompt.

The records (CR ID, original filename, absolute path) are recorded on state for the archival helper at session end.

## 4. Reverse-engineer architecture (first-contact only)

`reverse_engineer_architecture_node` runs once per repo to synthesize a baseline `SPEC_ARCHITECTURE.md` for projects that don't have one. Subsequent change-request sessions see the file present and skip the node entirely.

- **Context**: stack tags from `_detect_workspace_stack` (`harness/impact.py:635`), source root from `_detect_source_root` (`harness/impact.py:911`), and a representative file sample (≤30 files / ≤100 KB cumulative), biased toward entry-point basenames (`main.py`, `app.py`, `pyproject.toml`, `package.json`, `index.ts`, `go.mod`, …) and skipping noise dirs (`.git`, `node_modules`, `__pycache__`, `dist`, `build`, `.venv`, …).
- **One LLM call**: planning role, structured prompt asking for module map / data model / integration surface / build & runtime / known unknowns sections.
- **Budget gate**: `change_requests.reverse_engineer_budget_usd` in `config.json` (defaults to `$0.50`). When the remaining session budget is below the cap, the node logs and skips — the discovery pipeline that follows still runs, just without the synthesized baseline.

## 5. Delta-mode discovery + spec writing

The same `requirements_discovery_node` / `architecture_discovery_node` / `deployment_discovery_node` greenfield uses, but in change-request mode they get a phase-specific preamble (`_build_change_request_preamble` in `harness/graph.py`) telling the LLM to:

- Ask **delta-shaped** questions ("what's changing?", "what must NOT change?", "what's the acceptance test?") rather than re-eliciting baseline requirements.
- **Short-circuit** when none of the active CRs are significant in this phase — `architecture_discovery_node` and `deployment_discovery_node` are allowed to return `modules=[], complete=true` so light app-only fixes don't drag a full architecture or deployment review through the gatekeeper.
- **Tag** every passage they propose to add or modify with `<!-- BEGIN CR-N -->` / `<!-- END CR-N -->` HTML comments. The markers are invisible in rendered Markdown but grep-friendly in the raw file.

`write_spec_node` is **non-destructive** in change-request mode. It reads the existing `SPEC_REQUIREMENTS.md` (or `SPEC_ARCHITECTURE.md`, or `DEPLOYMENT_BLUEPRINT.md`), prepends a revision header naming the active CR IDs, and preserves the prior content verbatim below:

```markdown
## Revision: CR-7, CR-8 — session <id>

_(Existing spec preserved verbatim below; this section captures the delta
proposed by the listed change requests. Inline `<!-- BEGIN CR-N -->` markers
in the body link each modified passage to its originating request.)_

…new delta content…

---

…prior spec preserved verbatim…
```

Greenfield runs still see the original overwrite behaviour. Both flows share the same write_spec_node code path; the branch is `if state.get("change_request_mode") and os.path.isfile(spec_path)`.

## 6. CR-N markers on code, tests, and infrastructure

The `patching_node`, `repair_node`, `test_generation_node`, and the deployment synthesizer each see the CR preamble injected into their LLM prompts. The contract for each artifact:

- **Source code** — one terse comment per modified function / class / region, language-appropriate:
  - Python: `# CR-7: rate-limit check in middleware`
  - JS/TS: `// CR-7: rate-limit check in middleware`
  - Go / Rust / Java: idiomatic single-line comment for the language
  
  New files get the same one-line comment under the module docstring / imports. **Not** one marker per line touched — one per region.
- **Generated tests** — function names follow the `test_cr_N_<descriptive>` pattern (or the per-language idiom), and the CR is referenced in the test docstring (`"""Verifies CR-7: requests above the per-IP threshold receive 429."""`).
- **Deployment artifacts** — when `deployment_discovery_node` flags a CR as deployment-significant, the synthesis prompt asks the LLM to populate `blueprint["cr_attribution"]` as a mapping of service name → `"CR-N: <one-line reason>"`. `generate_assets_from_blueprint` (`harness/deploy.py`) reads the mapping and prepends a `# CR-N: <reason>` comment to each annotated service block in `docker-compose.yml`, the per-service `Dockerfile`, and the matching `Caddyfile` stanza. The kwarg `cr_attribution=...` is accepted explicitly too; both paths reach the same generators.
- **Commit message** — when git integration is enabled, the trailer gets a `Change-requests: CR-7, CR-8` line mirroring the `Co-authored-by:` convention.

After a session, `grep -rn "CR-7" .` finds the spec revision header, the inline spec markers, the modified source comments, the test function names / docstrings, the touched infra files, and the commit-message trailer — single command, full traceability.

## 7. Archival

At session end (including HITL escalation, except `[s] Save & Quit` which leaves the workspace ready to resume), the harness moves every consumed `.txt` file into `change_requests/applied/<session-id>/CR-N-<original-filename>.txt` and writes a `manifest.json` capturing:

```json
{
  "session_id": "...",
  "status": "success" | "failed-build" | "hitl-escalated" | "cancelled",
  "change_requests": [
    {"cr_id": 7, "archived_as": "CR-7-rewrite-auth.txt", "original_name": "rewrite-auth.txt"},
    ...
  ],
  "modified_files": ["app/auth.py", "tests/test_cr_7_auth.py", ...]
}
```

Re-running `harness run` on the same workspace with new `.txt` files at the top of `change_requests/` picks up the next CR ID (max archived + 1) and processes only the new pending files. The archive is the source of truth — no separate counter file to drift out of sync.

## 8. Config knobs

`config.json` keys consumed by the change-request flow:

| Key | Default | Purpose |
| --- | --- | --- |
| `change_requests_dir` | `"change_requests"` | Folder name at workspace root holding pending `.txt` files. Bare folder name only — no path separators or `..`. |
| `change_requests.reverse_engineer_budget_usd` | `0.50` | Hard cap on the one-shot LLM walk in `reverse_engineer_architecture_node`. Skipped when remaining session budget is below the cap. |
| `patcher.enforce_read_before_edit` | `false` | Drift detection always runs when tracking is enabled; this gate adds the stricter "LLM must have been shown the file before editing it" rule. Recommended `true` for large existing codebases. |

## What stays the same

This flow is **purely additive**. Greenfield runs (`--new_build=true` + `product_spec_dir`) behave byte-identically to before the change-request feature shipped. Every delta behaviour is gated on `state["change_request_mode"]`; when the flag is false, every existing node executes the same code path it always has. The HITL gate logic (interview loop, spec reviewer, gatekeeper) is shared between both flows — only the prompts inside the discovery nodes differ.

## Caveats & opt-in guards

- **`enforce_read_before_edit` defaults to off.** Drift detection always runs when tracking is enabled, but the harder "must have read it" guard is opt-in via `config.json`. For risky multi-file refactors on a large codebase, turning it on prevents the LLM from blind-patching files it has not actually seen, at the cost of extra `READ_FILE` round-trips.
- **Repair budget is per-session.** The repair loop caps at 5 consecutive zero-progress cycles before escalating to HITL. On a large existing project with subtle build failures, that ceiling can hit faster than on greenfield. Cap is configured via `node_throttle.max_patch_repair_iterations`.
- **Reverse-engineer is best-effort.** The first-contact `SPEC_ARCHITECTURE.md` synthesis sees only the file sample, not the whole codebase. Expect a coarse module map and "known unknowns" sections inviting follow-up — the gatekeeper review cycles are the right place to fill them in. Subsequent change-request sessions on the same repo reuse the (now operator-reviewed) baseline at zero LLM cost.
- **Resume sessions are exempt from the hard gate.** `harness resume --session-id <id>` continues an already-ingested session and does not re-walk the `change_requests/` folder.

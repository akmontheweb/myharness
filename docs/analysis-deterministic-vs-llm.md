# Analysis: Deterministic vs LLM-Driven Surface Area, and a Plan for the Deterministic Autofix Pass

## Purpose

The harness has grown into a hybrid system: a LangGraph state machine where some nodes are LLM-driven (planning, patching, repair, discovery, doc generation) and others are pure deterministic Python (build, lint, security scan, routing, spec compilation). This document records:

1. **§1 — Inventory**: what is deterministic today and what is LLM-driven, with file paths and line numbers so future maintainers can see exactly where each line sits.
2. **§2 — Hotspot ranking**: where the harness spends the most tokens.
3. **§3 — Recommendations**: seven candidate moves from LLM → deterministic, organized into three tiers by ROI.
4. **§4 — Implementation plan**: the concrete plan for Tier 1 (R1 + R2 + R3) so an implementer can pick it up without rediscovering the design.
5. **§5 — Deferred work**: R4–R7, captured so they stay visible as candidate future work.

The goal is not to eliminate the LLM — it's to stop spending tokens on work the harness can already do for free.

---

## §1 — Inventory

### 1.1 Currently deterministic (no LLM call)

| Surface | File | What it does |
|---|---|---|
| Build execution | `harness/sandbox.py:SandboxExecutor` | Runs build cmd in Docker/unshare/bare backend, parses diagnostics, returns `BuildResult` |
| Diagnostic parsing | `harness/parser_registry.py` (Rust/GCC/Go/Python/Java/TypeScript/Dart/Generic), `harness/sandbox.py:extract_diagnostics` | Native compiler-output → `DiagnosticObject` |
| Security scanning | `harness/security.py:security_scan_node` + adapters for gitleaks/bandit/semgrep/trivy | Run scanners in parallel, parse JSON, normalize severity, apply policy |
| Lintgate / formatters | `harness/lintgate.py` | Run language-canonical formatters (black, ruff, prettier, gofmt, rustfmt) |
| Patch application | `harness/patcher.py` (`ASTPatcher`, `TextPatcher`, `HybridPatcher`) | Parse LLM patch blocks, apply via tree-sitter or text |
| Sandbox adaptation | `harness/graph.py:_apply_toolchain_adaptation`, `cli.py:_detect_default_build_command` | Pick docker image + network flag from build cmd; sniff `make`/`pytest`/`npm test` |
| Stack detection | `harness/impact.py:_detect_workspace_stack` | Tag workspace (`python`, `react`, `ios`, `android`, …) from manifests/files |
| Style guides loader | `harness/style_guides.py`, `harness/graph.py:_load_skills_markdown` | Frontmatter-filtered markdown injection into system prompt |
| Routing (every `route_after_*`) | `harness/graph.py` lines 1280 / 1857 / 1902 / 1943 / 2010 | Pure state-machine functions, **zero LLM calls** |
| Spec compilation | `harness/graph.py:write_spec_node` (line 1674) | Walks conversation history, extracts Q&A blocks, writes Markdown — **no LLM** |
| Telemetry scan | `scan_workspace_telemetry` (used by `generate_deployment_spec_node`) | Extracts Docker/compose/volumes/ports from workspace deterministically |
| Git lifecycle | `harness/security.py:GitGuardian` | Patch branches, stash, commit-on-success / rollback-on-failure |
| Command validation | `harness/security.py:CommandValidator` | Allow/block-list shell commands before sandbox dispatch |
| Redactor | `harness/redactor.py` | Regex-based secret scrubbing in prompts and outputs |
| Storage / checkpoints | `harness/storage.py` | SQLite-backed LangGraph checkpoint persistence |

### 1.2 Currently LLM-driven

| Node | File / line | LLM role | Cost shape |
|---|---|---|---|
| `planning_node` | `graph.py:569` | `PLANNING` | **Largest single call.** System prompt 8–12 KB (tree + skills + style guides + cmd) + task. ~2–3 K input tokens, ~0.3–0.8 K output. |
| `patching_node` | `graph.py:642` | `PATCHING` | Mostly prompt-cached after planning; ~0.5–1 K new input + variable patch output. |
| `repair_node` | `graph.py:970` | `REPAIR` (cheap rounds 1–2, reasoning round 3) | Up to 3 iterations. Error summary 1–3 KB; message history grows per loop. |
| `requirements_discovery_node` | `graph.py:1332` | `PLANNING` | 8 sectors × 6–8 questions ≈ 48–64 question Q&A. 3–4 rounds typical. **6–12 KB total across rounds.** |
| `architecture_discovery_node` | `graph.py:1463` | `PLANNING` | 8 sectors × ~5 questions. Same loop pattern. |
| `deployment_discovery_node` | `graph.py:1573` | `PLANNING` | 4 sectors × 5 questions. Same loop pattern. |
| `generate_deployment_spec_node` | `graph.py:1770` | `PLANNING` (conditional) | Telemetry JSON + arch spec → blueprint Markdown. Falls back to deterministic telemetry snapshot when budget exhausted. |
| DocGen skills (5×) | `skills.py:432` (`DocGenSkill`) | `PLANNING` | On-demand, 1 call each. arch_doc / functional_spec / requirements / api_doc / readme. ~2–3 KB in, ~1–5 KB out per skill. |
| Generic `SubAgentSkill` | `skills.py:154` | `PATCHING` | User-defined sub-agents (lintgate.py registers some) loop up to `max_iterations`. |

### 1.3 Hybrid (deterministic shell, LLM in the body)

- **Memory cleanse**: deterministic compression rule (`apply_memory_cleanse`) but operates on LLM-generated messages.
- **HITL gate**: deterministic CLI menu (`hitl_menu_loop`) but resumes a graph whose downstream nodes are LLM-driven.
- **Gateway**: deterministic dispatch + model-selection logic (`harness/gateway.py`), wraps LLM calls.

---

## §2 — Token-cost hotspots, ranked

1. **`planning_node` first invocation** — full system prompt (8–12 KB) is the single biggest one-shot.
2. **Discovery loops** — 3 phases × 3–4 rounds × 2–3 KB per round = the biggest *cumulative* spend on a fresh project.
3. **`repair_node` on persistent failures** — up to 3 iterations, with reasoning model on round 3.
4. **`generate_deployment_spec_node`** — single-shot, but receives a wide telemetry blob.
5. **DocGen skills** — on-demand only; ~$0.01 each, but all 5 invoked ≈ $0.05.

Prompt prefix-caching at `messages[0]` blunts items 2–4 (~90 % discount on the repeated system prompt) so the marginal cost lives in the per-node delta, not the anchored prefix.

---

## §3 — Recommendations, by tier

Ordered by ROI (savings × applicability ÷ implementation risk). Each item is independently shippable.

### Tier 1 — High value, low risk, clear path (PLAN BELOW)

- **R1**: Capture compiler fix suggestions, apply machine-applicable ones without the LLM.
- **R2**: Auto-import / symbol-resolution before repair.
- **R3**: Deterministic fixes for common security findings.

R1 + R2 + R3 combined ≈ **50 % reduction in repair-loop LLM calls** on debug-heavy sessions, with zero loss of capability — the LLM still handles everything an autofixer cannot.

### Tier 2 — Medium value, more code, justified by per-call savings (DEFERRED)

- **R4**: Deterministic route + schema extraction for `api_doc_generator`.
- **R5**: Deterministic README sections from manifests.

See §5 for full descriptions.

### Tier 3 — Possible but contested (DEFER OR SKIP)

- **R6**: Discovery question banks per sector. Discovery is *intentionally* adversarial cross-exam — pre-canning weakens it. Recommend defer.
- **R7**: Pre-cached spec-template scaffolds. Marginal returns. Recommend skip.

### Headline numbers

If R1 + R2 + R3 ship together:

- Compile-error repair loops: ~50 % reduction in LLM calls.
- Security-finding repair loops: ~50 % reduction.
- Per-session token spend on debug-heavy sessions: 20–35 % reduction.
- DocGen calls (when R4 + R5 also ship): additional 60–70 % reduction on the *output* token side of api_doc + readme.

The deterministic moves do not reduce `planning_node` cost. The anchored prefix cost stays anchored — that's already optimal via prefix caching.

---

## §4 — Implementation plan: R1 + R2 + R3 deterministic autofix pass

### 4.1 Surface

A new module `harness/autofix.py` exposes one public entry point:

```python
def apply_autofixes(
    diagnostics: list[DiagnosticObjectDict],
    workspace_path: str,
) -> tuple[list[DiagnosticObjectDict], list[AutofixResult]]:
    """Return (unhandled_diagnostics, applied_fixes).

    Walks each diagnostic, dispatches to the appropriate autofixer
    (compiler-suggestion, missing-import, security), applies any
    successful fix via HybridPatcher, and returns the diagnostics
    that still need the LLM.
    """
```

Three internal dispatchers, one per recommendation:

- `_try_compiler_suggestion(d)` — R1
- `_try_missing_import(d, workspace_path)` — R2
- `_try_security_autofix(d, workspace_path)` — R3 (only fires when the diagnostic carries a scanner-prefixed `error_code`)

Each returns `Optional[PatchBlock]`. `apply_autofixes` runs each fix via the existing `HybridPatcher` from `harness/patcher.py`, then strips applied diagnostics from the unhandled list.

### 4.2 Wiring points (two)

**1. `repair_node`** (`harness/graph.py:970`) — at the very top, after `errors = state.get("compiler_errors", [])`, call `apply_autofixes(errors, workspace_path)`. The returned `unhandled` list replaces `errors` for the rest of the function. The `applied_fixes` list goes into `state["modified_files"]` and a system message describing what was auto-fixed. If `unhandled` is empty after the autofix pass, **skip the LLM call entirely** — return state with the modified files and let the router send back to `compiler_node` for verification.

**2. `security_scan_node`** (`harness/security.py:security_scan_node`) — after the block list is built (post `apply_policy`), call `apply_autofixes` on the block-list diagnostics. Anything the security autofixer resolved disappears from `compiler_errors`. If everything was resolved, the gate reports `passed=True` for this round. The router then routes to compiler_node (via the existing patching/repair → compiler loop) to re-verify the fix landed.

### 4.3 R1 — Compiler fix suggestions

Extend `DiagnosticObject` in `harness/sandbox.py` with one new field:

```python
@dataclass
class DiagnosticObject:
    ...existing fields...
    suggested_fix: Optional["FixSuggestion"] = None

@dataclass
class FixSuggestion:
    replacement: str             # exact text to substitute
    span_start_line: int         # 1-indexed
    span_start_col: int          # 1-indexed
    span_end_line: int
    span_end_col: int
    applicability: str           # "machine-applicable" | "maybe-incorrect" | "unspecified"
```

Update three parsers in `harness/parser_registry.py`:

- **`RustParser`** reads `children[].spans[].suggested_replacement` from `cargo --message-format=json` diagnostics. Map the `applicability` field directly.
- **`GccClangParser`** reads `fixits[]` from `-fdiagnostics-format=json` — gives `start.line/column`, `next.line/column`, `string`. Default applicability to `"machine-applicable"` (clang only emits fixits when it's confident).
- **`TypeScriptParser`** does NOT emit fixes via plain `tsc`. Skip — TS fixes come from the LSP, not the CLI. Tag as "future work" comment.

`_try_compiler_suggestion(d)` returns a `REPLACE_BLOCK` PatchBlock built from `(file, span, replacement)` **only when `applicability == "machine-applicable"`**. Maybe-incorrect / unspecified suggestions pass through to the LLM untouched — they need judgment.

### 4.4 R2 — Missing-symbol auto-import

Per-language matcher table keyed on `(parser_name, error_code_regex)`:

| Parser | Error pattern | Symbol extractor |
|---|---|---|
| Python | `NameError: name '(\w+)' is not defined` | regex group 1 |
| Python | `ImportError: cannot import name '(\w+)' from '(\S+)'` | group 1, hint module = group 2 |
| TypeScript | `TS2304` ("Cannot find name 'X'") | parse `'(\w+)'` from message |
| Rust | `E0425` ("cannot find value `X` in this scope") | parse backtick name |
| Go | `undefined: (\w+)` | regex group 1 |
| Java | `cannot find symbol\s+symbol:\s+(class\|method\|variable)\s+(\w+)` | group 2 |

For each match:

1. Walk the workspace using the existing tree-sitter parsers in `harness/patcher.py`. Look for top-level `class X` / `def X` / `function X` / `fn X` / `pub fn X` / `interface X` / `export const X` / `type X` defining the symbol.
2. If **exactly one** definition exists outside the offending file: build an `INSERT_AT_BLOCK` patch placing the appropriate import statement at the top of the offending file. Language-specific templates:
   - Python: `from {module_path} import {symbol}`
   - TypeScript: `import { {symbol} } from '{relative_path}';`
   - Rust: `use {crate_path}::{symbol};`
   - Go: `import "{package_path}"` (uses last path component as alias)
   - Java: `import {package}.{Class};`
3. If zero or multiple matches: return None — LLM handles it.

The "one match" rule is strict on purpose — ambiguity is the failure mode, and the LLM is good at resolving it. The wins come from the 80 % case where the user has just forgotten the import.

### 4.5 R3 — Security-finding autofixes

A registry mapping `(scanner, rule_id_pattern) → fix_fn`. Initial coverage:

| Scanner | Rule | Fix |
|---|---|---|
| `bandit` | `B201` (Flask debug=True) | `REPLACE_BLOCK`: `debug=True` → `debug=False` (line-local) |
| `bandit` | `B602` (`shell=True` in subprocess) | `REPLACE_BLOCK`: `shell=True` → `shell=False` (**only when args are already a list literal** — skip when string concatenation, that needs judgment) |
| `gitleaks` (any rule_id) | hardcoded secret | (a) `DELETE_BLOCK` removes the offending line, (b) `CREATE_FILE` / `INSERT_AT_BLOCK` adds `<RULE_ID>=<placeholder>` to `.env.example` |
| `trivy` (any rule with non-empty `FixedVersion` parsed from the `message`) | dep-vuln with available fix | `REPLACE_BLOCK` bumps the version pin in `requirements.txt` / `package.json` / `go.mod` / `Cargo.toml` (target file from `f.file`) |

Each fix returns None on ambiguity. The B608 SQL-injection class is **deliberately excluded** — the rewrite to parameterized queries needs context the autofixer can't capture safely.

The dispatch is data-driven so adding new rules is a single dict-entry change.

### 4.6 Behaviour glue

`apply_autofixes` returns a result list per applied fix:

```python
@dataclass
class AutofixResult:
    diagnostic_index: int          # which input diagnostic was resolved
    fix_kind: str                  # "compiler" | "import" | "security"
    rule_id: str                   # for telemetry
    file: str
    patch_block: PatchBlock
    apply_status: PatchResult      # from HybridPatcher
```

`repair_node` and `security_scan_node` use this to:

- Log a one-line "auto-fixed N of M findings" message.
- Append a system message to `messages` listing the auto-fixed items so the LLM (if it still gets called) doesn't see them and try to fix them again.
- Bump `state["modified_files"]` with the touched files.

The system message is also valuable telemetry — when users wonder why a fix happened without LLM cost, the trace shows it.

### 4.7 Critical files

- **New**: `harness/autofix.py` — orchestrator + the three dispatchers, ~250 lines.
- **Edit**: `harness/sandbox.py` — add `FixSuggestion` dataclass + `suggested_fix` field on `DiagnosticObject`.
- **Edit**: `harness/parser_registry.py` — `RustParser` and `GccClangParser` populate `suggested_fix`.
- **Edit**: `harness/graph.py:repair_node` — call `apply_autofixes` at the top; short-circuit when `unhandled` is empty.
- **Edit**: `harness/security.py:security_scan_node` — call `apply_autofixes` on the block list before populating `compiler_errors`.
- **New**: `tests/test_autofix.py` — unit tests for each dispatcher, fixture diagnostics per scanner / parser, the "exactly one definition" rule.

### 4.8 Reuse leverage already in place

- `HybridPatcher` (`harness/patcher.py`) — every autofix emits a `PatchBlock` and applies via `HybridPatcher.apply_patch`. AST safety, allowlist gating, idempotency on resume — all already wired.
- The patch parsing path used by LLM patches is exactly the same code applying these patches, so behaviour stays consistent.
- The tree-sitter language registry in `harness/patcher.py` already covers Python/JS/TS/Go/Rust/Java/Dart — symbol grep needs no new parsers.
- `SecurityFinding` and `_findings_to_diagnostics` in `harness/security.py` already carry the `rule_id`, `file`, `line`, and `message` the security autofixer reads.
- `_format_diagnostics_for_repair` (`harness/graph.py:1179`) doesn't change — it just operates on a shorter list.

### 4.9 Verification

**Unit tests** (`tests/test_autofix.py`, ~25 cases):

- **R1**: feed a fixture Rust diagnostic with `applicability: "machine-applicable"` and confirm a REPLACE_BLOCK is emitted with the right span and replacement. Repeat for "maybe-incorrect" and confirm None.
- **R1**: feed a fixture GCC diagnostic with a fixit and confirm a REPLACE_BLOCK is emitted.
- **R2**: build a tmp workspace with `foo/bar.py: def baz(): pass` and a `main.py: NameError: name 'baz' is not defined`. Assert the autofixer emits `from foo.bar import baz` as INSERT_AT_BLOCK. Repeat with two definitions and assert None.
- **R2**: TS / Rust / Go / Java equivalents.
- **R3**: Bandit B201 fixture → assert `debug=True` → `debug=False`.
- **R3**: Gitleaks fixture with a fake AWS key on line 5 → assert DELETE_BLOCK on line 5 + CREATE_FILE / INSERT for `.env.example`.
- **R3**: Trivy fixture with `FixedVersion: "4.17.21"` for `lodash` → assert `package.json` `"lodash": "4.17.20"` → `"4.17.21"`.

**Integration tests** (extend `tests/test_security_scan.py` and add to `tests/test_harness.py`):

- **Repair shortcut**: stub the gateway with a sentinel that records "the LLM was called". Build a state with one diagnostic that R1 can fix. Call `repair_node`. Assert the sentinel was NOT called AND `state["modified_files"]` includes the touched file.
- **Security shortcut**: similar pattern via `security_scan_node`. Build a state where the only finding is a Bandit B201. Run the node end-to-end. Assert `compiler_errors` is empty in the returned state (R3 resolved it) and the system message says auto-fixed.
- **Fall-through**: ambiguous diagnostic (two candidate definitions for an undefined symbol) — assert the LLM IS called for it.

**Regression**: full `pytest tests/` must pass. Current count 691; expect ~+25.

**Manual smoke**:

- Generate a tiny Python project with one obvious missing import → run the harness → confirm logs show `autofix: imported {symbol} from {module}` and zero LLM repair tokens spent.
- Inject `debug=True` in a Flask file, scan, confirm `autofix: bandit B201` and no LLM cost.

### 4.10 Deliberately not doing in this pass

- **TypeScript LSP integration** for fix suggestions. `tsc` CLI doesn't emit fixes; pulling them in needs a language-server bridge. Out of scope for R1.
- **ESLint `--fix-dry-run`** as a fix source. Worth adding later but lintgate already auto-applies eslint fixes — there's no diagnostic left for autofix to see.
- **SQL-injection autofix** (Bandit B608). Rewriting to parameterized queries is judgment work.
- **Dep-vuln autofix when `FixedVersion` is empty**. Originally called out: "don't loop on findings the model can't fix".
- **Telemetry assertion on token spend in CI**. Worth doing but a follow-on PR — needs a stable cost fixture.

---

## §5 — Deferred work (R4–R7)

### R4 — Deterministic route + schema extraction for `api_doc_generator`

**Today**: `DocGenSkill("api_doc")` (`skills.py:432`) sends the directory tree to the LLM and asks it to (1) find every route, (2) extract request/response schemas, (3) write narrative. Steps (1) and (2) are mechanical AST work — every framework has annotation-based registration (`@app.get(...)` / `@RestController` / Express `app.get(...)` / Angular Router config / Spring `@GetMapping`).

**Move**: new `harness/route_extractor.py` with per-framework adapters using the tree-sitter integration already in `harness/patcher.py`. Walk the workspace, emit a structured JSON list: `[{method, path, handler, request_schema, response_schema, file:line, docstring}]`. Pass that JSON to the LLM instead of the tree — the LLM only writes the narrative between endpoints.

**Savings**: 60–80 % of `api_doc_generator` output tokens. Per-call cost drops from ~$0.005 to ~$0.001. More importantly, the doc becomes *correct* — extraction errors are no longer possible.

**Risk**: low for the four common frameworks (FastAPI / Express / Spring / Angular). Fall back to current LLM-only path for unrecognized.

### R5 — Deterministic README sections from manifests

**Today**: `DocGenSkill("readme")` sends the tree to the LLM and asks for a full README including Installation, Project Structure, Build, Contributing, License sections. The first four are derivable mechanically (read `package.json` for install / build, walk the tree for structure, read `LICENSE`).

**Move**: extend the `_DOCGEN_SYSTEM_PROMPTS["readme"]` flow to pre-render the mechanical sections (Installation / Project Structure / Build / License) deterministically, and pass them to the LLM as a fill-in-the-narrative scaffold. The LLM writes only Overview, Quick Start, Usage Guide, Contributing.

**Savings**: 50 % of `readme_generator` output tokens.

**Risk**: very low — strictly additive scaffolding.

### R6 — Discovery question banks per sector (CONTESTED)

The three discovery nodes spend 6–12 KB total across rounds asking universal questions ("what are field length limits?", "what auth method?"). Many of these are project-agnostic. Could ship `harness/discovery_templates/{requirements,architecture,deployment}/{sector}.md` with the universal questions pre-canned, leaving the LLM to only generate project-specific follow-ups.

**Trade-off**: discovery is *intentionally* adversarial cross-exam — the LLM's ability to push beyond a checklist is the feature. Pre-canning weakens that. Likely savings 30–50 % per phase, but recommend keeping LLM gap-filling rounds intact.

**Recommend**: defer. Revisit if telemetry shows discovery loops genuinely dominate cost.

### R7 — Pre-cached spec-template scaffolds

`write_spec_node` is already deterministic; tightening the LLM's discovery-output JSON schema would make it even tighter, but marginal returns.

**Recommend**: skip.

---

## Provenance

This document was synthesized from a code audit of `/mnt/data1/akhila/mywork/projects/myharness`. It captures the analysis that motivated the R1 + R2 + R3 implementation; future maintainers should treat §4 as the implementation contract and §5 as a candidate backlog.

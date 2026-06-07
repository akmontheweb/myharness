# AI Agent Harness — Requirements Specification

*Auto-generated from exhaustive codebase analysis and architecture review.*

---

## 1. Executive Summary

AI Agent Harness is a production-grade, model-agnostic autonomous coding agent built on LangGraph. It accepts natural language engineering tasks, generates code patches using LLMs, sandboxes build verification, and applies validated changes to the workspace — all under budget controls, security guardrails, and persistence boundaries.

The system replaces manual edit-compile-fix cycles with an automated pipeline: planning → patching → linting → compilation → repair → deployment, with human-in-the-loop intervention points at every critical decision boundary.

---

## 2. Functional Requirements (FR)

### FR-001: Natural Language Task Acceptance
- **Description:** The system must accept a natural language engineering task via CLI (`-p` / `--prompt` flag) and execute it against a specified workspace directory (`-r` / `--workspace` flag).
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given a valid workspace path and task prompt, the system initiates graph execution
  - Given a missing workspace path, the system exits with code 1 and an error message
  - The task prompt is anchored as `messages[1]` (user role) in the AgentState

### FR-002: Multi-Provider LLM Gateway
- **Description:** The system must support dispatching LLM calls to multiple providers (DeepSeek, Anthropic, OpenAI, Ollama) through a unified interface, with per-node role model selection.
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given a GatewayConfig with planning_primary, patching_primary, repair_primary, the gateway routes calls to the correct provider per NodeRole
  - Given a provider API error, the system retries with exponential backoff + jitter (up to 3 attempts)
  - Given a budget below zero, the gateway refuses dispatch with a RuntimeError
  - Given a typo in model routing config, the system logs a warning and falls back to Ollama if configured (Graceful Typo Resilience, v1.1+)

### FR-003: Token Budget Enforcement
- **Description:** The system must enforce a hard dollar cap on LLM API spending per session, with cumulative tracking across all model calls.
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given a `hard_cap_usd` of $2.00, the system tracks cumulative cost in `token_tracker.total_cost_usd`
  - Given cost exceeds the cap, the gateway raises RuntimeError on the next dispatch attempt
  - Token costs are computed per each model's input/output pricing

### FR-004: Code Generation via Structured Patch Blocks
- **Description:** The patching node must generate code changes using a strict patch block syntax (CREATE_FILE, REPLACE_BLOCK, DELETE_BLOCK, INSERT_AT_BLOCK) that can be deterministically parsed and applied to disk.
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given an LLM response containing `<<<CREATE_FILE>>>...<<<END_CREATE_FILE>>>` blocks, the patcher creates new files at the specified paths
  - Given an LLM response containing `<<<REPLACE_BLOCK>>>...<<<END_REPLACE_BLOCK>>>` blocks, the patcher finds and replaces exact text matches
  - SEARCH blocks failing to match uniquely produce a PatchResult with `success=False` and a descriptive error
  - The `modified_files` list in AgentState tracks all successfully changed files
  - A strict format reminder is injected before every patching/repair LLM call to ensure compliance (v1.1+)

### FR-005: Sandboxed Build Verification
- **Description:** Every patch application must be verified by executing the project's build command inside an isolated sandbox (Docker container, Linux namespaces, or bare).
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given `allow_network=False`, the sandbox has no outbound network access
  - Build processes are terminated after `timeout_seconds` (default 300) via PGID-based signal escalation (SIGTERM → 3s → SIGKILL)
  - Raw build output is captured via disk-buffered log streaming (500MB max)
  - Structured compiler diagnostics (file, line, column, severity, error_code, message) are extracted from raw output
  - Auto-detection prioritizes Docker over unshare over bare (Docker-First strategy, v1.1+)

### FR-006: Automatic Repair Loop
- **Description:** When the compiler_node returns a non-zero exit code, the system must enter a repair loop where the LLM analyzes diagnostics and generates fix patches, with a maximum of 3 attempts before escalating to human intervention.
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given exit_code != 0 AND total_repairs < 3, the system routes to repair_node
  - Given exit_code != 0 AND total_repairs >= 3, the system routes to human_intervention_node
  - Repair attempts 1-2 use the repair_primary model; attempt 3 escalates to repair_fallback with thinking mode
  - When no structured diagnostics exist, the raw build output (last 2000 chars) is included in the repair prompt (v1.1+)
  - Lintgate errors are appended to the repair prompt context (v1.1+)

### FR-007: Human-in-the-Loop Intervention
- **Description:** When repair limits are exceeded or budget is exhausted, the system must present an interactive menu to the developer with options to resume, inject hints, manually edit files, increase budget, or abandon.
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given [v], the system displays active file diffs
  - Given [r], the system clears HITL flags, resets the loop counter, and routes to compiler_node
  - Given [e], the system appends the developer's hint as a user message and routes to repair_node
  - Given [m], the system pauses for manual IDE edits, clears compiler errors, and routes to compiler_node
  - Given [b], the system increases the budget by $2.00 and resets the loop counter
  - Given [q], the system sets hitl_abandon flag, attempts git rollback, and routes to END
  - When no structured diagnostics exist, raw build output is displayed in the HITL menu (v1.1+)

### FR-008: Checkpoint Persistence
- **Description:** The system must persist LangGraph state checkpoints to disk via SQLite, enabling crash recovery and session resumption across process restarts.
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given a session, each graph node transition is checkpointed to SQLite
  - Given `harness resume --session-id <id>`, the system restores state from the last checkpoint and continues execution
  - Checkpoints older than 30 days are garbage-collected on startup

### FR-009: Session Inspection
- **Description:** The system must provide a read-only `harness status` command that displays the current state of any checkpointed session without triggering graph execution.
- **Priority:** Should Have
- **Acceptance Criteria:**
  - Given `harness status --session-id <id>`, the system prints thread_id, current node, exit code, budget remaining, token cost, modified files, and loop counters
  - Given `harness status --all`, the system lists all checkpointed sessions by thread_id

### FR-010: Workspace Configuration Discovery
- **Description:** The system must discover configuration hierarchically: workspace `.harness_config.json` → `~/.harness/config.json` → shipped `cli.json` fallback, with deep merging of nested dictionaries.
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given no `.harness_config.json` exists in the workspace, the system auto-generates one from global + fallback configs
  - Given a key exists in both workspace and global config, the workspace value takes precedence
  - Nested dicts are deep-merged (shallow keys override, nested keys merge)

### FR-011: Git Lifecycle Management
- **Description:** Every harness session must operate on an isolated git patch branch, with automatic stashing of dirty state, commit on success, and rollback on failure.
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given a dirty workspace, pre-existing changes are stashed before branch creation
  - A branch named `agent/patch-{session_id[:8]}` is created off the current HEAD
  - On build success, all changes are committed with the session ID and exit code in the commit message
  - On build failure or `[q]` abandon, the system performs `git checkout -- .`

### FR-012: Secret Redaction
- **Description:** All messages sent to external LLM APIs must be scanned for secrets (API keys, tokens, JWT, credentials) and replaced with placeholders before transmission.
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Given a message containing a GitHub token pattern (`ghp_`), the token is replaced with `[REDACTED:sha256:<hash>]`
  - Given a message containing an OpenAI API key pattern (`sk-proj-`), the key is replaced with a placeholder
  - The original content is excluded from the outbound request
  - A `RedactionResult` tracks the count of replacements per call

### FR-013: Language-Aware Diagnostic Parsing
- **Description:** Compiler output must be parsed into structured diagnostics using language-specific parsers (Rust JSON, GCC/Clang JSON, Go regex, Python traceback, generic fallback).
- **Priority:** Should Have
- **Acceptance Criteria:**
  - Given Rust compiler output with `--error-format=json`, structured DiagnosticObjects are extracted with file, line, column, severity, error_code, message
  - Given Go compiler output (`file:line:col: message`), diagnostics are parsed via regex
  - Given unrecognized compiler output, the generic parser (`file:line:col: severity: message`) is attempted

### FR-014: Pre-Build Lint Gate
- **Description:** Modified files must be auto-formatted and optionally linted using deterministic local tools (ruff, gofmt, prettier, rustfmt) before the heavy compiler pipeline runs, to catch trivial issues without LLM cost.
- **Priority:** Should Have
- **Acceptance Criteria:**
  - Given modified Python files, `ruff format --quiet` is run on each file
  - Given modified Go files, `gofmt -w` is run on each file
  - Lint errors are logged as warnings and included in the repair prompt context
  - Lint gate failures do not block compilation
  - Lint errors are truncated at 500 chars to preserve full diagnostic context (v1.1+)

### FR-015: Speculative Multi-Variant Compilation
- **Description:** After patching, the system may generate N parallel variant patches (with temperature > 0) and compile them in isolated git worktrees, selecting the best variant by configurable strategy (first_success, fewest_changes, all_pass).
- **Priority:** Could Have
- **Acceptance Criteria:**
  - Given strategy=`first_success`, the first variant with exit_code=0 is selected as winner
  - Given strategy=`fewest_changes`, the passing variant with the fewest lines changed is selected
  - Each variant is compiled in an isolated `git worktree`

### FR-016: Deployment Orchestration
- **Description:** After successful compilation and security scan, the system must generate container assets (Dockerfile, docker-compose.yml, Caddyfile) and optionally deploy via docker-compose with health check polling.
- **Priority:** Could Have
- **Acceptance Criteria:**
  - Given workspace telemetry, a Docker Compose file is generated with per-service configurations
  - Given a deployment blueprint, `docker-compose up --build -d` is executed
  - Health checks poll `docker inspect` until containers report healthy or timeout (120s)

### FR-017: Skip Discovery Mode
- **Description:** The `--skip-discovery` / `-s` CLI flag must bypass the exhaustive requirements/architecture discovery phases and route directly to code generation.
- **Priority:** Should Have
- **Acceptance Criteria:**
  - Given `--skip-discovery`, the graph routes START → patching_node directly
  - The system prompt (snapshot-based) is used as messages[0] without modification

### FR-018: Memory Cleanse
- **Description:** After successful compilation or human intervention resolution, verbose intermediate repair-loop messages must be compressed into a single structured summary to conserve context window tokens.
- **Priority:** Should Have
- **Acceptance Criteria:**
  - Given compiler exit_code == 0, messages are cleansed, retaining system prompt + original planning message + final successful patch + compression summary
  - The compression summary includes target file, repair iterations, and debug token cost

### FR-019: Code Quality Standards Enforcement
- **Description:** The system prompt must include explicit code quality standards (modularity, error handling, type hints, edge cases, production-readiness) and both patching/repair format reminders must include a quality directive.
- **Priority:** Must Have
- **Acceptance Criteria:**
  - Messages[0] includes a "Code Quality Standards" section with 8 quality rules
  - Both patching_node and repair_node inject a one-line quality directive before LLM dispatch
  - Generated code must be modular, self-contained, and include proper error handling

---

## 3. System Scope

### In Scope
- Autonomous code generation and patching for any text-based language
- Multi-provider LLM integration with budget tracking
- Docker-first sandboxed build execution with unshare and bare fallbacks
- Structured diagnostic parsing for Rust, C/C++, Go, Python, and generic compilers
- Automatic repair loops with cross-model escalation and raw output fallback
- Human-in-the-loop intervention menus with raw build output display
- SQLite-based checkpoint persistence with resume capability
- Read-only session status inspection
- Git lifecycle management (isolated branches, stash, commit, rollback)
- Secret redaction before API transit
- Pre-build lint formatting (ruff, gofmt, prettier, rustfmt)
- Configuration discovery with graceful fallback for typos
- CLI with subcommand routing (run, resume, status, purge)
- Optional discovery pipeline (requirements/architecture/deployment interviews)
- Speculative multi-variant compilation
- Code quality standards embedded in all LLM prompts

### Out of Scope
- Real-time collaborative editing
- Web UI or graphical interface (CLI only)
- Windows sandbox isolation (Linux namespaces / Docker only)
- Direct database or file system migrations (only code generation)
- CI/CD pipeline integration (runs as standalone CLI tool)
- Multi-repository or monorepo-aware analysis
- Code review or pull request generation
- IDE plugin integration

---

## 4. Technical Constraints

### Language & Runtime
- **Language:** Python 3.11+
- **Orchestration:** LangGraph ≥ 0.4.0
- **Async:** asyncio throughout (aiofiles, httpx async client, aiosqlite)
- **Type Safety:** TypedDict for LangGraph state; Pydantic for validation

### Platform
- **Primary Target:** Linux (x86_64, aarch64)
- **Sandbox Backend (Primary):** Docker container with resource limits (v1.1+ Docker-First)
- **Sandbox Backend (Secondary):** Linux kernel namespaces via `unshare(2)` 
- **Sandbox Backend (Tertiary):** Bare subprocess with diagnostic warning
- **macOS Support:** Docker backend only (no `unshare` on macOS)
- **File System:** ext4 / APFS / NTFS for workspace; `~/.harness/` for persistence

### Performance Targets
- **LLM Latency:** Gateway dispatch completes within API provider SLA (typically 2-60s)
- **Build Execution:** Sandbox runs with configurable timeout (default 300s)
- **Log Streaming:** Supports build output up to 500MB via disk-buffered streaming
- **Persistence:** WAL-mode SQLite supports concurrent read/write with < 50ms latency

### Security Requirements
- No secrets transmitted to external APIs (redaction enforced before every call)
- No outbound network access from sandbox unless explicitly enabled (`--allow-network`)
- Sandbox process containment via PID namespaces and PGID-based termination
- Command validation for whitelist/blocklist before sandbox execution
- Git isolation: all changes on temporary branches, no direct main/master mutation

---

## 5. Explicit Edge Cases

### Error States
| Condition | System Behavior |
|-----------|----------------|
| LLM API returns 5xx | Gateway retries with exponential backoff + jitter (3 attempts) |
| LLM API returns 4xx (auth) | Gateway logs error, returns RuntimeError to graph node |
| Budget exhausted mid-execution | Gateway refuses dispatch; node returns budget_exhausted flag; router sends to HITL |
| Build command not found | Sandbox returns exit code 127; structured diagnostic generated |
| Build times out (default 300s) | PGID SIGTERM → 3s → SIGKILL; BuildResult.timed_out = True |
| Patch SEARCH block not found | TextPatcher returns PatchResult(success=False) with closest-match context |
| Patch file already exists (CREATE_FILE) | PatchResult(success=False, error="File already exists") |
| SQLite database locked | WAL mode + busy_timeout=5000ms handles concurrent access |
| Secret redactor false positive | Placeholder masks content; developer can inspect original in local logs |
| Git rollback fails (no git repo) | Warning logged; session proceeds without git lifecycle management |
| Discovery interview receives EOF | Current state saved; system exits gracefully with non-zero code |
| HITL menu receives EOF/KeyboardInterrupt | Session abandoned; git rollback attempted |
| Build produces no structured diagnostics | Raw build output displayed in HITL menu and included in repair prompt |
| Typo in model routing config | Logger.error with suggestion; auto-falls back to Ollama if configured |

### Boundary Conditions
| Condition | Limit |
|-----------|-------|
| Maximum repair loop iterations | 3 (configurable via `node_throttle.max_patch_repair_iterations`) |
| Maximum security fix attempts | 2 |
| Default budget cap | $2.00 (configurable via `token_budget.hard_cap_usd`) |
| Context window threshold | 85% of model's context_window (configurable) |
| Maximum sandbox build output | 500MB disk-buffered |
| Maximum session TTL | 30 days (configurable via `persistence.ttl_days`) |
| Maximum log tail fallback | Last 50 lines (when no critical patterns match) |
| System prompt max depth | 4 directory levels, 50 files per directory |
| Speculative variants | 3 parallel worktrees |
| Raw output in repair prompt | Last 2000 chars |
| Lint error truncation | 500 chars |

### Recovery Scenarios
| Scenario | Recovery |
|----------|----------|
| Process crash mid-execution | Checkpoint stored after every node; `harness resume --session-id <id>` restores |
| Power loss / reboot | SQLite WAL journal replays on next connection |
| LLM returns unusable response | Node catches exception, logs error, routes to next node or HITL |
| Docker daemon unreachable | Sandbox auto-falls-back to UnshareBackend → BareBackend |
| tree-sitter grammar not installed | HybridPatcher auto-falls-back to TextPatcher |
| aiofiles not installed | All file operations fall back to sync `open()` |

---

## 6. Non-Functional Requirements

### Reliability (NFR-001)
- **Target:** 99% of session completions within 3 repair attempts or successful HITL resolution
- **Measurement:** Track repair_loop_limit HITL triggers vs successful compilations per session
- **Graceful Degradation:** Docker → unshare → bare sandbox chain; remote LLM → local Ollama fallback

### Scalability (NFR-002)
- **Target:** Support workspaces up to 10,000 files without system prompt bloat
- **Implementation:** Directory tree snapshot limited to 4 levels depth, 50 files per directory

### Observability (NFR-003)
- **Target:** All node transitions, LLM calls, and build executions logged with structured data
- **Log Levels:** DEBUG (full LLM responses), INFO (node transitions + costs), WARNING (recoverable errors), ERROR (unrecoverable)
- **Token Tracking:** Per-model input/output/cached token counts + USD cost aggregated in state

### Maintainability (NFR-004)
- **Target:** Adding a new LLM provider requires only a new Provider class implementing `dispatch()` 
- **Target:** Adding a new language parser requires only a new class with `parse_diagnostics()` and registration in `parser_registry`
- **Target:** Adding a new formatter requires only a `FormatterSpec` entry in the defaults dict

### Security (NFR-005)
- **Target:** Zero credentials in any external network call
- **Target:** All sandboxed processes die on timeout; no orphan processes
- **Target:** Workspace never mutated on the original branch; all changes isolated to patch branches
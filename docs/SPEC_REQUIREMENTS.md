# AI Agent Harness — Requirements Specification

*Auto-generated from exhaustive codebase analysis.*

---

## 1. Executive Summary

AI Agent Harness is a production-grade, model-agnostic LangGraph agent for autonomous code generation, sandboxed build execution, and bulletproof persistence. It transforms natural language engineering prompts into verified, compiler-passing code patches within an isolated execution environment, with human-in-the-loop gates at every architectural boundary.

---

## 2. Functional Requirements (FR)

### FR-001: Multi-Provider LLM Gateway
- **Description**: The system shall provide a model-agnostic gateway supporting DeepSeek, Anthropic, OpenAI, and Ollama providers with automatic model selection based on node role (planning, patching, repair).
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given a configured `.harness_config.json` with model routing rules, When a graph node dispatches an LLM call, Then the correct provider and model is selected for the node's role
  - Given prefix caching is enabled, When the system prompt is anchored at messages[0], Then downstream providers receive the cached prompt prefix with discounted token rates
  - Given the budget remaining drops below $0.05, When a dispatch is attempted, Then the gateway automatically falls back to local Ollama inference

### FR-002: Autonomous Code Patching via LLM
- **Description**: The system shall parse LLM responses containing SEARCH/REPLACE blocks and apply them to workspace files using a hybrid tree-sitter AST-aware + text exact-match engine.
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given an LLM response with `<<<REPLACE_BLOCK>>>`, `<<<CREATE_FILE>>>`, `<<<DELETE_BLOCK>>>`, or `<<<INSERT_AT_BLOCK>>>` tags, When processed by the patcher, Then files are modified exactly as specified
  - Given a file with a registered tree-sitter grammar, When a replace block targets it, Then the AST-aware patcher is used (preserving surrounding formatting)
  - Given a file without a tree-sitter grammar, When a patch is applied, Then the text exact-match fallback is used
  - Given a patch search block matches 0 or >1 times, When applied, Then the operation fails with a clear error message

### FR-003: Sandboxed Build Execution
- **Description**: The system shall execute build commands inside isolated environments with pluggable backends (Linux unshare namespaces, Docker containers, or bare subprocess).
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given the backend is configured as "auto", When the sandbox initializes, Then the best available backend is auto-detected (unshare → docker → bare)
  - Given network is disabled (`allow_network: false`), When a build command runs, Then the process has no outbound network access
  - Given a build times out after `timeout_seconds`, When the process group is killed, Then SIGTERM is sent followed by SIGKILL after 3 seconds
  - Given compiler output contains structured errors, When the sandbox finishes, Then structured `DiagnosticObject` entries are extracted per language parser

### FR-004: Automated Repair Loop
- **Description**: The system shall automatically retry failed builds with LLM-generated repair patches, escalating from cheap models to expensive reasoning models on the final attempt.
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given a build fails with exit code ≠ 0, When repairs remain under 3, Then the repair_node generates a fix patch and re-verifies
  - Given repair attempts 1-2 fail, When attempt 3 is reached, Then the expensive fallback model is used with thinking mode enabled
  - Given 3 repair attempts all fail, When the loop limit is hit, Then the human_intervention_node is triggered
  - Given budget is exhausted at any point, When routing occurs, Then human_intervention_node takes priority over repair_node

### FR-005: Human-in-the-Loop Intervention
- **Description**: The system shall present interactive menus when repair limits or budget caps are reached, allowing developers to view diffs, inject hints, pause for manual edits, increase budget, or abandon.
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given HITL is triggered, When the developer chooses [e] inject hint, Then the hint is appended to messages and the repair loop counter is reset
  - Given HITL is triggered, When the developer chooses [m] manual edits, Then the harness pauses and waits for IDE edits before resuming compilation
  - Given HITL is triggered, When the developer chooses [q] abandon, Then git rollback is attempted and the session ends
  - Given HITL is triggered, When the developer chooses [b] increase budget, Then $2.00 is added to the remaining budget

### FR-006: Three-Phase Exhaustive Discovery
- **Description**: Before code generation, the system shall conduct exhaustive requirements, architecture, and deployment discovery using the planning LLM across 8+ structured sectors each.
- **Priority**: Should Have
- **Acceptance Criteria**:
  - Given the requirements discovery node executes, When the LLM returns JSON questions, Then 8 sectors are covered (input validation, payload formatting, error handling, multi-user edge cases, security controls, business logic, data retention, hidden assumptions)
  - Given critical questions remain unanswered, When the user types DONE, Then the system refuses to proceed and displays a warning
  - Given a phase is approved at the human gatekeeper, When the gatekeeper routes forward, Then the next discovery phase begins (requirements → architecture → code generation)

### FR-007: Exhaustive Deployment Infrastructure Discovery
- **Description**: The system shall cross-examine the user across 4 deployment-specific sectors (network topology, data/storage persistence, secrets/identity management, partial infrastructure sync) before generating container assets.
- **Priority**: Should Have
- **Acceptance Criteria**:
  - Given the deployment discovery node executes, When the LLM generates questions, Then 4 deployment sectors are covered
  - Given discovery is complete, When routing proceeds, Then `generate_deployment_spec_node` produces `DEPLOYMENT_BLUEPRINT.md`

### FR-008: Deterministic Lint & Format Gate
- **Description**: The system shall run language-specific auto-formatters (ruff, gofmt, prettier, rustfmt, clang-format) on modified files after patching but before compilation.
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given `.py` files are modified, When lintgate runs, Then `ruff format --quiet` is applied
  - Given `.go` files are modified, When lintgate runs, Then `gofmt -w` is applied
  - Given no formatter is installed for a file extension, When lintgate runs, Then the file is skipped with a debug log (no error raised)

### FR-009: Multi-Variant Speculative Compilation
- **Description**: The system shall generate N parallel patches with diverse temperatures, compile each in an isolated git worktree, and select the first passing variant.
- **Priority**: Could Have
- **Acceptance Criteria**:
  - Given `num_variants` is 3, When speculate_node runs, Then 3 LLM calls are made with temperature > 0
  - Given at least one variant passes compilation, When selection occurs, Then the winner's files are copied back to the main workspace
  - Given all variants fail, When speculation completes, Then the system falls back to sequential single-patch repair flow

### FR-010: Impact Analysis Warnings
- **Description**: Before patches are applied, the system shall scan the dependency graph and warn about downstream files potentially affected by the modifications.
- **Priority**: Should Have
- **Acceptance Criteria**:
  - Given file `core/auth.py` is modified, When impact analysis runs, Then files importing from `core/auth.py` are listed in a warning message
  - Given the dependency graph has not been built, When `analyze()` is called, Then the graph is lazily built on first use

### FR-011: Secret Redaction
- **Description**: The system shall scan all outbound LLM messages for API keys, tokens, passwords, private keys, and connection strings, replacing them with hashed placeholders.
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given a message contains `sk-proj-...`, When redaction runs, Then the key is replaced with `[REDACTED:OpenAI API key:sha256:xxxxxxxx]`
  - Given the redaction mode is "mask", When a secret is found, Then it is replaced with `[REDACTED]`
  - Given no secrets are present, When redaction runs, Then the text is returned unmodified

### FR-012: Token Budget Enforcement
- **Description**: The system shall enforce a hard USD cap on LLM API calls, refusing dispatch when `budget_remaining_usd <= 0`.
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given budget_remaining_usd is $0.00, When `gateway.dispatch()` is called, Then `RuntimeError` is raised
  - Given budget_remaining_usd drops below $0.05, When dispatch is attempted, Then the gateway auto-switches to local Ollama

### FR-013: Git Lifecycle Management
- **Description**: The system shall create isolated patch branches per session, commit on success, and rollback on failure.
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given a session starts, When `create_patch_branch()` is called, Then a branch named `agent/patch-{session_id[:8]}` is created
  - Given build exit code is 0, When the session completes, Then changes are committed and the original branch is restored
  - Given build exit code ≠ 0, When the session completes, Then the working tree is rolled back and the patch branch is deleted

### FR-014: Checkpoint Persistence
- **Description**: The system shall persist LangGraph state to SQLite for crash recovery and cross-process resume.
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given a graph executes with a checkpointer, When state transitions, Then checkpoints are saved to `~/.harness/checkpoints.db`
  - Given checkpoints older than 30 days, When GC runs, Then those rows are deleted
  - Given `harness status --session-id <id>` is run, When the checkpoint exists, Then a clean text summary is displayed without triggering any graph execution

### FR-015: Command Whitelist Validation
- **Description**: The system shall block dangerous shell commands (curl, wget, sudo, rm -rf /, etc.) before build execution.
- **Priority**: Must Have
- **Acceptance Criteria**:
  - Given a build command contains `curl`, When validated, Then `ValueError` is raised with a security block message
  - Given a build command is `make build`, When validated, Then the command passes

### FR-016: Security Scanning (SAST + Secrets)
- **Description**: After compilation succeeds, the system shall run gitleaks (secret detection) and bandit/semgrep (SAST) in parallel, routing findings back to the repair loop.
- **Priority**: Should Have
- **Acceptance Criteria**:
  - Given a security scan finds hardcoded secrets, When `compiler_errors` is populated, Then the router sends findings to `patching_node` for remediation
  - Given 2 security fix attempts fail, When the attempt limit is reached, Then the router sends the session to HITL

### FR-017: Documentation Generation
- **Description**: The system shall provide skill-based documentation generators for README, architecture, functional spec, requirements, and API reference documents.
- **Priority**: Could Have
- **Acceptance Criteria**:
  - Given `register_builtin_skills()` is called, When skills are registered, Then 5 DocGenSkill instances plus 3 PipelineSkill instances are registered
  - Given a docgen skill executes, When the LLM responds, Then the output is written to the specified file path

---

## 3. System Scope

### In-Scope
- Multi-provider LLM gateway (DeepSeek, Anthropic, OpenAI, Ollama)
- Autonomous code patching with AST-aware + text fallback
- Isolated sandbox build execution (unshare, Docker, bare)
- Automated build repair with cross-model escalation
- Human-in-the-loop intervention menus
- Exhaustive requirements/architecture/deployment discovery
- Deterministic pre-build linting and formatting
- Multi-variant speculative compilation
- Secret redaction before LLM transit
- SAST + secret security scanning
- Git branch lifecycle management
- SQLite checkpoint persistence with TTL GC
- Command whitelist security validation
- Documentation skill generation

### Out-of-Scope
- IDE/editor integration (VS Code extension, etc.)
- Real-time collaboration features
- Kubernetes cluster deployment (only docker-compose)
- Web UI dashboard
- Multi-agent orchestration beyond the single-agent LangGraph
- Payment/billing integration

---

## 4. Technical Constraints

### Language & Runtime
- **Language**: Python 3.11+
- **Framework**: LangGraph ≥ 0.4.0 for state machine orchestration
- **Persistence**: aiosqlite with WAL journal mode
- **File I/O**: aiofiles ≥ 24.0 with sync fallback
- **HTTP Client**: httpx ≥ 0.28 with async transport

### Infrastructure
- **Sandbox Backends**: Linux unshare namespaces (primary), Docker containers, bare subprocess
- **Network**: Disabled by default in sandbox; toggleable via `allow_network` config
- **Resource Limits**: Configurable memory, CPU, and PID limits for Docker backend

### Performance Targets
- **Build Timeout**: 300 seconds default, configurable
- **LLM Retry**: 5 attempts max with exponential backoff + jitter
- **Context Window**: 85% threshold triggering aggressive truncation
- **Log Buffer**: Disk-buffered by default with 500MB max, supports in-memory mode

### Security
- **Secrets**: Strip/hash API keys, tokens, private keys before API transmission
- **Commands**: Whitelist-only validation; curl, wget, sudo blocked
- **Git**: Isolated patch branches, automatic rollback on failure
- **Network**: Namespace-level network isolation in sandbox

---

## 5. Explicit Edge Cases

### Error States
- **Budget Exhausted**: Gateway raises RuntimeError; router sends to HITL node
- **Model Not Registered**: `create_provider()` raises ValueError with available providers listed
- **Context Window Exceeded**: Aggressive truncation keeping system prompt + last message; raises if still over threshold
- **API Rate Limited (429)**: Exponential backoff with Retry-After header support
- **Server Error (5xx)**: Exponential backoff up to max_retries
- **Tree-Sitter Unavailable**: Graceful fallback to TextPatcher with debug log
- **Docker Not Installed**: Falls back to unshare or bare backend
- **Unshare Permission Denied**: Falls back to bare backend
- **Empty LLM Response**: Raised as RuntimeError in synthesis workflows
- **Invalid JSON from LLM**: Caught with fallback blueprint generation / error state
- **File Not Found (patching)**: PatchResult with clear error, operation skipped
- **Duplicate Search Match**: PatchResult failure with count reported
- **Build Timed Out**: SIGTERM → 3s wait → SIGKILL escalation
- **Corrupt JSON in diagnostics**: Graceful skip of bad line, continue parsing

### Boundary Conditions
- **Max Repair Iterations**: 3 (routes to HITL on 4th failure)
- **Max Security Fix Attempts**: 2
- **Max Discovery Rounds**: No hard limit; user types DONE to finalize
- **Max Files Scanned (impact)**: 500
- **Max Log Size (disk mode)**: 500MB before truncation
- **Max Variants (speculative)**: Configurable, default 3
- **Budget Hard Cap**: Default $2.00 USD
- **Context Window Threshold**: 85% of model limit
- **TTL for Checkpoints**: 30 days

### Recovery Scenarios
- **Process Crash During Graph Execution**: Checkpointer restores from last saved checkpoint
- **Network Partition During LLM Call**: Exponential backoff retry up to 5 attempts
- **Partial Patch Application**: HybridPatcher stops at first failure to prevent cascading errors
- **Cached Sessions**: `harness resume --session-id` picks up from exact checkpoint boundary
- **Memory Cleanse on Success**: Verbose debugging messages compressed into summary to preserve context budget

---

## 6. Non-Functional Requirements

### NFR-001: Reliability
- Graph state is persisted to SQLite on every transition
- WAL journal mode prevents corruption on crash
- Automatic 30-day TTL garbage collection prevents unbounded DB growth

### NFR-002: Model Agnosticism
- No provider is hardcoded as default
- All models must be explicitly registered via config
- Provider-specific response parsing is abstracted behind `BaseLLM` interface

### NFR-003: Observability
- Structured logging at DEBUG/INFO/WARNING/ERROR levels
- Per-node logging with `[node_name]` prefixes
- Token tracking aggregated per-model in state
- Build output streamed to disk with line-by-line filtering

### NFR-004: Configurability
- Hierarchical config discovery: workspace `.harness_config.json` → `~/.harness/config.json` → `cli.json` fallback
- Deep merge semantics for nested config keys
- CLI flags override config values

### NFR-005: Extensibility
- Pluggable sandbox backends via `SandboxBackend` ABC
- Pluggable LLM providers via `BaseLLM` ABC
- Pluggable skill registry for tools, pipelines, and sub-agents
- Pluggable diagnostic parser registry per compiler/language
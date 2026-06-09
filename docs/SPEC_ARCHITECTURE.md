# AI Agent Harness — Architecture Specification

*Refreshed from current codebase state. Companion to `SPEC_REQUIREMENTS.md`.*

---

## 1. System Context (C4 Level 1)

AI Agent Harness sits between the developer and their codebase, acting as an autonomous engineering agent. It accepts natural language prompts, generates code patches, verifies them via sandboxed builds, and applies them to the workspace — all under budget and security guardrails.

```
┌──────────────┐     ┌─────────────────────────────────────┐     ┌──────────────┐
│              │     │                                     │     │              │
│  Developer   │────▶│       AI Agent Harness              │────▶│   Git Repo   │
│  (CLI/IDE)   │     │  (LangGraph Agent + Sandbox)        │     │  (Workspace) │
│              │◀────│                                     │◀────│              │
└──────────────┘     └──────────────┬──────────────────────┘     └──────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
             ┌──────────┐   ┌──────────┐   ┌──────────────┐
             │ DeepSeek │   │ Anthropic│   │ Ollama (Local)│
             │   API    │   │ (Claude) │   │              │
             └──────────┘   └──────────┘   └──────────────┘
                    │               │               │
                    ▼               ▼               ▼
             ┌─────────────────────────────────────────┐
             │          LLM Gateway (harness/gateway)   │
             │  - Model routing by NodeRole             │
             │  - Budget enforcement                    │
             │  - Secret redaction before transit        │
             │  - Context window guardrail              │
             │  - Exponential backoff + jitter          │
             └─────────────────────────────────────────┘
```

**External Systems:**
- **DeepSeek API** — Primary cheap model for patching (OpenAI-compatible `/v1/chat/completions`)
- **Anthropic API** — Reasoning/fallback model for repair escalation (`/v1/messages`)
- **OpenAI API** — Optional provider (`/v1/chat/completions`)
- **Ollama** — Local inference server, zero-cost fallback, used when budget is low or `force_local_only` is set

---

## 2. Container Diagram (C4 Level 2)

The harness is a single-process Python application with these deployable/service boundaries:

```
┌────────────────────────────────────────────────────────────────────┐
│                       HARNESS CLI PROCESS                          │
│                                                                    │
│  ┌───────────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│  │   CLI Layer        │  │  Persistence      │  │  Git Lifecycle │  │
│  │  (harness/cli.py)  │  │  (harness/storage)│  │  (harness/     │  │
│  │                    │  │                   │  │   security.py) │  │
│  │  - Argparse        │  │  - AsyncSqliteSaver│  │                │  │
│  │  - Config discovery│  │  - Checkpoint CRUD │  │  - Patch branch│  │
│  │  - HITL menus      │  │  - 30-day TTL GC  │  │  - Stash/dirty │  │
│  │  - Subcommand routing│ │  - Status inspector│  │  - Commit/     │  │
│  └─────────┬─────────┘  └────────┬─────────┘  │    rollback    │  │
│            │                     │             └────────────────┘  │
│            ▼                     ▼                                 │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    LangGraph Runtime                         │   │
│  │                   (harness/graph.py)                         │   │
│  │                                                              │   │
│  │  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐ │   │
│  │  │ Planning │──▶│ Patching │──▶│  Lint    │──▶│ Compiler │ │   │
│  │  │   Node   │   │   Node   │   │  Gate    │   │   Node   │ │   │
│  │  └──────────┘   └──────────┘   └──────────┘   └────┬─────┘ │   │
│  │                                                     │       │   │
│  │                              ┌──────────────────────┼───┐   │   │
│  │                              │    exit 0?           │   │   │   │
│  │                              │  yes         no      │   │   │   │
│  │                              ▼              ▼       │   │   │   │
│  │                       ┌──────────┐   ┌──────────┐  │   │   │
│  │                       │ Security │   │  Repair  │  │   │   │
│  │                       │   Scan   │   │   Node   │  │   │   │
│  │                       └────┬─────┘   └────┬─────┘  │   │   │
│  │                            │              │         │   │   │
│  │                            ▼              ▼         │   │   │
│  │                       ┌──────────┐   ┌──────────┐  │   │   │
│  │                       │ Deploy   │   │   HITL   │  │   │   │
│  │                       │   Node   │   │   Node   │  │   │   │
│  │                       └────┬─────┘   └──────────┘  │   │   │
│  │                            │                        │   │   │
│  │                            ▼                        │   │   │
│  │                         [END]                       │   │   │
│  └─────────────────────────────────────────────────────┘   │   │
│                                                              │   │
│  ┌──────────────────────────────────────────────────────────┐│   │
│  │              Discovery Pipeline (Three-Phase)             ││   │
│  │  requirements → interview → write_spec → gatekeeper →     ││   │
│  │  architecture → interview → write_spec → gatekeeper →     ││   │
│  │  deployment → interview → write_spec → gatekeeper → END   ││   │
│  └──────────────────────────────────────────────────────────┘│   │
└────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Diagram (C4 Level 3)

### 3.1 Module Decomposition

```
harness/
├── __init__.py           # Package init, version, __all__
├── cli.json              # Fallback defaults (shipped config)
├── cli.py                # CLI entry, subcommand routing (run / resume / status / doctor / purge),
│                         # HITL menus, config discovery, doctor healthchecks
│   ├── discover_config()        # Hierarchical merge: workspace → home → cli.json
│   ├── _validate_config_keys()  # Recursive top-level + nested typo detection (FR-030)
│   ├── cmd_run / cmd_resume / cmd_status / cmd_purge
│   ├── cmd_doctor()             # 5-check healthcheck (FR-025)
│   ├── _doctor_check_git / api_keys / sandbox / checkpoint_db / config
│   ├── human_gatekeeper_node()  # Three-phase HITL gatekeeper
│   ├── hitl_menu_loop()         # 7-action HITL menu: [v/r/e/m/b/s/q]
│   └── interactive_review_loop()# Pre-flight manifest review
├── gateway.py            # Model-agnostic LLM Gateway
│   ├── GatewayConfig     # Runtime config dataclass
│   ├── Gateway           # Orchestrator: dispatch + budget + retry
│   ├── DeepSeekProvider  # OpenAI-compatible /v1/chat/completions
│   ├── AnthropicProvider # Claude /v1/messages with system prompt extraction
│   ├── OpenAIProvider    # Standard /v1/chat/completions
│   ├── OllamaProvider    # Local inference, free
│   ├── retry_with_backoff()  # Exponential backoff + jitter
│   └── check_context_window() # 85% threshold truncation
├── graph.py              # LangGraph StateGraph topology
│   ├── AgentState        # TypedDict state schema
│   ├── planning_node()   # LLM: generate implementation blueprint
│   ├── patching_node()   # LLM: generate SEARCH/REPLACE patches
│   ├── compiler_node()   # Deterministic: run build in sandbox
│   ├── repair_node()     # LLM: analyze errors, fix, escalate to fallback model
│   ├── human_intervention_node() # Set HITL flags
│   ├── requirements_discovery_node() # LLM: 8-sector requirements discovery
│   ├── architecture_discovery_node() # LLM: 8-sector architecture discovery
│   ├── deployment_discovery_node()   # LLM: 4-sector deployment discovery
│   ├── write_spec_node() # Serialize discovery to .md files
│   ├── generate_deployment_spec_node() # Produce DEPLOYMENT_BLUEPRINT.md
│   ├── route_after_compiler()    # Conditional: repair / HITL / security_scan
│   ├── route_after_discovery()   # Conditional: write_spec / discovery loop
│   ├── route_after_gatekeeper()  # Conditional: next phase / refinement loop
│   ├── route_after_security_scan() # Conditional: patch / HITL / deployment
│   ├── route_after_hitl()        # Conditional: compiler / END
│   ├── apply_memory_cleanse()    # Compress verbose repair messages
│   ├── build_graph()             # Assemble full StateGraph
│   └── run_graph()               # Async entry point
├── sandbox.py            # Sandbox execution engine
│   ├── SandboxBackend    # ABC for isolation backends
│   ├── UnshareBackend    # Linux namespace isolation
│   ├── DockerBackend     # Docker container isolation
│   ├── BareBackend       # No isolation (fallback)
│   ├── SandboxExecutor   # Orchestrator
│   ├── DiskLogStreamer   # Temp-file buffered log I/O
│   ├── MemoryLogStreamer # In-memory log accumulator
│   ├── filter_critical_errors()  # Regex log interceptor
│   ├── _execute_subprocess_with_timeout() # PGID-managed subprocess
│   └── extract_diagnostics()     # Multi-language diagnostic parser
├── patcher.py            # Hybrid file modification engine
│   ├── PatchBlock        # Parsed patch instruction
│   ├── PatchResult       # Operation result
│   ├── TextPatcher       # Exact-match SEARCH/REPLACE
│   ├── TreeSitterPatcher # AST-aware rewriting
│   ├── HybridPatcher     # Auto-selects best strategy
│   ├── parse_patch_blocks() # Extract blocks from LLM text
│   └── process_llm_patch_output() # Primary integration point
├── security.py           # Lifecycle & security
│   ├── GitGuardian       # Branch creation, commit, rollback
│   ├── CommandValidator  # Whitelist/blocklist command scanner
│   ├── HITLGate          # Pre-execution sensitive operation confirmation
│   └── security_scan_node() # SAST + secret scanning gatekeeper
├── storage.py            # Checkpoint persistence
│   ├── AsyncSqliteSaver  # Disk-backed LangGraph checkpointer
│   ├── CheckpointSummary # Read-only state snapshot
│   ├── generate_session_id()
│   ├── inspect_session() # Read-only status inspector
│   └── list_all_sessions()
├── deploy.py             # Containerization & deployment
│   ├── scan_workspace_telemetry() # Deterministic workspace scanner
│   ├── synthesize_architecture()  # LLM: JSON blueprint → compose
│   ├── generate_assets_from_blueprint() # Dockerfile, compose, Caddyfile
│   ├── health_check_loop() # docker inspect polling
│   └── deployment_node() # Phase orchestrator
├── lintgate.py           # Deterministic format verification
│   ├── FormatterSpec     # Tool command spec
│   ├── lintgate_node()   # Pre-build format + lint runner
│   └── _resolve_path()   # Workspace-relative path resolution
├── redactor.py           # Zero-knowledge secret scanner
│   ├── SecretScanner     # Regex + entropy-based detection
│   ├── RedactionResult   # Replacement stats
│   ├── redact_text()     # String redaction
│   └── redact_messages() # Message list redaction
├── speculative.py        # Multi-variant compilation
│   ├── VariantResult     # Per-variant compilation result
│   ├── SpeculativeResult # Aggregate speculation result
│   ├── speculate_node()  # N-variant parallel compilation
│   └── _select_winner()  # first_success / fewest_changes / all_pass
├── impact.py             # Semantic dependency graph
│   ├── DependencyGraph   # Cross-file dependency scanner
│   ├── ImpactAnalyzer    # Pre-patch impact checker
│   └── ImpactResult      # Warning + impacted files
├── skills.py             # Unified skill registry
│   ├── SkillBase         # ABC for all skill types
│   ├── ToolSkill         # LLM-invokable function
│   ├── PipelineSkill     # LangGraph node wrapper
│   ├── SubAgentSkill     # Autonomous mini-agent
│   ├── DocGenSkill       # Documentation sub-agent
│   └── SkillRegistry     # Global singleton
│   # NOTE: stack-aware skill filtering (FR-027) lives in
│   # graph.py:_parse_skill_frontmatter() — it reads the
│   # `applies_to:` YAML frontmatter on harness/skills/*.md
│   # files and intersects against the workspace tag set
│   # before loading skills into the prompt.
├── hitl.py               # Pluggable HITL transport (FR-009)
│   ├── HitlChannel       # ABC: prompt / notes / confirm / wait_for_manual_edit
│   ├── StdinChannel      # Default — interactive terminal
│   ├── FileChannel       # Read prompts/answers from JSONL files
│   ├── HttpChannel       # POST prompts to a webhook; receive answers as JSON
│   ├── get_channel / set_channel / reset_channel  # Process-wide singleton
│   └── _auto_approve()   # CI / HARNESS_AUTO_APPROVE / non-TTY auto-approve
├── observability.py      # Structured logging + JSONL session events
│   ├── JSONFormatter     # One JSON object per log line, with `extra=` merge
│   ├── configure_logging()  # Stderr + per-session JSONL + optional LangSmith
│   ├── emit_event()      # INFO-level structured event (successful / observational)
│   └── log_failure()     # ERROR-level structured failure event (FR-029)
│                         # Catalogue: sandbox_start_failed, token_budget_exhausted,
│                         #            hitl_gate_blocked.
├── trust.py              # Workspace boundary enforcement + structured-output trust
│   ├── safe_resolve()           # Block path traversal outside workspace_root
│   ├── is_path_allowed()
│   ├── is_valid_docker_image / service_name / env_var_name / port_mapping
│   ├── validate_blueprint()     # Deploy blueprint schema check
│   ├── validate_discovery_json()# Discovery-LLM output trust gate
│   ├── validate_blueprint_json()# Deploy-LLM output trust gate
│   ├── validate_synthesized_spec()  # Manifest-synthesis trust gate (Bug 7 closure)
│   └── safe_subprocess_env()    # Scrub envrionment passed to sandbox subprocess
└── parser_registry.py    # Diagnostic parser plugins (FR-026)
    ├── RustParser        # --error-format=json
    ├── GccClangParser    # -fdiagnostics-format=json
    ├── GoParser          # file:line:col: message
    ├── PythonParser      # Traceback extraction
    ├── JavaParser        # javac / maven / gradle diagnostic shapes
    ├── TypeScriptParser  # tsc / eslint
    ├── DartParser        # dart analyze / flutter build
    ├── GenericParser     # file:line:col: severity: message
    ├── register_parser / register_extension_parser
    ├── get_parser / get_parser_for_extension / list_registered_parsers
    └── detect_and_parse() # Auto-detect + parse
```

### 3.2 Data Flow

```
1. User prompt + workspace → CLI
                              │
                              ▼
2. Config discovery (+ models, routing, budget, sandbox)
                              │
                              ▼
3. GitGuardian: stash dirty, create patch branch
                              │
                              ▼
4. SecretScanner: register global redactor
                              │
                              ▼
5. Gateway: register models from config, create Gateway instance
                              │
                              ▼
6. run_graph() → create_initial_state()
                              │
                              ▼
        ┌─────────────────────────────────────────────────┐
        │         EXHAUSTIVE DISCOVERY PIPELINE           │
        │                                                 │
        │  requirements_discovery_node                    │
        │         │                                       │
        │         ▼                                       │
        │  discovery_interview_loop (CLI stdin)           │
        │         │                                       │
        │         ▼                                       │
        │  route_after_discovery → write_spec_node        │
        │         │                                       │
        │         ▼                                       │
        │  human_gatekeeper_node (approve/refine/manual)  │
        │         │                                       │
        │         ▼ (approve)                             │
        │  architecture_discovery_node                    │
        │         │                                       │
        │    [same loop as above]                         │
        │         │                                       │
        │         ▼ (approve gatekeeper)                  │
        └─────────┼───────────────────────────────────────┘
                  │
                  ▼
7. planning_node → LLM (planning_primary, thinking mode)
                  │
                  ▼
8. patching_node → LLM (patching_primary, non-thinking)
                  │
                  ▼
9. speculate_node → N LLM calls (temp>0) → parallel worktrees → select winner
                  │
                  ▼
10. lintgate_node → ruff/gofmt/prettier/rustfmt on modified files
                  │
                  ▼
11. compiler_node → SandboxExecutor → backend.run(build_command)
                  │
           ┌──────┴──────┐
           │ exit 0       │ exit ≠ 0
           ▼              ▼
12. security_scan_node   13. repair_node → LLM (repair_primary, thinking)
    │                          │
    │ clean? ──yes──▶          ├── lintgate_node
    │                          │
    │ findings?                ├── compiler_node (re-verify)
    │   │                      │
    │   ▼                      │  repairs < 3 → loop to repair_node
    │ patching_node            │  repairs >= 3 → human_intervention_node
    │   │                      │                 │
    │   ▼                      │                 ├── [hint] → repair_node
    │ lintgate → compiler      │                 ├── [manual] → compiler_node
    │                          │                 ├── [resume] → compiler_node
    │                          │                 └── [abandon] → END
    │                          │
    ▼                          │
14. Flutter detected? ─yes─▶ [END]  (FR-028 — mobile builds bypass docker compose)
    │ no                       │
    ▼                          │
15. deployment_discovery_node │
    │                          │
    ▼                          │
16. generate_deployment_spec  │
    │                          │
    ▼                          │
17. human_gatekeeper (DEPLOY) │
    │ (approve)                │
    ▼                          │
18. deployment_node            │
    ├── scan_workspace_telemetry()
    ├── synthesize_architecture()
    ├── generate_assets_from_blueprint()
    ├── docker compose up --build -d   # V2 syntax, no hyphen
    └── health_check_loop()
        │
        ▼
      [END]

Independent of the graph, `harness doctor` reuses the same config-
discovery + checkpoint-DB code paths to run five healthchecks (git
repo, API keys per routed provider, sandbox backend reachable,
checkpoint DB writable, config parses cleanly) and reports
PASS/WARN/FAIL with colored markers.
```

### 3.3 State Mutation per Node

```
AgentState fields and which nodes write to them:

┌──────────────────────────┬──────────────────────────────────────────────┐
│ Field                    │ Written By                                   │
├──────────────────────────┼──────────────────────────────────────────────┤
│ messages                 │ planning_node, patching_node, repair_node,    │
│                          │   lintgate_node, security_scan_node,          │
│                          │   deployment_node, discovery_nodes            │
│ modified_files           │ patching_node, repair_node,                   │
│                          │   process_llm_patch_output()                  │
│ compiler_errors          │ compiler_node, security_scan_node,            │
│                          │   deployment_node                             │
│ token_tracker            │ planning_node, patching_node, repair_node     │
│ loop_counter             │ ALL nodes (increment their counter)           │
│ budget_remaining_usd     │ planning_node, patching_node, repair_node     │
│ exit_code                │ compiler_node                                 │
│ node_state               │ ALL nodes (metadata + routing signals)        │
│ current_gate             │ requirements_discovery, architecture_discovery│
│                          │   deployment_discovery, generate_deployment   │
│ spec_requirements_path   │ write_spec_node                               │
│ spec_architecture_path   │ write_spec_node                               │
│ deployment_blueprint_path│ generate_deployment_spec_node                 │
└──────────────────────────┴──────────────────────────────────────────────┘
```

---

## 4. Technology Stack

| Layer | Technology | Justification |
|-------|-----------|---------------|
| **Orchestration** | LangGraph ≥ 0.4.0 | Stateful graph execution with checkpointing; typed state schema |
| **Language** | Python 3.11+ (CI: 3.11 / 3.12 / 3.13) | TypedDict, asyncio improvements, `None`-aware operators |
| **Persistence** | aiosqlite + WAL mode | Crash-safe, zero-config, survives reboots; WAL for concurrent reads |
| **File I/O** | aiofiles ≥ 24.0 | Non-blocking disk ops with sync fallback for missing dep |
| **AST Parsing** | tree-sitter + tree-sitter-language-pack ≥ 1.8 | Single wheel covering 165+ grammars (Python / Java / JS / TS / TSX / Dart / Rust / Go / Swift / …); replaces six individual grammar packages and gives us Dart coverage that has no standalone PyPI distribution |
| **HTTP Client** | httpx ≥ 0.28 | Async HTTP/2 with connection pooling and timeout management |
| **Config** | JSON (discovered hierarchically) | Workspace `.harness_config.json` → `~/.harness/config.json` → `cli.json` |
| **Testing** | pytest + pytest-asyncio | Async test support, fixture injection, coverage |
| **CI** | GitHub Actions matrix | `pytest tests/ -q --tb=short` on push to `main` and PRs across Python 3.11 / 3.12 / 3.13 |
| **Pre-commit** | pre-commit + local pytest hook | Same suite runs locally as in CI; bypassable with `--no-verify` for emergencies only |
| **Linting** | ruff ≥ 0.8 | Fast Python linter + formatter |
| **Type Checking** | mypy ≥ 1.13 (strict mode) | TypedDict validation |
| **Sandbox (primary)** | Docker CLI | Strongest isolation, built-in resource limits; preferred by `backend: "auto"` |
| **Sandbox (fallback)** | Linux unshare(2) | Kernel namespace isolation without Docker dependency |
| **Sandbox (opt-in)** | bare subprocess | Zero isolation; opt-in via `HARNESS_ALLOW_UNSAFE_SANDBOX=true` for environments where neither Docker nor user-namespaces are available |
| **Secrets** | SHA-256 hashing | Stable hash for traceability without exposing values |
| **Release** | `make release` + `scripts/release.py` | SemVer bump → CHANGELOG roll → tag → push; refuses dirty trees and off-`main` runs |

**Dependency Versions (pyproject.toml):**
```
# runtime
langgraph>=0.4.0
langgraph-checkpoint-sqlite>=2.0.0
aiofiles>=24.0.0
tree-sitter>=0.23.0
tree-sitter-language-pack>=1.8.0
httpx>=0.28.0
uuid7>=0.1.0
typing-extensions>=4.12.0

# dev (extras = "dev")
pytest>=8.0.0
pytest-asyncio>=0.24.0
ruff>=0.8.0
mypy>=1.13.0
pre-commit>=3.7.0
msgpack>=1.0.0          # storage GC regression test; runtime falls back to JSON if missing
```

---

## 5. Key Design Decisions

### 5.1 Hybrid Patcher: AST-Aware with Text Fallback

**Decision**: Use tree-sitter for AST-level structural patching on supported languages, with exact-match text SEARCH/REPLACE as universal fallback.

**Rationale**: Pure text-based SEARCH/REPLACE fails on whitespace/indentation drift between LLM-generated patches and actual files. AST-aware patching locates nodes by structural signature and replaces only the node's bytes, preserving all surrounding code. The text fallback ensures the system works on any file type without tree-sitter grammars installed.

**Trade-off**: Tree-sitter adds a native dependency. The fallback to TextPatcher is automatic and transparent, so the system degrades gracefully.

### 5.2 Disk-Buffered Log Streaming

**Decision**: Stream build output to NamedTemporaryFiles on disk rather than accumulating in memory.

**Rationale**: Large builds (C++, Rust) can produce gigabytes of output. Disk-buffered mode keeps RAM usage constant. The `DiskLogStreamer` enforces a 500MB max size limit, writes stdout/stderr to separate temp files, and reads back via line-by-line iteration. Temp files are auto-cleaned after execution.

**Trade-off**: Slightly higher latency for small builds due to disk I/O. In-memory `MemoryLogStreamer` is available as an alternative via `log_buffer_mode: "memory"`.

### 5.3 Cross-Model Speculative Repair Escalation

**Decision**: Repair attempts 1-2 use the cheap primary model; repair attempt 3 escalates to the expensive fallback model with thinking mode.

**Rationale**: Most compilation errors are simple (missing import, wrong type) and the cheap model can fix them. Only the hardest problems warrant the reasoning model's higher cost. This saves 60-80% of repair costs vs always using the expensive model.

**Trade-off**: Adds complexity to `repair_node` with temporary config mutation + restore in a `finally` block.

### 5.4 Exhaustive Zero-Unknowns Discovery

**Decision**: Before any code is generated, the planning LLM cross-examines the developer across 8 structured sectors (requirements) + 8 technical sectors (architecture) + 4 deployment sectors, each with follow-up loops and critical/unknown tracking.

**Rationale**: LLMs produce better code when given exhaustive context. The multi-phase discovery eliminates ambiguous requirements before patches are generated, reducing downstream repair loops.

**Trade-off**: Adds significant pre-generation latency and LLM token cost. Discovery is **off by default**; opt in with `--discover` on greenfield projects or when working from a blank workspace. The legacy `--skip-discovery` flag remains as a hidden no-op alias for scripts.

### 5.5 Secret Redaction Before Every API Call

**Decision**: All outbound LLM messages pass through `SecretScanner.redact_messages()` before transmission. The redactor uses 15+ high-confidence regex patterns plus entropy analysis for unknown token formats.

**Rationale**: Developers may accidentally include API keys, tokens, or credentials in their prompts or code context. The redactor acts as a safety net, preventing secrets from ever leaving the local machine.

**Trade-off**: Regex-based detection has false negatives (custom secret formats) and false positives (long random strings). The entropy-based fallback mitigates unknowns; the hash mode (`[REDACTED:sha256:xxxxxxxx]`) allows tracing without exposure.

### 5.6 Hierarchical Config with Deep Merge

**Decision**: Configuration is loaded in priority order: workspace `.harness_config.json` → `~/.harness/config.json` → shipped `cli.json` fallback. Nested dicts are deep-merged rather than replaced.

**Rationale**: Different projects need different models, budgets, and sandbox configs. Deep merge allows overriding a single nested key (e.g., `token_budget.hard_cap_usd`) without re-declaring the entire section.

### 5.7 GitGuardian: Isolated Patch Branches

**Decision**: Every harness session creates an `agent/patch-{session_id[:8]}` branch off the current HEAD. On success, changes are committed and the original branch restored. On failure, the patch branch is deleted with checkout rollback.

**Rationale**: The harness must never corrupt the developer's working state. Stashing pre-existing changes + isolated branches + automatic rollback provides defense-in-depth against accidental destruction.

### 5.8 TypedDict-Only State Schema

**Decision**: `AgentState` is a `TypedDict` (for LangGraph compatibility). An earlier version of this codebase also defined a parallel `AgentStatePydantic(BaseModel)` and companion `TokenTrackerPydantic` / `DiagnosticObjectPydantic` / `MessagePydantic` classes. These were removed because they were never imported anywhere outside their own definition block — they added no runtime validation, imposed an optional `pydantic` dependency, and their claimed "dual schema" was fictional.

**Rationale**: LangGraph's `StateGraph` requires a TypedDict schema. State factories (`create_initial_state`) already provide safe defaults; Pydantic's per-field validation would add per-call overhead without catching bugs that TypedDict's structural contract (plus existing regression tests) doesn't already catch. The Pydantic option remains available as a future addition if a clear use case emerges.

### 5.9 Docker-First Sandbox Selection

**Decision**: Auto-detection now prioritizes Docker over unshare: `docker → unshare → bare`. Previously it was `unshare → docker → bare`.

**Rationale**: Docker provides stronger isolation boundaries (containers vs namespaces) with built-in resource limits (memory, CPU, PID caps). The `unshare` backend is still available as a faster fallback when Docker is unreachable. User-specified backends (`"unshare"`, `"docker"`, `"bare"`) bypass auto-detection entirely.

**Trade-off**: Slightly higher startup latency on container cold-start (~1-2s). The unshare backend remains the faster option on systems where Docker is unavailable or unnecessary.

### 5.10 Gateway Typo Resilience

**Decision**: When `_validate_routing_keys()` detects unregistered model names in config (likely typos), it no longer raises a blocking `ValueError`. Instead, it logs the error and auto-falls back to the configured `ollama_local_model` if available.

**Rationale**: Stopping execution for a typo in `.harness_config.json` is unnecessarily disruptive. Ollama is configured as a zero-cost fallback — using it keeps the graph alive while alerting the developer to fix their config.

### 5.11 Repair Prompt Fallback Triad

**Decision**: The repair node now composes its prompt from three sources in priority order: (1) structured compiler diagnostics, (2) lintgate errors, (3) raw build output tail (last 2000 chars). If no structured diagnostics exist, the raw build output is appended.

**Rationale**: Many build tools produce output that doesn't match any structured parser (Makefiles, shell scripts, custom build systems). The raw output fallback ensures the LLM always has context to generate a fix, even when diagnostic parsing produces zero results.

### 5.12 Strict Format Reminders for Code Generation

**Decision**: Both `patching_node` and `repair_node` inject a `[CRITICAL FORMAT INSTRUCTION]` message immediately before the LLM dispatch call. This message shows exact patch block templates and forbids markdown, explanations, or text outside blocks.

**Rationale**: Smaller/faster models (used as `patching_primary`) often ignore the system prompt's format instructions when they're buried in a long initial prompt. A short, forceful reminder immediately before the call dramatically increases patch block compliance.

### 5.13 Code Quality Standards in Prompts

**Decision**: The system prompt (`messages[0]`) now includes a **Code Quality Standards** section (modularity, error handling, type hints, edge cases, production-ready code). Both format reminders include a one-line quality directive.

**Rationale**: Autonomous code generation without quality guardrails produces fragile, throwaway code. Embedding quality expectations in every LLM call ensures generated code is modular, well-typed, and production-ready.

### 5.14 HITL Raw Build Output Display

**Decision**: When the HITL menu shows "No compiler errors captured" but `node_state.last_build_output` exists, it now displays the raw build output (last 2000 chars) instead of leaving the developer blind.

**Rationale**: The developer needs to see what actually failed before choosing an action ([e] hint, [m] manual fix, [r] retry). Previously, with zero structured diagnostics, the HITL screen gave no actionable information.

### 5.15 First-Run Healthcheck (`harness doctor`)

**Decision**: A dedicated `harness doctor` subcommand surfaces the five environment preconditions that previously turned into silent first-run failures: git repo presence, API keys per routed provider, sandbox backend reachability, checkpoint DB writability, and config parse cleanliness. Each check returns one of PASS / WARN / FAIL with a colored marker; the command exits non-zero on any FAIL.

**Rationale**: Before doctor, users debugging a broken install had to read error messages buried in `harness run` logs. Surfacing the preconditions explicitly turns "why didn't anything happen?" into "your `OPENAI_API_KEY` is missing." Each check is also a smoke test for the underlying config path, so doctor doubles as a sanity check after editing `.harness_config.json`.

**Trade-off**: Adds five subprocess + filesystem probes (~50ms each, parallel where possible) per invocation. Acceptable for a deliberate operator command.

### 5.16 Pluggable HITL Transport

**Decision**: The HITL menu is rendered through an `HitlChannel` interface with three built-in implementations: `StdinChannel` (default — interactive terminal), `FileChannel` (read prompts/answers from JSONL files; useful for replay and tests), `HttpChannel` (POST prompt → receive JSON answer; useful for remote operators / web dashboards).

**Rationale**: The original implementation hard-coded `input()` calls inside the gatekeeper nodes, which made every HITL site uniquely difficult to test. Routing through an ABC let us write deterministic tests against `FileChannel` and unblocked the still-deferred web dashboard (T4.1) without committing to it.

**Trade-off**: One extra indirection per prompt. The channel is a process-wide singleton, so non-CI tests reset it via `reset_channel()`.

### 5.17 Multi-Stack Coverage via `tree-sitter-language-pack`

**Decision**: Replace six individual `tree-sitter-*` grammar packages with the single `tree-sitter-language-pack` wheel, which bundles 165+ grammars including Python, Java, JS/TS/TSX, Dart, Rust, Go, and Swift. Patcher, impact analyzer, and the new `JavaParser` / `TypeScriptParser` / `DartParser` all read from the same registry.

**Rationale**: Six grammar packages meant six upgrade cadences, six release-note streams, and one of them (Dart) had no standalone PyPI distribution at all. Consolidating to one wheel buys us Dart coverage and amortizes the grammar churn into a single dependency line. Adding a new language is now "register a parser" instead of "add a new dependency."

**Trade-off**: Slightly larger install footprint (~15 MB of bundled grammars). The footprint is paid once at install time, not per-run.

### 5.18 Stack-Aware Skill Filtering

**Decision**: Skill files in `harness/skills/` may declare an `applies_to: [tag1, tag2]` YAML frontmatter (parsed by `graph.py:_parse_skill_frontmatter`). At graph assembly, the workspace is fingerprinted to a tag set (`python`, `flutter`, `spring`, `react`, …); skill files whose `applies_to` doesn't intersect the workspace tags are excluded from the LLM prompt. Files without frontmatter always load (universal skills).

**Rationale**: A user working on a Flutter app should not see a 4000-character Django Channels skill in their prompt. Filtering at the frontmatter level keeps the prompt budget small without forcing the harness to "guess" relevance from filename pattern matching.

**Trade-off**: Skill authors have to remember to add the frontmatter — but the failure mode is permissive (no frontmatter → always load), so the worst case is a too-large prompt, not a missing skill.

### 5.19 Flutter / Mobile Routing Short-Circuit

**Decision**: On a clean security scan, if the workspace looks like a Flutter project (`pubspec.yaml` with `flutter:` SDK dep, detected by `impact._is_flutter_project`), the graph routes directly to END instead of through the docker compose deploy pipeline.

**Rationale**: Flutter's artifact is a mobile binary (APK / AAB / IPA / web bundle), not a docker compose service stack. Running the deploy pipeline on a Flutter project would produce an unrunnable Dockerfile and waste budget on a synthesize-architecture LLM call. Short-circuiting matches the user's mental model — "build and stop."

**Trade-off**: Flutter projects don't get the deploy-blueprint HITL gate. That's correct for v1.x; if users ask for cloud-build wiring we can add a `flutter:` deploy backend.

### 5.20 Structured Failure-Event Catalogue

**Decision**: Failure sites emit structured events via `harness.observability.log_failure(name, **fields)` — an ERROR-level mirror of the existing `emit_event` helper. Each event carries a snake_case `event` field, so failures are grep-able from the per-session JSONL log by event name instead of by string fragment. The initial catalogue: `sandbox_start_failed`, `token_budget_exhausted`, `hitl_gate_blocked`.

**Rationale**: Logging was already comprehensive but inconsistent — every module invented its own `logger.error("...")` format, so an operator scanning a failure across modules had to grep multiple substrings. A named event catalogue makes the failure modes a first-class queryable shape: `jq 'select(.event == "token_budget_exhausted")'`.

**Trade-off**: New failure sites need a name. The `log_failure` docstring lists naming conventions (`_failed`, `_exhausted`, `_blocked`) and the canonical catalogue, so authors can extend it without inventing new patterns.

---

## 6. Data Model Overview

### 6.1 AgentState (Primary State Object)

```
AgentState
├── workspace_path: str              # Absolute path to target repo
├── messages: list[MessageDict]      # Conversation history
│   ├── role: "system"|"user"|"assistant"|"tool"
│   ├── content: str
│   ├── name: Optional[str]
│   ├── tool_calls: Optional[list]
│   └── tool_call_id: Optional[str]
├── modified_files: list[str]        # Paths edited this session
├── compiler_errors: list[DiagnosticObjectDict]
│   ├── file: str
│   ├── line: int
│   ├── column: int
│   ├── severity: "error"|"warning"
│   ├── error_code: str
│   ├── message: str
│   └── semantic_context: str
├── token_tracker: TokenTrackerDict
│   ├── total_input_tokens: int
│   ├── total_output_tokens: int
│   ├── total_cached_tokens: int
│   ├── total_cost_usd: float
│   └── per_model: dict[str, dict]   # Per-model breakdown
├── loop_counter: dict[str, int]     # {patching, repair, compiler, total_repairs, security, deployment}
├── allow_network: bool
├── build_command: str               # e.g., "make build"
├── budget_remaining_usd: float
├── session_id: str                  # UUIDv4 or user-provided
├── exit_code: int                   # Last compiler exit code
├── node_state: dict[str, Any]       # Node-specific metadata
├── current_gate: str                # "REQUIREMENTS"|"ARCHITECTURE"|"DEPLOYMENT"|""
├── spec_requirements_path: str
├── spec_architecture_path: str
└── deployment_blueprint_path: str
```

### 6.2 Checkpoint Schema (SQLite)

```
Table: checkpoints
├── thread_id: TEXT (PK composite)
├── checkpoint_ns: TEXT (PK composite)
├── checkpoint_id: TEXT (PK composite)
├── parent_checkpoint_id: TEXT
├── type: TEXT
├── checkpoint: BLOB (JSON serialized state)
├── metadata: BLOB (JSON)
├── created_at: TEXT
└── updated_at: TEXT

Table: writes
├── thread_id, checkpoint_ns, checkpoint_id,
│   task_id, idx (PK composite)
├── channel: TEXT
├── type: TEXT
├── value: BLOB
└── created_at: TEXT

Table: blobs
├── thread_id, checkpoint_ns, channel,
│   version (PK composite)
├── type: TEXT
├── blob: BLOB
└── created_at: TEXT
```

### 6.3 Model Registry

```
_MODEL_REGISTRY: dict[str, ModelSpec]
├── key: "provider:model_id" (e.g., "openai:gpt-4o")
└── ModelSpec
    ├── provider: "deepseek"|"anthropic"|"openai"|"ollama"
    ├── model_id: str
    ├── context_window: int
    ├── input_cost_per_1m: float
    ├── output_cost_per_1m: float
    ├── cached_input_cost_per_1m: float
    ├── api_base_url: str
    ├── supports_thinking: bool
    └── supports_cache: bool
```

---

## 7. Integration Points

### 7.1 Gateway ↔ Providers
- **Protocol**: HTTPS REST (httpx AsyncClient)
- **DeepSeek**: POST `{base_url}/chat/completions` (OpenAI-compatible JSON)
- **Anthropic**: POST `{base_url}/messages` with `x-api-key` header, system prompt extracted to top-level field
- **OpenAI**: POST `{base_url}/chat/completions`
- **Ollama**: POST `{base_url}/chat/completions` (no auth, localhost)

### 7.2 Sandbox ↔ Build Tools
- **Protocol**: asyncio subprocess with PGID management
- **Unshare**: `unshare --mount --pid --fork --mount-proc [--net] -- sh -c "<command>"`
- **Docker**: `docker run --rm --read-only --tmpfs /tmp:exec --memory=... --network=none|bridge -v ...`
- **Bare**: `sh -c "cd <workspace> && <command>"`

### 7.3 Persistence ↔ LangGraph
- **Protocol**: `AsyncSqliteSaver` implementing LangGraph's `BaseCheckpointSaver` interface
- **Methods**: `put(config, checkpoint, metadata, new_versions)` → `get(config)` → `list(config, limit, before)`
- **Journal**: WAL mode for concurrent read/write safety

### 7.4 File I/O
- **Primary**: aiofiles (async) for all patcher operations
- **Fallback**: sync `open()` when aiofiles is not installed
- **Temp Files**: `tempfile.NamedTemporaryFile` for sandbox log buffering

### 7.5 External Tools (Optional, Runtime-Detected)
- **gitleaks**: Secret scanning (`detect --no-git --report-format json`)
- **bandit**: Python SAST (`-r -f json -ll -q`)
- **semgrep**: Universal SAST (`scan --config=auto --json --quiet`)
- **ruff**: Python formatting (`format --quiet`) and linting (`check --fix --quiet`)
- **gofmt**: Go formatting (`-w`)
- **prettier**: JS / TS / TSX / JSX / CSS / HTML / JSON / YAML / Markdown formatting (`--write`)
- **rustfmt** + **clippy**: Rust formatting + lint
- **clang-format**: C / C++ formatting (`-i`)
- **google-java-format**: Java formatting
- **dart format**: Dart formatting (Flutter / Dart projects)
- **shfmt**: shell-script formatting
- **sqlfluff**: SQL linting + formatting
- **docker compose** (V2 — no hyphen): Container orchestration (`up --build -d`, `down`). The legacy `docker-compose` V1 binary is no longer probed.

---

## 8. Deployment & Environment

### 8.1 Runtime Requirements
- Python 3.11+ (3.11 / 3.12 / 3.13 covered by CI)
- Linux is the only platform actively tested. macOS and Windows + WSL2 are
  best-effort via the Docker backend — see the platform matrix in `README.md`.
- Git 2.x+ (for branch lifecycle management)
- Sandbox: Docker daemon (preferred), OR Linux user-namespace support
  (`unshare --user`), OR opt-in bare via `HARNESS_ALLOW_UNSAFE_SANDBOX=true`.
- tree-sitter grammars ship in-tree via `tree-sitter-language-pack`; no
  per-language install needed.

### 8.2 Configuration Files
| File | Location | Purpose |
|------|----------|---------|
| `cli.json` | Shipped with package | Absolute fallback defaults |
| `~/.harness/config.json` | User home | Global default models and settings |
| `.harness_config.json` | Workspace root | Per-project override (highest priority) |

### 8.3 Environment Variables
| Variable | Purpose |
|----------|---------|
| `DEEPSEEK_API_KEY` | DeepSeek API authentication |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) API authentication |
| `OPENAI_API_KEY` | OpenAI API authentication |
| `CI` | Detect CI environment (auto-approve HITL gate behavior) |
| `HARNESS_AUTO_APPROVE` | Force auto-approve for non-interactive runs |
| `HARNESS_ALLOW_UNSAFE_SANDBOX` | Opt in to the `bare` (zero-isolation) sandbox backend when neither Docker nor `unshare` is available. Never set this outside a disposable VM. |
| `NO_COLOR` | Suppress ANSI colour markers in `harness doctor` output. |
| `LANGCHAIN_API_KEY` | Required when `logging.langsmith=true` to forward traces to LangSmith. |
| `LANGCHAIN_TRACING_V2`, `LANGSMITH_PROJECT` | Additional LangSmith trace routing knobs honoured by `configure_logging`. |

### 8.4 Generated Files (during execution)
- `docs/SPEC_REQUIREMENTS.md` — Requirements specification
- `docs/SPEC_ARCHITECTURE.md` — Architecture specification
- `docs/DEPLOYMENT_BLUEPRINT.md` — Container deployment blueprint
- `Dockerfile` / `Dockerfile.<service>` — Per-service container images
- `docker-compose.yml` — Multi-service orchestration
- `Caddyfile` — Reverse proxy routing rules
- `~/.harness/checkpoints.db` — Session checkpoint database
- `/tmp/.harness/` — Temporary sandbox build logs (auto-cleaned)
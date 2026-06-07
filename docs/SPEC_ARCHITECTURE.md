# AI Agent Harness — Architecture Specification

*Auto-generated from exhaustive codebase analysis.*

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
│                        HARSH CLI PROCESS                           │
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
├── cli.py                # CLI entry, subcommand routing, HITL menus, config discovery
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
└── parser_registry.py    # Diagnostic parser plugins
    ├── RustParser        # --error-format=json
    ├── GccClangParser    # -fdiagnostics-format=json
    ├── GoParser          # file:line:col: message
    ├── PythonParser      # Traceback extraction
    ├── GenericParser     # file:line:col: severity: message
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
14. deployment_discovery_node │
    │                          │
    ▼                          │
15. generate_deployment_spec  │
    │                          │
    ▼                          │
16. human_gatekeeper (DEPLOY) │
    │ (approve)                │
    ▼                          │
17. deployment_node            │
    ├── scan_workspace_telemetry()
    ├── synthesize_architecture()
    ├── generate_assets_from_blueprint()
    ├── docker-compose up --build -d
    └── health_check_loop()
        │
        ▼
      [END]
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
| **Language** | Python 3.11+ | TypedDict, asyncio improvements, `None`-aware operators |
| **Persistence** | aiosqlite + WAL mode | Crash-safe, zero-config, survives reboots; WAL for concurrent reads |
| **File I/O** | aiofiles ≥ 24.0 | Non-blocking disk ops with sync fallback for missing dep |
| **AST Parsing** | tree-sitter + tree-sitter-python | Structural code manipulation; preserves formatting |
| **HTTP Client** | httpx ≥ 0.28 | Async HTTP/2 with connection pooling and timeout management |
| **Config** | JSON (discovered hierarchically) | Workspace `.harness_config.json` → `~/.harness/config.json` → `cli.json` |
| **Testing** | pytest + pytest-asyncio | Async test support, fixture injection, coverage |
| **Linting** | ruff ≥ 0.8 | Fast Python linter + formatter |
| **Type Checking** | mypy ≥ 1.13 (strict mode) | TypedDict validation, Pydantic compatibility |
| **Sandbox** | Linux unshare(2) | Kernel namespace isolation without Docker dependency |
| **Sandbox (alt)** | Docker CLI | Container isolation with CPU/memory/PID limits |
| **Secrets** | SHA-256 hashing | Stable hash for traceability without exposing values |

**Dependency Versions (pyproject.toml):**
```
langgraph>=0.4.0
langgraph-checkpoint-sqlite>=2.0.0
aiofiles>=24.0.0
tree-sitter>=0.23.0
httpx>=0.28.0
pydantic>=2.10.0
uuid7>=0.1.0
typing-extensions>=4.12.0
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

**Trade-off**: Adds significant pre-generation latency and LLM token cost. Bypassable via `--prompt-only` (skips discovery).

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

### 5.8 Pydantic + TypedDict Dual State Schema

**Decision**: AgentState is defined as both a `TypedDict` (for LangGraph compatibility) and a `Pydantic BaseModel` (for runtime validation). The TypedDict is the primary schema; Pydantic is available when installed.

**Rationale**: LangGraph's `StateGraph` requires a TypedDict schema. Pydantic provides validation, default values, and serialization that TypedDict cannot. The dual approach gives both.

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
- **prettier**: JS/TS/CSS/JSON/YAML formatting (`--write`)
- **rustfmt**: Rust formatting (`--edition 2021`)
- **clang-format**: C/C++ formatting (`-i`)
- **docker-compose**: Container orchestration (`up --build -d`, `down`)

---

## 8. Deployment & Environment

### 8.1 Runtime Requirements
- Python 3.11+
- Linux (for unshare sandbox backend) — macOS/Docker works via Docker backend fallback
- Git 2.x+ (for branch lifecycle management)
- Optional: Docker daemon (for Docker sandbox backend)
- Optional: tree-sitter language grammars (for AST-aware patching)

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

### 8.4 Generated Files (during execution)
- `docs/SPEC_REQUIREMENTS.md` — Requirements specification
- `docs/SPEC_ARCHITECTURE.md` — Architecture specification
- `docs/DEPLOYMENT_BLUEPRINT.md` — Container deployment blueprint
- `Dockerfile` / `Dockerfile.<service>` — Per-service container images
- `docker-compose.yml` — Multi-service orchestration
- `Caddyfile` — Reverse proxy routing rules
- `~/.harness/checkpoints.db` — Session checkpoint database
- `/tmp/.harness/` — Temporary sandbox build logs (auto-cleaned)
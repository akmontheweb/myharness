# Changelog

All notable changes to myharness are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html):

- **MAJOR** — backwards-incompatible change to the CLI surface, config
  schema, or checkpoint format.
- **MINOR** — new capability, new subcommand, new config section.
- **PATCH** — bug fix, doc update, CI fix.

## [Unreleased]

### Added
- **Tier 4** — Platform support matrix in `README.md`. Documents which
  platforms (Linux / macOS / WSL2 / Windows) are CI-tested vs
  best-effort vs unsupported, per sandbox backend (`docker` / `unshare` /
  `bare`). T4.1 (web dashboard) is intentionally deferred — the audit
  itself flagged it as out of scope for v1.x.
- **Tier 2** — `CONTRIBUTING.md` covering pre-commit gate behavior, test
  layout, commit-message convention, SemVer policy, and the scope rules
  the project enforces on PRs.
- **Tier 2** — `CHANGELOG.md` (this file) and `make release` target that
  verifies a clean tree, runs tests, bumps the version, tags, and pushes.
- **Tier 2** — `harness.observability.log_failure(name, **fields)` helper
  plus a catalogue of named failure events: `sandbox_start_failed`,
  `token_budget_exhausted`, `hitl_gate_blocked`. Failures can now be
  grepped by event name from JSONL session logs instead of by string
  fragment.
- **Tier 1** — `harness doctor` subcommand: five healthchecks (git repo,
  API keys per routed provider, sandbox backend reachable, checkpoint DB
  writable, config parses cleanly) with green/yellow/red markers; non-zero
  exit on any failure.
- **Tier 1** — GitHub Actions CI workflow running `pytest` on push to
  `main` and PRs, matrix on Python 3.11 / 3.12 / 3.13.
- **Tier 1** — Recursive config typo detection: `_validate_config_keys`
  now walks known nested sections (`sandbox`, `token_budget`,
  `persistence`, `model_routing`, `deployment`, `lintgate`, `logging`,
  `node_throttle`) with fuzzy-match suggestions, so typos like
  `token_budget.hrad_cap_usd` surface as `did you mean 'hard_cap_usd'?`
  instead of silently no-op-ing.
- **Tier 1** — README rewrite: quick-start, command reference with flag
  tables, configuration overview, troubleshooting matrix keyed to
  `harness doctor` output.

### Fixed
- `tests/test_hitl.py` used `Optional[list]` in a function signature
  without importing `Optional`. Python 3.14 evaluates defaults lazily
  (PEP 649), so the `NameError` never fired locally; 3.11–3.13 raised at
  import. Module-level import added.
- `msgpack` pinned as a dev dep. `langgraph-checkpoint-sqlite` switched
  to `ormsgpack`, so the storage GC regression test (which builds a
  msgpack blob directly) no longer pulled it in transitively. Runtime
  path already handles `msgpack` missing.

## [1.0.0] - initial

Initial commit: LangGraph-orchestrated agent harness with sandboxed
builds (Docker / unshare / bare), SQLite checkpoint store with WAL + TTL
GC, model-agnostic gateway (OpenAI / Anthropic / DeepSeek / Ollama),
three-phase HITL gate (Requirements / Architecture / Deployment),
tree-sitter-backed multi-stack parsing (Python / Java / Node / Dart /
Flutter), structured logging with optional LangSmith tracing, and a
545-test regression pack.

Note: v1.0.0 is the version declared in `pyproject.toml` from project
inception; it was not git-tagged. The first formal tagged release will
be the Tier 1 + Tier 2 closeout above.

[Unreleased]: https://github.com/akmontheweb/myharness/commits/main

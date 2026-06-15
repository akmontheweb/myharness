# myharness vs. the market — how we fare & where to invest

## Context

You asked how `myharness` stacks up against the best coding harnesses on the
market (Claude Code et al.) and what to improve. This document is the
assessment, not an implementation plan. Read it as: where we win today, where
we are objectively behind table-stakes, and a ranked improvement backlog.

The honest framing first: `myharness` and Claude Code are **not in the same
category**. Claude Code is an *interactive* coding agent — a REPL with
slash commands, MCP, hooks, subagents, plan mode, and IDE bindings.
`myharness` is an *autonomous batch* harness — one prompt in, a spec-driven
graph that lands compiled code (and optionally a docker-compose deployment)
on the other side. The right peer set is Devin, Replit Agent, SWE-agent,
Cline-in-auto-mode, GPT-Engineer, Bolt.new — autonomous end-to-end builders.
Claude Code is a useful design reference but not a feature-parity target;
trying to be both will produce neither.

---

## Where myharness already wins (preserve these — they are the moat)

These are capabilities the autonomous-agent peer set mostly does **not** have:

- **Provider-agnostic gateway with per-role routing** (`harness/gateway.py`).
  Devin and Replit are model-locked. Claude Code is Anthropic-locked. We can
  route planning to Anthropic, patching to DeepSeek, repair to Ollama —
  matched to cost and latency. This is a durable cost advantage.
- **Three-tier sandboxing** (`harness/sandbox.py`: Docker / `unshare` /
  `bare`). Most peers are either no-sandbox (Aider, Cursor agent) or
  cloud-VM (Devin, Replit). Local-namespace isolation with explicit `bare`
  opt-out is differentiated.
- **Spec-driven flow with HITL gates** (Requirements → Architecture →
  Deployment, `harness/graph.py` + `harness/cli.py` gatekeeper). GPT-Engineer
  and Smol-Developer do single-shot specs; we do three-phase with
  gatekeeper approval and refinement loops. Strong fit for regulated/
  enterprise builds.
- **Checkpoint/resume** via `langgraph-checkpoint-sqlite`. Ctrl-C recovery
  across a multi-hour run is rare in this peer set.
- **Budget enforcement + circuit breaker** (`gateway.py:_preflight_budget_check`,
  `_circuit_is_open`). Pre-flight rejection plus Ollama failover on rate
  limits is mature.
- **Multi-stack tree-sitter patcher + autofix + lintgate**
  (`harness/patcher.py`, `autofix.py`, `lintgate.py`). Python/Java/Node/
  Go/Rust/Dart/Flutter. Cursor/Aider are language-agnostic via raw diffs;
  we are AST-aware in 7+ languages.
- **Trust boundary on LLM output** (`harness/trust.py`). Path traversal
  guards, Docker name validators, env scrubbing, JSON depth caps. Few
  peers have a single audit point for "LLM output → filesystem/container."
- **Impact analysis pre-patch** (`harness/impact.py`). Cross-file symbol
  graph injected as a system message before patching. Effectively a
  poor-person's RAG but local and deterministic. Real differentiator.
- **Speculative patch branching** (`harness/speculative.py`, disabled by
  default). 3-variant fan-out across isolated worktrees. Architecturally
  ahead of the field, but currently a science project (see gap #8 below).
- **Production deployment generation + preview gate** (`harness/deploy.py`,
  `--dev-deployment`). Dockerfile + compose + Caddyfile + blueprint. Most
  peers stop at "code that compiles."
- **Change-request mode** (delta-aware brownfield flow, `cli.py`
  `_archive_consumed_change_requests`). PR-3 work from recent commits.
  Useful for iterating against an existing codebase.
- **Prometheus metrics output** (`harness/metrics.py`). Atomic textfile
  writes for `node_exporter`. Production-grade observability hook, rare
  in this peer set.

## Where myharness is objectively behind (table stakes)

These gaps appear in nearly every comparable peer and are starting to
read as "missing":

1. **No MCP (Model Context Protocol) support.** Largest single gap.
   MCP is now the de-facto interop standard — Claude Code, Cursor,
   Continue.dev, Goose, Cline, and OpenAI's agents all speak it. Adding
   MCP client support unlocks the entire ecosystem of MCP servers
   (databases, browsers, GitHub, search, internal tools) **without
   writing tool integrations**. This is the highest-leverage single
   change available.
2. **No prompt caching.** Anthropic and DeepSeek both support
   `cache_control` blocks now; caching the planning context and the
   tree-sitter symbol index can cut LLM cost 60–90% on long sessions.
   `gateway.py` sends `stream: False` and no cache markers. Doc
   `analysis-deterministic-vs-llm.md` references caching as a goal but
   the gateway does not implement it.
3. **No web search / web fetch tools.** Peer agents routinely fetch
   docs, check Stack Overflow, scrape API references. We have no
   primitive for it. `harness/skills.py:ToolSkill` is the right hook but
   no `WebFetchSkill` / `WebSearchSkill` ship today.
4. **No interactive / iterative mode.** All flows are one-shot batch.
   Mid-run you can answer a HITL prompt but cannot say "actually,
   reconsider — the auth approach is wrong." Either a true REPL or a
   `harness chat` subcommand that reuses the same gateway/sandbox would
   close the gap without re-architecting the graph.
5. **No GitHub integration.** Reading issues, opening PRs, posting
   review comments. Trivial via `gh` CLI or PyGithub; surprising it's
   not there given the change-request mode already implies "task in,
   patch out."
6. **No repo-wide semantic search / embeddings.** `impact.py` is
   AST-only. For "where else in the codebase does this pattern appear"
   we have nothing. A small embeddings index keyed by file SHA, stored
   alongside the SQLite checkpoint, would be a 1–2 day win.
7. **Skills are not user-extensible at runtime.** `harness/skills/` is
   actually 18 markdown *style-guides* (Django, FastAPI, React, Spring
   Boot, etc.) — pre-shipped LLM prompts, not user-extensible. The
   `SkillBase` ABC in `skills.py` requires a code edit + registry
   bootstrap to add a new skill. Claude Code skills load from a
   directory at runtime; matching that pattern would let users ship
   skills as files.
8. **Speculative execution is disabled and effectively unmaintained.**
   `speculative.enabled = false` ships off, and an internal note says
   "3× LLM cost with negative ROI on recent sessions." This is a
   premium feature gated behind premium cost. Either retire it or
   re-target with cheaper variant models (e.g. one DeepSeek + one
   Ollama + one Anthropic) so the cost-of-variance lands closer to 1×.
9. **No persistent memory across sessions.** Checkpoints resume *one*
   thread. There is no per-repo, per-user "what we learned last time."
   A small `~/.harness/memory/<repo-sha>.md` written by the planner at
   end-of-run and re-read at start-of-run would help on iterative
   sessions.
10. **No IDE integration.** No VS Code extension, no LSP. The
    `HttpChannel` HITL transport already lets external tools drive the
    harness — a thin VS Code extension that opens a panel and POSTs to
    that channel would unlock a much bigger user surface.
11. **Test count claim is loose.** README says "545-test regression
    pack"; actual count is 35 test files / ~18,800 LoC of tests.
    Probably accurate at the *test-case* level (parametrize counts),
    but unverified — claim should be tightened to match `pytest
    --collect-only -q | tail -1`.

## Strategic differentiators (next horizon)

If the table-stakes gaps close, these are the bets that could make
`myharness` *the* autonomous harness of choice:

- **Multi-agent fan-out at the graph level.** Today, fan-out is only
  inside `speculative.py` and `SubAgentSkill`. Lifting it into `graph.py`
  — N parallel discovery agents per sector, parallel test generation
  per module, parallel security-fix attempts per finding — would match
  Claude Code's Workflow pattern and beat the rest of the autonomous
  peer set on wall-clock.
- **Multi-repo / monorepo intelligence.** Bazel / Turborepo / Nx
  awareness. None of the peers do this well. Combined with `impact.py`,
  myharness could become the only harness that knows "patching
  `packages/auth` will break `apps/api` and `apps/admin` — let me
  generate tests for both."
- **Cron / scheduled / background runs.** "Run nightly: pull the latest
  Renovate PRs, regenerate failing tests, post results to Slack." A
  daemon mode + RemoteTrigger would make this a backbone tool, not a
  workstation one.
- **Web/cloud dashboard.** Surfaces the Prometheus output, session
  history, cost burn-down, deployment previews. Mostly a UI project
  over data we already emit.
- **Production deployment beyond docker-compose.** Terraform module
  generation, GitHub Actions workflow generation, Kubernetes manifests.
  The deployment node already proves we can generate infra from a
  spec; expand the targets.
- **Cost-aware model auto-tuning.** Track per-role accuracy/cost over
  time; auto-promote cheap models when their pass rate is high enough.
  Gateway already has the metrics hooks.

## Recommended roadmap (ranked by leverage / cost ratio)

These are ordered by where to spend the next $X of engineering. Each is
a self-contained slice; each closes a competitive gap or sharpens an
existing moat.

| # | Initiative | Effort | Leverage | Why |
|---|------------|--------|----------|-----|
| 1 | **MCP client in `gateway.py`** | M | XL | Single change unlocks the entire MCP server ecosystem. Largest gap. |
| 2 | **Prompt caching for Anthropic + DeepSeek** | S | XL | 60–90% LLM cost reduction on long sessions. Pure margin. |
| 3 | **`WebFetchSkill` + `WebSearchSkill`** | S | L | Closes a glaring tool gap. Pattern already exists in `ToolSkill`. |
| 4 | **GitHub integration** (issues→prompt, PR-out, comments) | M | L | Plugs the change-request mode into the place CRs actually live. |
| 5 | **Runtime-extensible skills directory** | S | L | Load `*.py` from `~/.harness/skills/` at startup. 1-day change. |
| 6 | **Repo embeddings + semantic retrieval** alongside `impact.py` | M | L | Cheap RAG, complements the AST graph. |
| 7 | **Persistent per-repo memory file** | S | M | `~/.harness/memory/<repo-sha>.md` read+write hook. |
| 8 | **`harness chat` subcommand** (iterative refinement loop) | M | M | Reuses gateway + sandbox; doesn't require graph rewrite. |
| 9 | **Tighten README claims; add coverage report** | S | S | "545 tests" → verified number + `pytest-cov` artifact. |
| 10 | **VS Code extension over existing `HttpChannel`** | L | M | Bigger user surface; requires sustained maintenance. |
| 11 | **Graph-level multi-agent fan-out** | L | L | Big win, big lift. Best done after MCP + caching land. |
| 12 | **Decide on `speculative.py`** — retire or rebuild with cheap variants | S | M | Currently a maintenance tax with no users. |
| 13 | **Cron / `harness watch` / background daemon** | M | M | Moves myharness from workstation tool to infra tool. |
| 14 | **Web dashboard for sessions + cost + deployments** | L | M | Visualizes the Prometheus output we already emit. |

**Files most affected by tier-1 work (#1–#5):**
`harness/gateway.py` (MCP client, cache markers), `harness/skills.py`
(runtime registry + new tool skills), `harness/cli.py` (skills loader,
new `chat` subcommand, GitHub-aware CR ingest), `harness/graph.py`
(thread new tools into the patcher/planner node prompts), `docs/SPEC_*.md`
+ `README.md` + `CHANGELOG.md` for the schema/CLI surface changes.

## Verification

This is an assessment, not a change set — there is nothing to test
*from this document*. To validate the assessment itself:

1. Read the "Where myharness already wins" list against `harness/` to
   confirm each capability exists where claimed (file paths included).
2. Read the "Where myharness is behind" list against the same — each
   gap is a `grep` away from being verified (e.g. `grep -ri mcp harness/`,
   `grep -ri cache_control harness/`).
3. Once an initiative from the roadmap is picked, exit plan mode and
   open a per-initiative plan with concrete file edits, tests, and an
   acceptance bar.

## Bottom line

`myharness` is genuinely competitive in the autonomous-agent category
on **safety, cost control, provider freedom, multi-stack reach, and
production-deployment generation** — and meaningfully ahead on
`impact.py`, `trust.py`, and the three-phase HITL spec flow. It is
visibly behind on **MCP, prompt caching, web tools, GitHub, and
runtime extensibility** — all of which have become table stakes in
the last 12 months. Closing tier-1 (#1–#5) is roughly two engineer-
weeks and would put us at parity on capabilities while keeping the
existing differentiators intact. Don't try to out-Claude-Code Claude
Code; double down on being the best autonomous harness instead.

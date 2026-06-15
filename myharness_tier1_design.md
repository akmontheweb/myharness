# Tier-1 implementation design — prompt caching, web tools, MCP client

Status: design, awaiting feedback. **No code changes yet.**

This document covers initiatives #1, #2, #3 from
`myharness_improvement_plan.md`. The ordering below is **risk-ascending**,
not the original ranking — I propose we land #2 first (smallest, all
internal), then #3 (new but isolated), then #1 (largest, but additive).

---

## 0. What I confirmed about the current code before designing

These are load-bearing facts. If any are wrong, the design changes.

| Fact | Evidence |
|------|----------|
| `TokenUsage` already carries `cached_tokens` and `cache_creation_tokens` | `gateway.py:72-87` |
| `ModelSpec` already carries `cached_input_cost_per_1m`, `cache_creation_cost_per_1m`, `supports_cache` | `gateway.py:111-130` |
| Anthropic provider **parses** cache-hit usage (`cache_read_input_tokens`, `cache_creation_input_tokens`) | `gateway.py:594-605` |
| Anthropic provider **does NOT send** `cache_control` markers — the request payload has no cache directive today | `gateway.py:546-555` |
| DeepSeek caching is server-side automatic (no client markers needed); usage extraction already works | `gateway.py:467-476` |
| `ensure_prefix_cache_anchor` exists but only logs/hashes — it does not inject cache markers | `gateway.py:837-867` |
| `dispatch()` already calls `ensure_prefix_cache_anchor` on every call | `gateway.py:1632` |
| `LLMResponse.tool_calls` field exists in the dataclass but providers don't populate it (gated behind `use_structured_tools=False`) | `gateway.py:107`, `gateway.py:1158-1168` |
| `SkillRegistry` exists; `ToolSkill` exists; `register_builtin_skills()` exists | `harness/skills.py` |
| `register_builtin_skills()` is **never called from cli.py or graph.py** — only from tests | `grep -rn register_builtin_skills harness/` returns no runtime caller |
| Config validation knows the top-level key `skills` already; `mcp` is not yet recognized | `cli.py:474-496` |
| Pre-shipped `harness/skills/*.md` are **style-guides**, not Python plugin code | `ls harness/skills/` shows 18 `.md` files only |
| Today's loop is text-DSL: LLM emits `<<<CREATE_FILE>>>` / `<<<READ_FILE>>>` / SEARCH/REPLACE blocks; graph node parses + executes; no mid-call tool dispatch | `harness/patcher.py:process_llm_patch_output`, `graph.py` patching node |

**Implication:** the harness already has the *substrate* for caching
(usage tracking, anchor utility, cost columns) and for tools (registry,
schema dataclass) — but the *runtime path* doesn't yet use either.
Initiatives #2 and #3 are mostly about wiring what's already there;
#1 is genuinely new surface.

---

## 1. Initiative #2 — Prompt caching across all currently-supported providers

> Scope refinement (after rechecking `model_prices.json` + provider code):
> the original "Anthropic + DeepSeek" framing understated what's reachable.
> Per-provider state today:
>
> | Provider | Mechanism | Client marker? | Wired today? |
> |----------|-----------|----------------|--------------|
> | **Anthropic** | Explicit `cache_control` blocks; 5-min ephemeral | **Yes** | Cost ✓, **marker emission ✗** (this slice fixes it) |
> | **OpenAI** (gpt-4o, -mini, o1, o3-mini) | Automatic server-side ≥ 1024-token prefix | No | **Fully wired** — extract_usage + compute_cost already deduct cached tokens |
> | **DeepSeek** (chat, reasoner) | Automatic server-side | No | **Fully wired** — same shape |
> | **Ollama** | Implicit local KV-cache | No | N/A (free) — doc-only |
> | Gemini / xAI / Mistral | Mostly automatic | Provider classes don't exist | Out of scope until a provider class is added |
>
> Net: this slice does **two** things, not one — (a) emit Anthropic
> markers, and (b) add a **prefix-stability hasher** that benefits every
> cache-capable provider (OpenAI/DeepSeek auto-caches only fire when the
> prefix is byte-identical across calls). The hasher is ~50 LoC, all
> internal, no provider-specific code.

### 1.1 What "done" looks like

- Anthropic requests for cache-supporting models carry one or more
  `cache_control: {"type": "ephemeral"}` markers on the largest
  immutable prefix (system prompt; the symbol-index/impact context;
  the planning blueprint).
- OpenAI and DeepSeek requests are unchanged on the wire (server-side
  auto-cache), but a new **prefix-stability hasher** verifies the
  declared-immutable prefix actually stays byte-identical across calls;
  drift is logged as an observability event so we notice when a cache
  hit silently disappears.
- Ollama unchanged; a one-line note in `SPEC_REQUIREMENTS.md` documents
  that `num_ctx` must stay constant for the local KV cache to reuse.
- Observed effect: in a fresh repair-loop session (3+ rounds), the
  second and third rounds report `cache_read_input_tokens >> 0` and
  the per-call cost drops by ~70–85%.
- `harness metrics --session-id <id>` shows `cached_tokens` and
  `cache_creation_tokens` cleanly. (These columns already exist; we
  verify they accumulate.)

### 1.2 Design

The Anthropic Messages API requires `cache_control` markers attached to
**content blocks**, not the top-level `system` field as a string. To
opt a system prompt into caching, it must be sent as a list of blocks:

```python
payload["system"] = [
    {"type": "text", "text": prompt_text, "cache_control": {"type": "ephemeral"}}
]
```

Markers must sit on content that is (a) byte-identical across calls
within 5 minutes, and (b) at least 1024 tokens (Sonnet/Opus) or 2048
(Haiku — though current models relax this). Anthropic allows up to **4
cache breakpoints** per request; we use 2:

1. **System prompt block** — always anchored, always immutable per
   role (planning system prompt is stable across the run; patching
   system prompt is stable; repair system prompt is stable).
2. **First user message** — when it carries the immutable preamble
   (impact analysis context, READ_FILE results, architecture inventory,
   directory tree). The graph constructs these once per loop and they
   recur unchanged.

The marker is added inside `AnthropicProvider.chat_completion` only
when:
- `self.spec.supports_cache` is True, AND
- `GatewayConfig.prompt_cache_enabled` is True (new config flag, default `true`)

For DeepSeek and OpenAI: nothing to do on the request side; both
already extract `prompt_tokens_details.cached_tokens` and bill at
`cached_input_cost_per_1m`. We add a verification step (assert
`model_prices.json` carries `cached_input_cost_per_1m` for every
model with `supports_cache: true`). Already true today for all 4
OpenAI models + both DeepSeek models.

**Prefix-stability hasher (provider-agnostic):**

```python
# new utility in gateway.py
def hash_stable_prefix(messages: list[dict], n_stable_messages: int) -> str:
    """SHA-256 the first n_stable_messages of a request. Cheap (~µs)."""

# called from Gateway.dispatch BEFORE provider call
prefix_hash = hash_stable_prefix(messages, n_stable_messages=2)
last_hash = self._last_prefix_hash.get((session_id, role))
if last_hash is not None and last_hash != prefix_hash:
    logger.warning(
        "[gateway] cache prefix drift detected for role=%s "
        "(prev=%s, now=%s) — auto-cache will miss this call.",
        role.value, last_hash[:8], prefix_hash[:8],
    )
    emit_event("cache_prefix_drift", role=role.value, ...)
self._last_prefix_hash[(session_id, role)] = prefix_hash
```

The hasher is observability-only on first land — it warns, it doesn't
mutate the request. After a session of running, we'll see in the logs
which graph nodes are leaking prefix stability (likely culprits:
timestamps in the planning blueprint, file-mtime in READ_FILE results,
randomly-ordered impact-analysis sets).

### 1.3 Files to change

| File | Change |
|------|--------|
| `harness/gateway.py` | `GatewayConfig` adds `prompt_cache_enabled: bool = True`. `AnthropicProvider.chat_completion` rewrites `payload["system"]` to a list-of-blocks form with `cache_control` when enabled. Optionally marks the *first* non-system message as the second cache breakpoint when its content is ≥ 1024 tokens (rough char-count gate). |
| `harness/gateway.py` | `ensure_prefix_cache_anchor` extended to (optionally) tag the first user message's `content` with an internal flag the provider can read. Cleaner alternative: drop the flag, let the provider just pick blocks 0 and 1 unconditionally. **Recommended: keep it dumb in the provider, no cross-layer flag.** |
| `harness/model_prices.json` | Verify (or add) `cached_input_cost_per_1m` + `cache_creation_cost_per_1m` for every model with `supports_cache: true`. |
| `harness/cli.py` | `_KNOWN_NESTED_KEYS["llm_dispatch"]` adds `prompt_cache_enabled` (config-typo catcher only). |
| `config/config.json` | Add the new key under `llm_dispatch` with the default `true`. |
| `docs/SPEC_REQUIREMENTS.md` | One new bullet: "FR-NN: When `supports_cache=true`, the gateway emits cache_control markers on the system prompt." |
| `CHANGELOG.md` | "Added: Anthropic prompt caching." |
| `tests/test_gateway_guards.py` (extend) or new `tests/test_prompt_cache.py` | Asserts marker is present in payload when enabled; absent when disabled; payload still validates against the Anthropic shape. |

### 1.4 Impact analysis — what could break?

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Anthropic returns 400 because the list-of-blocks system shape is incompatible with the configured `anthropic-version` header | Low (the 2023-06-01 stable accepts blocks; `extended-cache-ttl` is an additive beta header for longer TTL) | Marker emission gated on `supports_cache`; rollback flag (`prompt_cache_enabled=false`) restores legacy string form. Tests pin the payload shape. |
| Cost accounting double-counts: cache_read tokens billed at full rate too | None — `AnthropicProvider.compute_cost` already deducts. Confirmed at `gateway.py:607-624`. | n/a |
| Existing `test_gateway_guards.py` payload assertions break | Possible — if any test pins `payload["system"]` as a string | Add cache-aware branch in tests; keep string form when `prompt_cache_enabled=false`. |
| Speculative variants forced to recompute cache for each variant | Real — each variant currently builds its own messages list | Acceptable in v1. Speculative is disabled by default. Follow-up: thread a shared prefix into variants. |
| `debug.dump_llm_calls` dumps now include cache markers in the input dump (cosmetic noise) | Cosmetic | Strip cache_control before dumping (one extra line in `_dump_llm_call_to_disk`). |
| Provider returns `cache_creation_tokens > 0` but model_prices.json has no `cache_creation_cost_per_1m` | Already handled — falls back to `1.25× input_cost_per_1m` at `gateway.py:613-619` | n/a |
| Existing sessions resumed from checkpoint may see one cache-miss on resume | Expected. 5-minute TTL means resume after lunch always misses. | Document, not a bug. |

### 1.5 Out of scope for this slice

- Extended 1-hour TTL via `anthropic-beta: extended-cache-ttl-2025-04-11`.
  Future flag.
- DeepSeek explicit prefix-cache pinning (DeepSeek caches automatically).
- OpenAI prefix-cache (gpt-4o family caches automatically, no client
  marker needed; the existing usage extraction handles it).

---

## 2. Initiative #3 — Web tools (`WebFetchSkill` + `WebSearchSkill`)

### 2.1 What "done" looks like

Two new `ToolSkill` instances are registered in the skill registry:

- `web_fetch(url, max_bytes=200_000) -> str` — HTTP GET, content-type
  whitelisted (html, text, json, markdown), strips HTML to readable
  text, capped, returns content + status.
- `web_search(query, max_results=5) -> list[{title,url,snippet}]` —
  pluggable backend, default `duckduckgo_lite` (no API key), optional
  `tavily` / `brave` / `serpapi` when keys are configured.

The planner and patcher prompts gain a documented text-DSL block
shape — matching the existing `<<<READ_FILE>>>` pattern — that the
graph intercepts and executes via the skill registry:

```
<<<WEB_FETCH url="https://docs.example.com/x" >>>
<<<WEB_SEARCH query="rust async best practices" max_results=5 >>>
```

When the planner / patcher / repair node emits such a block, the graph
short-circuits to invoke the skill, appends the result back into the
conversation as a `tool` message, and re-runs the dispatch.

### 2.2 Why text-DSL not native function-calling?

The harness's existing patcher pattern is text-DSL, and
`use_structured_tools` is shipped `false` because per-provider wiring
is incomplete (see `gateway.py:1158-1168`). Forcing web tools through
native function-calling would mean delivering that wiring first.
Text-DSL is consistent with `<<<READ_FILE>>>`, `<<<CREATE_FILE>>>`,
SEARCH/REPLACE — and the moment `use_structured_tools` lands, the
same `ToolSkill.to_tool_schema()` JSON is reusable.

### 2.3 Files to change

| File | Change |
|------|--------|
| `harness/web_tools.py` (new) | `WebFetchSkill` and `WebSearchSkill` (subclasses of `ToolSkill`). HTTP via `httpx.AsyncClient` (same dep as gateway). HTML→text via `lxml.html.fromstring(...).text_content()` if available, else regex fallback. Search backends behind a `WebSearchBackend` ABC: `DuckDuckGoLiteBackend` (HTML scrape, no key), `TavilyBackend`, `BraveBackend`. |
| `harness/skills.py` | `register_builtin_skills()` registers the two new tools when their dependencies import cleanly (same try/except pattern as lintgate/speculative/security). |
| `harness/cli.py` | `cmd_run` / `cmd_resume` call `register_builtin_skills()` once at startup. **This is currently only called by tests** — wiring it into the CLI is a one-line change that also enables the existing docgen skills at runtime. (See impact note below.) |
| `harness/graph.py` | New helper `_intercept_tool_blocks(content: str) -> list[(name, args)]` parses `<<<WEB_FETCH ...>>>` / `<<<WEB_SEARCH ...>>>` blocks. `planning_node`, `patching_node`, `repair_node` check for them after each dispatch; if found, run the tool via `SkillRegistry().dispatch`, append the result as a `user` message ("Tool web_fetch result: ..."), and re-dispatch up to a small cap (e.g., 3 tool rounds per node call). |
| `harness/cli.py` | `_KNOWN_TOP_LEVEL_KEYS` adds `"web_tools"`; `_KNOWN_NESTED_KEYS["web_tools"]` = `{"enabled", "allow_network", "max_bytes", "max_results", "search_backend", "api_key_env"}`. |
| `config/config.json` | Add `web_tools` section with safe defaults: `enabled: false` initially, `max_bytes: 200000`, `max_results: 5`, `search_backend: "duckduckgo_lite"`. |
| `harness/security.py` | `CommandValidator` is unrelated, but `harness/trust.py` should grow a `validate_outbound_url(url)` function: rejects `file://`, `localhost`, `127.0.0.0/8`, `169.254.0.0/16` (cloud metadata), `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, unless `web_tools.allow_private_ips` is true (default false). |
| `harness/redactor.py` | Already strips secrets from outbound messages — confirm it also runs on the synthesised `tool result` we append, so fetched HTML doesn't smuggle API keys back into the next LLM call. |
| `tests/test_web_tools.py` (new) | Backend mocked with `httpx.MockTransport`. Asserts URL validation, content-type filtering, byte cap, redaction of result content, behaviour when `web_tools.enabled=false` (skill returns "disabled" error). |
| `docs/SPEC_REQUIREMENTS.md` | New FR-NN: web_fetch / web_search tool DSL contract. |
| `CHANGELOG.md` | "Added: web_fetch and web_search skills." |

### 2.4 Impact analysis — what could break?

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| **Sandbox/network coupling**: web tools execute in the *host* process (gateway), not the sandboxed build subprocess. They are NOT bound by `--allow-network` (which gates sandbox subprocess network only). | This is by design but easy to confuse. | Document explicitly. New config flag `web_tools.enabled` is the gate — orthogonal to `--allow-network`. Default `false` keeps current behavior. |
| `register_builtin_skills()` being called for the first time during a real run may surface latent bugs in the docgen/speculative/security skill imports | Medium — those imports run in tests today but not in cli.py | Wrap the call in try/except; failures log and continue (skills are additive, not required). Add an explicit unit test exercising the cli.py registration path. |
| Web fetches inject unbounded content into the LLM context window | Real — could blow past `check_context_window` threshold mid-run | Hard cap at `max_bytes` (default 200 KB ≈ 50k tokens). Pre-trim before append. `check_context_window` already runs on every dispatch so an overflow still triggers truncation, just less elegantly. |
| HTML scraping returns garbage on JS-heavy sites | Real | Document the limitation; surface clear "page may need a headless browser" error. A future `WebBrowseSkill` can wrap Playwright. |
| Search backend ToS violation if scraping DDG HTML at scale | Real but low | Default rate-limit to 1 query / 2 seconds; document the trade-off; ship Tavily/Brave as the recommended backends for any volume. |
| Tool DSL collision with existing patcher DSL | Low — `<<<WEB_FETCH>>>` / `<<<WEB_SEARCH>>>` are new tokens, parser is regex-bounded | grep'd existing patterns in `patcher.py`, `process_llm_patch_output`; no collisions. New parser added in `graph.py` runs BEFORE patch parsing, intercepts first, removes the block, then falls through. |
| Infinite tool loop (LLM keeps fetching) | Real | Hard cap: max 3 tool rounds per dispatch. After cap, append "Tool budget exhausted for this turn" and force the LLM to proceed. |
| `process_llm_patch_output` may try to parse `<<<WEB_FETCH>>>` as a malformed patch block | Possible | Intercept-and-strip in `graph.py` before passing content into the patcher. Verified by a regression test. |
| SSRF: LLM tells the harness to fetch `http://169.254.169.254/latest/meta-data` from inside a cloud VM | Real | `trust.validate_outbound_url` blocks RFC1918 + link-local by default. |
| Web tools break offline / air-gapped installs | Real | `web_tools.enabled = false` is the default. Air-gapped installs never see web tools. `doctor` should mention this. |

### 2.5 Out of scope for this slice

- Headless browser / JS rendering.
- A general PDF / image fetcher (mime-types restricted to text-ish).
- Caching web results to disk for replay.
- Built-in citation tracking.

---

## 3. Initiative #1 — MCP client

### 3.1 What "done" looks like

The harness can connect to one or more **MCP servers** declared in
config and expose their tools as `ToolSkill`s in the existing skill
registry. The text-DSL extension from #3 is reused:

```
<<<MCP_CALL server="github" tool="get_issue" args={"repo":"owner/x","number":42}>>>
```

This works because MCP servers expose typed tools/resources/prompts
that map cleanly onto `ToolSkill`. The MCP client lives entirely in
the gateway side; the LLM never speaks MCP directly.

Supported transports v1:
- **stdio** (`npx -y @modelcontextprotocol/server-filesystem ~/code`)
- **HTTP/SSE** (the standard streaming transport)

Not v1: WebSockets, custom transports, sampling, roots/elicitation
(MCP servers requesting LLM completions back from the host — useful
but doubles the attack surface).

### 3.2 Design

```
              ┌──────────────────────────────────────────────────┐
              │            McpClientPool (new)                   │
              │  - config: list[McpServerConfig]                 │
              │  - clients: dict[name, McpClient]                │
              │  - start() / shutdown()                          │
              │  - list_tools() / call_tool(server, tool, args)  │
              └──────────────────┬───────────────────────────────┘
                                 │
                ┌────────────────┼────────────────┐
                ▼                ▼                ▼
        ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
        │ StdioClient  │  │ HttpSseClient│  │ ...          │
        │ (subprocess) │  │ (httpx)      │  │              │
        └──────────────┘  └──────────────┘  └──────────────┘

   At startup (cli.py:cmd_run):
     1. read config.mcp.servers → build McpClientPool
     2. start() each — handshake initialize / list_tools
     3. for each tool returned, register an McpToolSkill in SkillRegistry
        (name = f"mcp__{server_name}__{tool_name}")
     4. proceed with the existing graph
   At end-of-run:
     - shutdown all clients (subprocess kill, http close)
```

Implementation choice: use the official Python MCP SDK
(`pip install mcp`) for the protocol handling. It already implements
client transports, the typed tool descriptors, and the JSON-RPC layer.
The official SDK is ~5 KB on the wire, ~10 transitive deps. Acceptable.

If we want to avoid the dep, we can hand-write the JSON-RPC client —
~200 lines for stdio + HTTP/SSE. **Recommendation: use the SDK** to
get protocol upgrades for free.

### 3.3 Files to change

| File | Change |
|------|--------|
| `harness/mcp_client.py` (new) | `McpServerConfig` dataclass, `McpClient` ABC, `StdioMcpClient`, `HttpSseMcpClient`, `McpClientPool`. Wraps the official SDK if installed, else hand-rolled fallback. |
| `harness/mcp_skill.py` (new) — or fold into `skills.py` | `McpToolSkill(ToolSkill)` — implements `execute(**kwargs)` by calling `pool.call_tool(server, tool, kwargs)`. `to_tool_schema()` returns the MCP-declared JSON schema directly. |
| `harness/skills.py` | New helper `register_mcp_skills(pool: McpClientPool)` that iterates tools and registers them. |
| `harness/cli.py` | `cmd_run` / `cmd_resume`: after `register_builtin_skills()`, if `config.mcp` is set, build the pool, `await pool.start()`, register skills, hand the pool to `run_graph` via context. On shutdown / Ctrl-C, await `pool.shutdown()`. |
| `harness/graph.py` | The `_intercept_tool_blocks` parser from #3 also handles `<<<MCP_CALL>>>`. No node-level changes beyond what #3 already adds. |
| `harness/cli.py` | `_KNOWN_TOP_LEVEL_KEYS` adds `"mcp"`; `_KNOWN_NESTED_KEYS["mcp"]` = `{"enabled", "servers", "tool_call_timeout_seconds", "allow_local_filesystem_servers"}`. |
| `config/config.json` | New `mcp` section, default `enabled: false`. Documented example: `filesystem`, `github`, `time` MCP servers. |
| `harness/cli.py` (`cmd_doctor`) | New healthcheck: when `mcp.enabled=true`, attempt to start each server and list its tools. Fail-fast on bad config. |
| `harness/trust.py` | `validate_mcp_server_command(cmd_args: list[str])` — refuses absolute paths under `/etc`, `/root`, `/proc`, `/sys`; refuses `sudo`, `su`, `bash -c`. Aligns with `CommandValidator`. |
| `tests/test_mcp_client.py` (new) | Mocks JSON-RPC over an in-memory pipe; round-trips initialize / list_tools / call_tool. Tests pool shutdown leaves no zombies. |
| `docs/mcp-integration.md` (new) | Tutorial: configure a filesystem MCP server, an HTTP-SSE server. Security caveats. |
| `docs/SPEC_REQUIREMENTS.md` | New FR-NN: MCP client requirements. |
| `pyproject.toml` | Optional dep: `mcp = ">=1.0"` under `[project.optional-dependencies] mcp = [...]`. Core install does NOT pull it in. |
| `CHANGELOG.md` | "Added: MCP client (`mcp` optional extra)." |

### 3.4 Impact analysis — what could break?

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| **MCP server is a subprocess we spawn**: malicious config could exec arbitrary binaries on the host | Real and serious | `trust.validate_mcp_server_command` whitelists patterns. By default require server commands be one of `["npx","node","python","python3","uvx","docker"]` (allowlist), reject everything else. `mcp.enabled=false` by default. |
| MCP server has access to the *host*, not the sandbox — bypasses `harness/sandbox.py` isolation | By design, but a new attack surface | Document loudly. Config flag `mcp.allow_local_filesystem_servers` defaults `false`; when `false` we refuse to start any MCP server whose command name matches `*filesystem*` (heuristic) or `*shell*`. Operators that *want* a filesystem MCP must opt in. |
| Slow/hanging MCP server stalls the graph | Real | `tool_call_timeout_seconds` default 30s; per-call asyncio.wait_for. A timed-out call returns a structured error to the LLM, the graph continues. |
| MCP `initialize` handshake takes 1–5 seconds × N servers — cold-start lag | Real | Start clients concurrently via `asyncio.gather`. Doctor surfaces total handshake time. |
| Subprocess MCP clients leak when harness crashes mid-run | Real | Register `atexit` handler + Ctrl-C handler in `cli.py`. Process-group kill on shutdown. Test with `pytest -m signals`. |
| Tool name collision (filesystem.read_file collides with the patcher's READ_FILE DSL) | Low — MCP tool names get the `mcp__<server>__` prefix | Documented; parser distinguishes. |
| MCP server returns huge result (e.g. read a 10 MB log) | Real | Same `max_bytes` cap as web tools. Truncate before injecting into the conversation. |
| The optional `mcp` package adds 10+ deps; corporate proxies may reject | Real | Optional extra (`pip install -e ".[mcp]"`). Core install unaffected. Hand-rolled fallback path always works. |
| `harness doctor` runs in CI environments without MCP servers configured | Fine | Healthcheck only runs when `mcp.enabled=true`; otherwise silent. |
| Existing test suite imports `harness.cli` in tests; if `mcp_client.py` import-time blows up, the whole suite is dead | Real | `cli.py` imports the MCP client module *lazily* (inside `cmd_run`), not at module load. Mirrors the existing pattern for `harness.deploy` / `harness.speculative`. |
| Operators expect MCP `prompts` and `resources` capabilities, not just `tools` | Real | v1: tools only. v1.1: resources (read-only attachments injected as system context). Document. |
| MCP servers expose `sampling` (asking the host to LLM-complete) — security hole | Real but easy | v1: explicitly decline sampling requests (return error). Documented. |

### 3.5 Out of scope for v1

- MCP `prompts` capability — server-provided prompt templates.
- MCP `resources` capability — server-provided context attachments.
- MCP `sampling` — server asks host to LLM-complete (refuse).
- MCP `roots` / `elicitation`.
- WebSocket transport.
- Per-tool ACL (whitelist of tools per server).

---

## 4. Cross-cutting test plan

| Layer | New / extended tests |
|-------|----------------------|
| **Prompt cache** | `tests/test_prompt_cache.py`: payload assertions per provider; cost accounting with cache hits / creations; toggle off restores legacy shape; cache_creation_cost fallback to 1.25× input. |
| **Web tools** | `tests/test_web_tools.py`: URL validation; content-type filter; byte cap; redaction of result; tool loop cap; intercept order vs patcher; disabled mode. |
| **Tool DSL parser** | `tests/test_tool_intercept.py`: `<<<WEB_FETCH>>>` / `<<<WEB_SEARCH>>>` / `<<<MCP_CALL>>>` parse correctly; malformed blocks return error; block stripped before patcher sees content; collision with `<<<READ_FILE>>>` etc. is impossible. |
| **MCP** | `tests/test_mcp_client.py`: handshake, list_tools, call_tool, timeout, shutdown cleanup, command validator rejects unsafe binaries. |
| **CLI wiring** | extend `tests/test_cli_basics.py`: `cmd_run` calls `register_builtin_skills`; doctor reports MCP health when enabled. |
| **Doctor** | extend `tests/test_doctor.py`: new `mcp` check pass/fail. |
| **Regression** | full pytest pack must stay green — current pack covers gateway dispatch, patcher, security, lintgate, autofix, observability, change-requests. Any failing test post-change is a hard stop. |

Acceptance bar: **zero regression** in the existing pack +
~25 new tests (~8 per initiative).

---

## 5. Config schema additions (consolidated)

```json
{
  "llm_dispatch": {
    "prompt_cache_enabled": true                       // NEW (#2)
  },
  "web_tools": {                                       // NEW (#3)
    "enabled": false,
    "max_bytes": 200000,
    "max_results": 5,
    "search_backend": "duckduckgo_lite",
    "api_key_env": "",
    "allow_private_ips": false,
    "tool_call_cap_per_dispatch": 3
  },
  "mcp": {                                             // NEW (#1)
    "enabled": false,
    "tool_call_timeout_seconds": 30,
    "allow_local_filesystem_servers": false,
    "servers": [
      {
        "name": "filesystem",
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
      },
      {
        "name": "internal-docs",
        "transport": "http_sse",
        "url": "https://mcp.internal.example.com/v1",
        "api_key_env": "INTERNAL_MCP_KEY"
      }
    ]
  }
}
```

Each new key is wired into `_KNOWN_TOP_LEVEL_KEYS` /
`_KNOWN_NESTED_KEYS` so typos surface with fuzzy suggestions, same
contract as every other section.

---

## 6. Rollout order, gating, and rollback

**Order (lowest risk first):**

1. **Land prompt cache** (#2) — flag-gated, default ON for cache-capable
   models. One PR. Pre-commit hooks + CI must pass green.
2. **Land web tools** (#3) — flag-gated, default OFF. Same PR can also
   wire `register_builtin_skills()` into `cli.py` for the first time.
3. **Land MCP client** (#1) — flag-gated, default OFF; new optional
   pyproject extra.

**Single-flag rollback for each:** flip the `enabled` to `false` in
`config/config.json` and existing flow is unaffected. The `gateway.py`
default for `prompt_cache_enabled` is `true`, but operators can flip
to `false` in their own `~/.harness/config.json` or
`.harness_config.json`.

**Reversibility check:** every new file is brand-new; every edit to
existing files is additive (new fields with defaults, new conditional
branches). No existing function signature changes. No existing config
key renames. Checkpoint schema unchanged — existing sessions resume
cleanly.

---

## 7. Defaults I'm choosing for the 5 open questions

You approved the plan with auto-mode active, which says move forward
and the user will redirect if a default is wrong. The defaults I'm
going with — each is the *conservative*, *additive*, *flag-gated*
option:

1. **Text-DSL for tools, not native function-calling.** Matches
   `<<<READ_FILE>>>` / `<<<CREATE_FILE>>>` / SEARCH/REPLACE patterns
   already in the patcher. `use_structured_tools=false` stays as is;
   `ToolSkill.to_tool_schema()` will be reusable later if we flip it.
2. **`register_builtin_skills()` wired into `cmd_run` / `cmd_resume`
   in `cli.py`.** Today it's only called from tests. The docgen
   skills coming live at runtime is a harmless side-effect — they
   were clearly intended to. Each individual skill registration is
   already wrapped in try/except so a docgen import failure won't
   break startup.
3. **MCP SDK as optional dep (`pip install -e ".[mcp]"`).** The
   official `mcp` package handles JSON-RPC + transports for free and
   tracks the spec. Core install stays clean. Hand-rolled fallback
   left as a future option if a corporate proxy rejects the deps.
4. **Default-on for cache, default-off for web/MCP.** Cache is pure
   internal cost optimization with one rollback flag. Web/MCP add new
   network and subprocess attack surface — operators must opt in.
5. **Search backend = `duckduckgo_lite` default.** Works out of the
   box, no key. Documented rate-limit (1 query / 2 s) keeps it inside
   ToS for harness-scale traffic. Operators that want volume swap to
   Tavily / Brave / SerpAPI via `web_tools.search_backend`.

Redirect any of these if wrong — they're all single-flag changes.

---

## 8. Original open questions (preserved for the record)

1. **Tool DSL vs native function-calling**: I'm proposing text-DSL
   (`<<<WEB_FETCH ...>>>`) because it matches the existing patcher
   pattern and avoids needing to ship `use_structured_tools=true`
   wiring first. Agree, or do you want me to finish the structured-
   tool wiring as part of this slice?
2. **`register_builtin_skills()` wired into `cli.py`**: today it's
   only called from tests, so docgen skills are effectively dead at
   runtime. Web tools + MCP require this to be wired. Are you OK with
   the existing docgen skills also coming live as a side-effect? (I
   don't see a reason not to — they were clearly intended to.)
3. **MCP SDK as optional dep**: `mcp` package, ~10 transitive deps,
   installed via `pip install -e ".[mcp]"`. Acceptable, or do you want
   the hand-rolled JSON-RPC fallback to be the default?
4. **Default-on for cache, default-off for web/MCP**: cache is purely
   internal cost optimization; web/MCP add new network/process attack
   surface. Confirm this default posture.
5. **Search backend default**: `duckduckgo_lite` (no key, scrapes HTML)
   vs requiring an explicit key for any search to work. I picked DDG
   for "works out of the box"; you may want stricter.

Mark up this file with feedback and I'll convert it to PRs.

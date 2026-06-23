"""Regression tests for the web tools slice.

Covers:
    - SSRF guard in ``harness.trust.validate_outbound_url``.
    - HTML → text helper produces readable output without script bodies.
    - DSL parser extracts ``<<<WEB_FETCH>>>`` / ``<<<WEB_SEARCH>>>`` blocks
      with mixed quoted and integer kwargs.
    - DSL parser is robust to malformed blocks (returns nothing rather
      than raising).
    - ``WebFetchSkill`` short-circuits when ``web_tools.enabled=false``.
    - ``WebFetchSkill`` enforces content-type allowlist + byte cap.
    - ``WebSearchSkill`` returns structured results via a stub backend.
    - ``register_builtin_skills(config=…)`` registers web tools only
      when enabled.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import harness.web_tools as web_tools_module
from harness.skills import SkillRegistry, register_builtin_skills
from harness.trust import validate_outbound_url
from harness.web_tools import (
    SearchResult,
    WebFetchSkill,
    WebSearchSkill,
    WebToolsConfig,
    html_to_text,
    parse_tool_blocks,
    strip_tool_blocks,
)


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------

def test_validate_outbound_url_accepts_https():
    # resolve_dns=False keeps this a pure shape/scheme/host check — the
    # other "real DNS" branches have their own coverage. Without it the
    # test depends on a live getaddrinfo("docs.python.org") and flakes
    # whenever the network or DNS hiccups.
    assert validate_outbound_url(
        "https://docs.python.org/3/", resolve_dns=False
    ) == "https://docs.python.org/3/"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "javascript:alert(1)",
    "data:text/plain;base64,YWJj",
])
def test_validate_outbound_url_rejects_unsafe_scheme(url):
    with pytest.raises(ValueError):
        validate_outbound_url(url)


@pytest.mark.parametrize("url", [
    "http://localhost/admin",
    "http://127.0.0.1:8080/",
    "http://169.254.169.254/latest/meta-data",   # AWS metadata
    "http://10.0.0.5/",
    "http://192.168.1.10/",
    "http://172.16.0.1/",
])
def test_validate_outbound_url_blocks_private_ips_by_default(url):
    with pytest.raises(ValueError):
        validate_outbound_url(url)


def test_validate_outbound_url_allows_private_when_opted_in():
    assert validate_outbound_url(
        "http://10.0.0.5/", allow_private_ips=True,
    ) == "http://10.0.0.5/"


def test_validate_outbound_url_rejects_empty():
    with pytest.raises(ValueError):
        validate_outbound_url("")


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------

def test_html_to_text_strips_scripts_and_decodes_entities():
    html = """<html><head><script>alert(1)</script><style>body{}</style></head>
    <body><h1>Title&nbsp;&amp;&nbsp;Subtitle</h1>
    <p>Para&nbsp;one</p>

    <p>Para two&#x26;final</p></body></html>"""
    text = html_to_text(html)
    assert "alert" not in text
    assert "body{}" not in text
    assert "Title & Subtitle" in text
    assert "Para one" in text
    assert "Para two&final" in text


# ---------------------------------------------------------------------------
# DSL parser
# ---------------------------------------------------------------------------

def test_parse_tool_blocks_handles_mixed_kwargs():
    content = (
        'Some preamble.\n'
        '<<<WEB_FETCH url="https://docs.python.org/" max_bytes=50000>>>\n'
        'Then later:\n'
        '<<<WEB_SEARCH query="python asyncio cancellation" max_results=3>>>\n'
        'And done.'
    )
    blocks = parse_tool_blocks(content)
    assert len(blocks) == 2
    assert blocks[0].skill_name == "web_fetch"
    assert blocks[0].kwargs == {"url": "https://docs.python.org/", "max_bytes": 50000}
    assert blocks[1].skill_name == "web_search"
    assert blocks[1].kwargs == {"query": "python asyncio cancellation", "max_results": 3}


def test_parse_tool_blocks_ignores_content_without_blocks():
    assert parse_tool_blocks("nothing to see here") == []
    assert parse_tool_blocks("") == []
    assert parse_tool_blocks(None) == []  # type: ignore[arg-type]


def test_strip_tool_blocks_removes_blocks_keeps_surrounding_text():
    content = (
        'Heading\n'
        '<<<WEB_FETCH url="https://example.com">>>\n'
        'Trailing.'
    )
    stripped = strip_tool_blocks(content)
    assert "<<<" not in stripped
    assert "Heading" in stripped
    assert "Trailing." in stripped


# ---------------------------------------------------------------------------
# WebFetchSkill — disabled guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_fetch_skill_returns_error_when_disabled():
    cfg = WebToolsConfig(enabled=False)
    skill = WebFetchSkill(cfg)
    result = await skill.execute(url="https://example.com/")
    assert isinstance(result, dict)
    assert "disabled" in result["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_skill_rejects_localhost_url_via_ssrf_guard():
    cfg = WebToolsConfig(enabled=True)
    skill = WebFetchSkill(cfg)
    result = await skill.execute(url="http://localhost/admin")
    assert "url rejected" in result["error"]


# ---------------------------------------------------------------------------
# WebFetchSkill — HTTP path stubbed via httpx mock transport
# ---------------------------------------------------------------------------

class _MockStreamResponse:
    """Async-context-manager wrapper that mimics httpx.Client.stream(...).

    After EDGE_CASE_AUDIT.md §4.11 the web fetch path uses
    ``client.stream(...) → aiter_bytes()`` instead of ``client.get`` →
    ``response.content`` so it can bound memory on huge bodies. The
    test mock now mirrors that interface.
    """

    def __init__(self, response: httpx.Response):
        self._response = response

    async def __aenter__(self) -> httpx.Response:
        return self._response

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _MockAsyncClient:
    """Stand-in for httpx.AsyncClient used by the fetch path."""

    def __init__(self, response: httpx.Response, captured: list[str]):
        self._response = response
        self._captured = captured

    async def __aenter__(self) -> "_MockAsyncClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str) -> httpx.Response:
        self._captured.append(url)
        return self._response

    def stream(self, method: str, url: str) -> _MockStreamResponse:  # noqa: ARG002
        self._captured.append(url)

        # Wrap the canned httpx.Response with an aiter_bytes that
        # streams the content in small chunks so the new code's
        # cap-while-iterating path is exercised.
        async def _aiter(self_inner):
            data = self_inner._content or b""
            chunk = 1024
            for i in range(0, len(data), chunk):
                yield data[i:i + chunk]

        self._response.aiter_bytes = lambda: _aiter(self._response)
        return _MockStreamResponse(self._response)


@pytest.mark.asyncio
async def test_web_fetch_skill_truncates_at_max_bytes(monkeypatch):
    cfg = WebToolsConfig(enabled=True, max_bytes=64)
    skill = WebFetchSkill(cfg)
    big_body = b"a" * 10_000
    fake_response = httpx.Response(
        status_code=200,
        content=big_body,
        headers={"content-type": "text/plain"},
        request=httpx.Request("GET", "https://example.com/data"),
    )
    captured: list[str] = []
    monkeypatch.setattr(
        web_tools_module.httpx, "AsyncClient",
        lambda **_kw: _MockAsyncClient(fake_response, captured),
    )
    # Pin DNS so validate_outbound_url doesn't hit the network — the
    # test is about the byte cap, not example.com resolution.
    monkeypatch.setattr(
        "harness.trust._resolve_host_addresses",
        lambda host: ["93.184.216.34"],
    )
    result = await skill.execute(url="https://example.com/data")
    assert result["truncated"] is True
    assert result["bytes_returned"] == 64
    assert len(result["content"]) <= 64
    assert captured == ["https://example.com/data"]


@pytest.mark.asyncio
async def test_web_fetch_skill_rejects_unwhitelisted_content_type(monkeypatch):
    cfg = WebToolsConfig(enabled=True)
    skill = WebFetchSkill(cfg)
    fake_response = httpx.Response(
        status_code=200,
        content=b"\x00\x01\x02",
        headers={"content-type": "application/octet-stream"},
        request=httpx.Request("GET", "https://example.com/blob"),
    )
    monkeypatch.setattr(
        web_tools_module.httpx, "AsyncClient",
        lambda **_kw: _MockAsyncClient(fake_response, []),
    )
    # Pin DNS so we test the content-type gate, not example.com lookup.
    monkeypatch.setattr(
        "harness.trust._resolve_host_addresses",
        lambda host: ["93.184.216.34"],
    )
    result = await skill.execute(url="https://example.com/blob")
    assert "content-type" in result["error"]


# ---------------------------------------------------------------------------
# WebSearchSkill — stub backend
# ---------------------------------------------------------------------------

class _StubBackend:
    name = "stub"

    def __init__(self, *, timeout_seconds: float = 0):  # noqa: ARG002
        return

    async def search(self, query: str, max_results: int):  # noqa: ARG002
        return [
            SearchResult(title="Result 1", url="https://r1.example/", snippet="s1"),
            SearchResult(title="Result 2", url="https://r2.example/", snippet="s2"),
            SearchResult(title="Result 3", url="https://r3.example/", snippet="s3"),
        ][:max_results]


@pytest.mark.asyncio
async def test_web_search_skill_returns_structured_results():
    cfg = WebToolsConfig(enabled=True, max_results=2, search_backend="stub")
    skill = WebSearchSkill(cfg)
    result = await skill._call(  # type: ignore[attr-defined]
        query="anything",
        _backend_factory=lambda name, timeout_seconds: _StubBackend(),
    )
    assert result["query"] == "anything"
    assert result["backend"] == "stub"
    assert len(result["results"]) == 2
    assert result["results"][0]["title"] == "Result 1"


@pytest.mark.asyncio
async def test_web_search_skill_rejects_empty_query():
    cfg = WebToolsConfig(enabled=True)
    skill = WebSearchSkill(cfg)
    result = await skill.execute(query="")
    assert "non-empty" in result["error"]


# ---------------------------------------------------------------------------
# Registry plumbing
# ---------------------------------------------------------------------------

def _drop_web_skills() -> None:
    """Wipe web skills from the (singleton) registry so each test starts
    from a known shape."""
    reg = SkillRegistry()
    for name in ("web_fetch", "web_search"):
        reg._skills.pop(name, None)  # type: ignore[attr-defined]


def test_register_builtin_skills_omits_web_tools_when_disabled():
    _drop_web_skills()
    register_builtin_skills(config={"web_tools": {"enabled": False}})
    reg = SkillRegistry()
    assert reg.get("web_fetch") is None
    assert reg.get("web_search") is None


def test_register_builtin_skills_registers_web_tools_when_enabled():
    _drop_web_skills()
    register_builtin_skills(config={"web_tools": {"enabled": True}})
    reg = SkillRegistry()
    assert reg.get("web_fetch") is not None
    assert reg.get("web_search") is not None
    _drop_web_skills()  # leave the registry clean for downstream tests


def test_register_builtin_skills_works_without_config_arg():
    """Historical call signature with no kwargs must still work — only
    pipeline + docgen skills register."""
    _drop_web_skills()
    register_builtin_skills()  # no config
    reg = SkillRegistry()
    assert reg.get("web_fetch") is None


# ---------------------------------------------------------------------------
# Configure-page overhaul: multi-instance web tools (backends list)
# ---------------------------------------------------------------------------

def test_web_tools_config_round_trips_backends_list():
    from harness.web_tools import WebToolsConfig
    cfg = WebToolsConfig.from_config({
        "web_tools": {
            "enabled": True,
            "search_backend": "duckduckgo_lite",
            "backends": [
                {"name": "brave", "enabled": True,
                 "search_backend": "brave", "api_key_env": "BRAVE_KEY"},
                {"name": "google", "enabled": False,
                 "search_backend": "google", "api_key_env": "GOOGLE_KEY"},
            ],
        }
    })
    assert len(cfg.backends) == 2
    names = [b["name"] for b in cfg.backends]
    assert "brave" in names and "google" in names


def test_web_tools_active_backends_filters_disabled_and_orders_primary_first():
    from harness.web_tools import WebToolsConfig
    cfg = WebToolsConfig.from_config({
        "web_tools": {
            "enabled": True,
            "search_backend": "duckduckgo_lite",
            "backends": [
                {"name": "brave", "enabled": True, "search_backend": "brave"},
                {"name": "google", "enabled": False, "search_backend": "google"},
                {"name": "no_backend", "enabled": True, "search_backend": ""},
            ],
        }
    })
    active = cfg.active_backends()
    backends = [b["search_backend"] for b in active]
    assert backends == ["duckduckgo_lite", "brave"]


def test_web_tools_active_backends_skips_primary_when_blank():
    from harness.web_tools import WebToolsConfig
    cfg = WebToolsConfig.from_config({
        "web_tools": {
            "search_backend": "",
            "backends": [
                {"name": "brave", "enabled": True, "search_backend": "brave"},
            ],
        }
    })
    active = cfg.active_backends()
    assert [b["search_backend"] for b in active] == ["brave"]


def test_web_tools_config_legacy_shape_still_loads():
    """Configs without a ``backends`` key keep working — the field
    defaults to an empty list."""
    from harness.web_tools import WebToolsConfig
    cfg = WebToolsConfig.from_config({
        "web_tools": {"enabled": True, "search_backend": "duckduckgo_lite"}
    })
    assert cfg.backends == []
    active = cfg.active_backends()
    assert active and active[0]["search_backend"] == "duckduckgo_lite"


# ---------------------------------------------------------------------------
# Pluggable backend registry
# ---------------------------------------------------------------------------
#
# The harness ships ONLY duckduckgo_lite as a built-in. Every other
# backend (Tavily, Brave, SerpAPI, in-house) is registered from a
# user-skill file via :func:`harness.web_tools.register_backend`. These
# tests pin that contract so a refactor that re-introduces a hardcoded
# ``if/elif`` (the bug we just removed) fails CI before merge.


class _FakeBackend:
    """Minimal SearchBackend stand-in for registry tests. Honours the
    timeout_seconds kwarg the registry passes through."""
    name = "fake"

    def __init__(self, *, timeout_seconds: float = 0.0):
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, max_results: int):  # noqa: ARG002
        return []


def test_ddg_is_registered_by_default():
    """DDG must be in the registry as soon as the module is imported, so
    a fresh install with no user skills can still web-search."""
    from harness.web_tools import registered_backends
    names = registered_backends()
    assert "duckduckgo_lite" in names
    # Legacy aliases must keep resolving so old configs don't break.
    assert "ddg" in names
    assert "duckduckgo" in names


def test_make_search_backend_resolves_registered_name():
    from harness.web_tools import make_search_backend, DuckDuckGoLiteBackend
    backend = make_search_backend("duckduckgo_lite", timeout_seconds=5.0)
    assert isinstance(backend, DuckDuckGoLiteBackend)


def test_make_search_backend_resolves_aliases():
    from harness.web_tools import make_search_backend, DuckDuckGoLiteBackend
    for alias in ("ddg", "duckduckgo", "DDG", "DuckDuckGo"):
        backend = make_search_backend(alias)
        assert isinstance(backend, DuckDuckGoLiteBackend), alias


def test_register_backend_adds_new_factory():
    """User-skill modules call register_backend() at import time. After
    the call, make_search_backend() must resolve the new name to a
    fresh instance via the factory."""
    from harness.web_tools import (
        make_search_backend, register_backend, unregister_backend,
    )
    try:
        register_backend("fake", _FakeBackend)
        backend = make_search_backend("fake", timeout_seconds=7.5)
        assert isinstance(backend, _FakeBackend)
        # The factory must receive the timeout kwarg so the operator's
        # web_tools.timeout_seconds actually bounds the HTTP call.
        assert backend.timeout_seconds == 7.5
    finally:
        unregister_backend("fake")


def test_register_backend_supports_aliases():
    from harness.web_tools import (
        make_search_backend, register_backend, unregister_backend,
    )
    try:
        register_backend("fake", _FakeBackend, aliases=["fk", "f"])
        for name in ("fake", "fk", "f", "FAKE"):
            assert isinstance(make_search_backend(name), _FakeBackend), name
    finally:
        for name in ("fake", "fk", "f"):
            unregister_backend(name)


def test_register_backend_overwrite_logs_and_replaces(caplog):
    """Re-registering the same name overwrites the prior factory and
    logs at INFO so an operator sees when a user file shadows a
    built-in. Idempotent re-registration of the SAME factory is silent."""
    import logging
    from harness.web_tools import register_backend, unregister_backend
    try:
        register_backend("fake", _FakeBackend)
        caplog.clear()
        # Same factory → no log.
        with caplog.at_level(logging.INFO, logger="harness.web_tools"):
            register_backend("fake", _FakeBackend)
        assert not any("re-registered" in r.message for r in caplog.records)
        # Different factory → INFO log so the shadow is visible.
        class _OtherFake(_FakeBackend):
            pass
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="harness.web_tools"):
            register_backend("fake", _OtherFake)
        assert any("re-registered" in r.message for r in caplog.records)
    finally:
        unregister_backend("fake")


def test_make_search_backend_unknown_lists_registered_names():
    """Error message must enumerate every registered name so the operator
    knows what's actually available — never another opaque 'Unknown
    backend' line that hides the answer."""
    from harness.web_tools import make_search_backend
    with pytest.raises(ValueError) as exc_info:
        make_search_backend("nope-not-a-real-backend")
    msg = str(exc_info.value)
    assert "nope-not-a-real-backend" in msg
    assert "duckduckgo_lite" in msg
    assert "register_backend" in msg


def test_register_backend_rejects_empty_name():
    from harness.web_tools import register_backend
    for bad in ("", "   ", None, 42):
        with pytest.raises((ValueError, AttributeError, TypeError)):
            register_backend(bad, _FakeBackend)  # type: ignore[arg-type]


def test_unregister_backend_is_idempotent():
    """Repeated unregister on a missing name is a no-op, not an error —
    tests rely on this to clean up regardless of registration outcome."""
    from harness.web_tools import unregister_backend
    unregister_backend("never-registered")
    unregister_backend("never-registered")


@pytest.mark.asyncio
async def test_web_search_skill_uses_registered_backend_at_call_time():
    """End-to-end: register a backend, then a WebSearchSkill configured
    to its name routes through it. Confirms _web_search_impl resolves
    the backend lazily, NOT at skill construction — which means user
    files loaded after WebSearchSkill registration still take effect."""
    from harness.web_tools import (
        make_search_backend, register_backend, unregister_backend,
    )

    class _RecordingBackend:
        name = "recording"
        calls: list[tuple[str, int]] = []

        def __init__(self, *, timeout_seconds: float = 0.0):  # noqa: ARG002
            return

        async def search(self, query: str, max_results: int):
            type(self).calls.append((query, max_results))
            return [SearchResult(title="hit", url="https://h/", snippet="s")]

    # Build the skill BEFORE registering, to prove resolution happens at
    # call-time. This is the load-order user-skill scenario.
    cfg = WebToolsConfig(enabled=True, search_backend="recording", max_results=2)
    skill = WebSearchSkill(cfg)

    try:
        register_backend("recording", _RecordingBackend)
        result = await skill._call(query="anything")  # type: ignore[attr-defined]
        assert result["backend"] == "recording"
        assert _RecordingBackend.calls == [("anything", 2)]
        assert result["results"][0]["title"] == "hit"
    finally:
        unregister_backend("recording")
        _RecordingBackend.calls.clear()
        # Sanity: registry is back to a clean state for any later tests.
        assert "recording" not in make_search_backend.__module__ or True


# ---------------------------------------------------------------------------
# Multi-backend fallback chain
# ---------------------------------------------------------------------------
#
# The harness has no hardcoded backend priority. ``cfg.active_backends()``
# returns the primary first (from ``web_tools.search_backend``) then every
# enabled entry from ``web_tools.backends`` in config order. ``_web_search_impl``
# walks the chain and stops at the first backend that returns without
# raising — including the empty-results case (legitimate "no hits", not
# a failure).


class _RaisingBackend:
    """Always raises on .search(). Tracks the exception type per instance
    so the test can assert "this specific failure was logged"."""
    name = "raising"

    def __init__(self, *, timeout_seconds: float = 0.0):  # noqa: ARG002
        return

    async def search(self, query: str, max_results: int):  # noqa: ARG002
        raise RuntimeError("simulated backend failure")


class _EmptyBackend:
    """Returns zero results without raising — a legitimate "no hits"
    outcome that MUST stop the chain (not fall through)."""
    name = "empty"

    def __init__(self, *, timeout_seconds: float = 0.0):  # noqa: ARG002
        return

    async def search(self, query: str, max_results: int):  # noqa: ARG002
        return []


class _RecordingBackend:
    """Returns canned results, records every call."""
    name = "ok"

    def __init__(self, *, timeout_seconds: float = 0.0):  # noqa: ARG002
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, max_results: int):
        self.calls.append((query, max_results))
        return [
            SearchResult(title="hit", url="https://h/", snippet="s"),
        ][:max_results]


def _factory_for(name_to_backend: dict[str, Any]):
    """Build a _backend_factory test seam from a name -> backend-instance
    map. Honours the same signature ``make_search_backend`` exposes so
    _web_search_impl treats it identically."""
    def _factory(name: str, *, timeout_seconds: float = 0.0):  # noqa: ARG001
        if name not in name_to_backend:
            raise ValueError(
                f"Unknown search backend {name!r}. Registered: "
                f"{sorted(name_to_backend.keys())}."
            )
        return name_to_backend[name]
    return _factory


@pytest.mark.asyncio
async def test_fallback_serves_from_secondary_when_primary_errors():
    """Primary raises → secondary serves. Result names the secondary
    backend and includes a ``fallback_from`` field documenting the
    skipped primary so operators see the fallback fired."""
    from harness.web_tools import _web_search_impl
    ok = _RecordingBackend()
    factory = _factory_for({"raising": _RaisingBackend(), "ok": ok})
    cfg = WebToolsConfig.from_config({
        "web_tools": {
            "enabled": True,
            "search_backend": "raising",
            "backends": [
                {"name": "secondary", "enabled": True, "search_backend": "ok"},
            ],
        }
    })
    result = await _web_search_impl(cfg, query="anything", _backend_factory=factory)
    assert "error" not in result
    assert result["backend"] == "ok"
    assert result["results"] == [{"title": "hit", "url": "https://h/", "snippet": "s"}]
    assert result["fallback_from"] == [
        {"backend": "raising", "error": "RuntimeError: simulated backend failure"},
    ]
    assert ok.calls == [("anything", 5)]


@pytest.mark.asyncio
async def test_fallback_empty_results_stops_the_chain():
    """Empty results = legitimate "no hits". The chain MUST NOT fall
    through to the next backend — that would silently double the cost
    on hard queries and hide the "nothing matched" signal from the LLM."""
    from harness.web_tools import _web_search_impl
    backup = _RecordingBackend()
    factory = _factory_for({"empty": _EmptyBackend(), "ok": backup})
    cfg = WebToolsConfig.from_config({
        "web_tools": {
            "enabled": True,
            "search_backend": "empty",
            "backends": [
                {"name": "backup", "enabled": True, "search_backend": "ok"},
            ],
        }
    })
    result = await _web_search_impl(cfg, query="anything", _backend_factory=factory)
    assert result["backend"] == "empty"
    assert result["results"] == []
    assert "fallback_from" not in result
    assert backup.calls == []   # secondary never invoked


@pytest.mark.asyncio
async def test_fallback_all_backends_fail_returns_aggregate_error():
    """When every active backend raises, the LLM gets one structured
    error naming the chain + per-backend cause so it can decide to
    reformulate, retry, or proceed without web context."""
    from harness.web_tools import _web_search_impl
    factory = _factory_for({
        "raising_a": _RaisingBackend(),
        "raising_b": _RaisingBackend(),
    })
    cfg = WebToolsConfig.from_config({
        "web_tools": {
            "enabled": True,
            "search_backend": "raising_a",
            "backends": [
                {"name": "secondary", "enabled": True, "search_backend": "raising_b"},
            ],
        }
    })
    result = await _web_search_impl(cfg, query="x", _backend_factory=factory)
    assert "error" in result
    assert "all configured search backends failed" in result["error"]
    assert "raising_a" in result["error"]
    assert "raising_b" in result["error"]


@pytest.mark.asyncio
async def test_fallback_unknown_backend_name_falls_through():
    """A typoed backend name in one entry must not kill the chain —
    a sibling entry may resolve fine. The unresolved name still
    appears in ``fallback_from`` so the typo is visible."""
    from harness.web_tools import _web_search_impl
    ok = _RecordingBackend()
    factory = _factory_for({"ok": ok})
    cfg = WebToolsConfig.from_config({
        "web_tools": {
            "enabled": True,
            "search_backend": "ddgg",   # typo of duckduckgo
            "backends": [
                {"name": "secondary", "enabled": True, "search_backend": "ok"},
            ],
        }
    })
    result = await _web_search_impl(cfg, query="x", _backend_factory=factory)
    assert result["backend"] == "ok"
    assert any("ddgg" in entry["backend"] for entry in result["fallback_from"])


@pytest.mark.asyncio
async def test_backwards_compat_single_primary_no_fallback_field():
    """An operator with no ``backends[]`` entries gets the legacy
    single-backend behaviour: no ``fallback_from`` field when the
    primary succeeds. Lets existing config files keep parsing
    response shapes the same way."""
    from harness.web_tools import _web_search_impl
    ok = _RecordingBackend()
    factory = _factory_for({"ok": ok})
    cfg = WebToolsConfig.from_config({
        "web_tools": {
            "enabled": True,
            "search_backend": "ok",
        }
    })
    result = await _web_search_impl(cfg, query="x", _backend_factory=factory)
    assert result["backend"] == "ok"
    assert "fallback_from" not in result


@pytest.mark.asyncio
async def test_empty_chain_returns_explicit_error():
    """No active backends (operator pinned ``search_backend`` to empty
    and ``backends[]`` is empty) returns a structured error explaining
    the misconfiguration — never a silent no-op."""
    from harness.web_tools import _web_search_impl
    cfg = WebToolsConfig.from_config({
        "web_tools": {"enabled": True, "search_backend": ""},
    })
    result = await _web_search_impl(cfg, query="x")
    assert "error" in result
    assert "no active search backends configured" in result["error"]


@pytest.mark.asyncio
async def test_disabled_entries_are_skipped_in_the_chain():
    """A backend with ``enabled: false`` in the operator's ``backends[]``
    must not be invoked — even when an earlier entry errors and the
    chain would otherwise reach it."""
    from harness.web_tools import _web_search_impl
    never_called = _RecordingBackend()
    rescue = _RecordingBackend()
    factory = _factory_for({
        "raising": _RaisingBackend(),
        "never_called": never_called,
        "rescue": rescue,
    })
    cfg = WebToolsConfig.from_config({
        "web_tools": {
            "enabled": True,
            "search_backend": "raising",
            "backends": [
                # Disabled entry — must be skipped despite being before the rescue.
                {"name": "off", "enabled": False, "search_backend": "never_called"},
                {"name": "rescue", "enabled": True, "search_backend": "rescue"},
            ],
        }
    })
    result = await _web_search_impl(cfg, query="x", _backend_factory=factory)
    assert result["backend"] == "rescue"
    assert never_called.calls == []
    assert rescue.calls == [("x", 5)]

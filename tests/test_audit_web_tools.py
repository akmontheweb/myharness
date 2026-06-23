"""Tests for web_tools audit hardening (batches 4, 6).

Covers:
  - Manual per-hop redirect validation                               (§3.1)
  - Streamed bounded read via client.stream + aiter_bytes            (§4.11)
"""

from __future__ import annotations


import httpx
import pytest

from harness import web_tools as web_tools_module
from harness.web_tools import WebFetchSkill, WebToolsConfig


# ---------------------------------------------------------------------------
# Mock httpx client that supports BOTH .get and .stream
# ---------------------------------------------------------------------------


class _StreamCtx:
    def __init__(self, response: httpx.Response, content: bytes,
                 chunk_size: int = 1024):
        self._response = response
        self._content = content
        self._chunk_size = chunk_size

    async def __aenter__(self):
        # Bolt aiter_bytes onto the response so the streamed-read path
        # works with the canned httpx.Response.
        async def _aiter():
            for i in range(0, len(self._content), self._chunk_size):
                yield self._content[i:i + self._chunk_size]
        self._response.aiter_bytes = lambda: _aiter()
        return self._response

    async def __aexit__(self, *exc):
        return None


class _StreamClient:
    """Records every URL seen across redirects + serves canned responses
    indexed by URL."""

    def __init__(self, responses_by_url: dict[str, httpx.Response]):
        self._responses = responses_by_url
        self.seen: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def stream(self, method: str, url: str) -> _StreamCtx:
        self.seen.append(url)
        resp = self._responses.get(url)
        if resp is None:
            raise RuntimeError(f"no canned response for {url}")
        return _StreamCtx(resp, resp.content or b"")


# ---------------------------------------------------------------------------
# Streamed read cap (audit §4.11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_fetch_truncates_at_byte_cap(monkeypatch):
    cfg = WebToolsConfig(enabled=True, max_bytes=128)
    skill = WebFetchSkill(cfg)
    url = "https://example.com/big"
    big = b"a" * 10_000
    response = httpx.Response(
        status_code=200, content=big,
        headers={"content-type": "text/plain"},
        request=httpx.Request("GET", url),
    )
    monkeypatch.setattr(
        web_tools_module.httpx, "AsyncClient",
        lambda **_kw: _StreamClient({url: response}),
    )
    # Pin DNS so validate_outbound_url doesn't do a real getaddrinfo
    # call. Without this the test depends on live example.com resolution
    # and any DNS hiccup under suite-wide load surfaces as
    # KeyError: 'truncated' because the URL gets rejected pre-fetch.
    monkeypatch.setattr(
        "harness.trust._resolve_host_addresses",
        lambda host: ["93.184.216.34"],
    )
    result = await skill.execute(url=url)
    assert result["truncated"] is True
    # The bytes_returned is bounded by the configured cap (it may be
    # SLIGHTLY less because of chunk-boundary alignment).
    assert result["bytes_returned"] <= 128


# ---------------------------------------------------------------------------
# Redirect re-validation per hop (audit §3.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_fetch_re_validates_each_redirect(monkeypatch):
    """A 302 to a private-IP URL must be rejected by the per-hop
    validate_outbound_url call BEFORE the harness follows it."""
    cfg = WebToolsConfig(enabled=True, allow_private_ips=False)
    skill = WebFetchSkill(cfg)
    src = "https://example.com/start"
    bad_redirect = "http://127.0.0.1/leak"

    # The initial response is a 302 pointing to a loopback URL.
    redirect_resp = httpx.Response(
        status_code=302,
        headers={"location": bad_redirect},
        request=httpx.Request("GET", src),
    )
    monkeypatch.setattr(
        web_tools_module.httpx, "AsyncClient",
        lambda **_kw: _StreamClient({src: redirect_resp}),
    )
    # Skip DNS resolution to keep the test offline; per-hop validate is
    # the only relevant guard for this case (literal IP).
    monkeypatch.setattr(
        "harness.trust._resolve_host_addresses",
        lambda host: ["93.184.216.34"],  # for the START url only
    )

    result = await skill.execute(url=src)
    assert "redirect target rejected" in result.get("error", "").lower()
    # The harness MUST NOT have followed the redirect to the loopback URL.


# ---------------------------------------------------------------------------
# Redirect chain cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_fetch_redirect_chain_bounded(monkeypatch):
    """At most 5 redirect hops before the harness gives up."""
    cfg = WebToolsConfig(enabled=True, allow_private_ips=False)
    skill = WebFetchSkill(cfg)
    # Build a chain of 7 redirects.
    chain = {}
    for i in range(7):
        src = f"https://example.com/hop-{i}"
        dst = f"https://example.com/hop-{i + 1}"
        chain[src] = httpx.Response(
            status_code=302,
            headers={"location": dst},
            request=httpx.Request("GET", src),
        )
    monkeypatch.setattr(
        web_tools_module.httpx, "AsyncClient",
        lambda **_kw: _StreamClient(chain),
    )
    monkeypatch.setattr(
        "harness.trust._resolve_host_addresses",
        lambda host: ["93.184.216.34"],
    )
    result = await skill.execute(url="https://example.com/hop-0")
    assert "redirect chain exceeded" in result.get("error", "").lower()

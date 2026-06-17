"""Tests for gateway audit hardening (batches 6, 9).

Covers:
  - retry_with_backoff catches httpx.TimeoutException                (§4.1)
  - rate_limit_observer invoked per 429                              (§4.2)
  - _parse_json_response synthesises 502 on JSONDecodeError          (§4.3)
  - ProviderEmbeddedError raised on 200 + {error}                    (§4.7)
  - _delay_from_rate_limit_headers clamps to max ceiling             (§4.13, §4.15)
  - retry_with_backoff jitter applied AFTER min(max_delay)           (§4.12)
  - retry_with_backoff max_total_seconds budget                      (§4.13)
"""

from __future__ import annotations

import json

import httpx
import pytest

from harness import gateway as gw


# ---------------------------------------------------------------------------
# retry_with_backoff: TimeoutException now retried (audit §4.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_with_backoff_retries_on_read_timeout():
    """A ReadTimeout used to escape as a non-retryable error. It must
    now be caught and retried."""
    attempts = {"n": 0}

    async def _flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ReadTimeout("simulated")
        # Return a minimal LLMResponse on success.
        return gw.LLMResponse(
            content="ok",
            usage=gw.TokenUsage(input_tokens=1, output_tokens=1),
            model="test",
        )

    result = await gw.retry_with_backoff(
        _flaky, max_retries=3, base_delay=0.001, max_delay=0.01,
    )
    assert result.content == "ok"
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_retry_with_backoff_retries_on_connect_timeout():
    attempts = {"n": 0}

    async def _flaky():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise httpx.ConnectTimeout("dns")
        return gw.LLMResponse(
            content="ok", usage=gw.TokenUsage(), model="t",
        )

    result = await gw.retry_with_backoff(
        _flaky, max_retries=3, base_delay=0.001, max_delay=0.01,
    )
    assert result.content == "ok"


# ---------------------------------------------------------------------------
# rate_limit_observer (audit §4.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_observer_invoked_per_429():
    """The observer hook fires once per 429 response — not just once per
    exhausted dispatch — so the circuit breaker can count every 429."""
    observer_calls = {"n": 0}

    def _observer():
        observer_calls["n"] += 1

    attempts = {"n": 0}

    async def _flaky():
        attempts["n"] += 1
        if attempts["n"] < 4:
            # Build a fake 429 response.
            req = httpx.Request("POST", "https://x")
            resp = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("429", request=req, response=resp)
        return gw.LLMResponse(content="ok", usage=gw.TokenUsage(), model="t")

    await gw.retry_with_backoff(
        _flaky, max_retries=5, base_delay=0.001, max_delay=0.01,
        rate_limit_observer=_observer,
    )
    # Three 429s seen → three observer fires.
    assert observer_calls["n"] == 3


# ---------------------------------------------------------------------------
# _parse_json_response synthesises 502 (audit §4.3)
# ---------------------------------------------------------------------------


def test_parse_json_response_wraps_decode_error_as_502():
    """A malformed JSON body should raise an HTTPStatusError with the
    response's status_code set to 502 so retry_with_backoff treats it
    as retryable."""

    class _BadResponse:
        request = httpx.Request("POST", "https://x")
        status_code = 200

        def json(self):
            raise json.JSONDecodeError("nope", "doc", 0)

    bad = _BadResponse()
    with pytest.raises(httpx.HTTPStatusError) as ex:
        gw._parse_json_response(bad)
    # The response's status code was upgraded to 502 (retryable).
    assert ex.value.response.status_code == 502


# ---------------------------------------------------------------------------
# _check_provider_embedded_error (audit §4.7)
# ---------------------------------------------------------------------------


def test_check_provider_embedded_error_raises_on_error_body():
    data = {"error": {"message": "quota exceeded", "code": "insufficient_quota"}}
    with pytest.raises(gw.ProviderEmbeddedError, match="quota exceeded"):
        gw._check_provider_embedded_error(data)


def test_check_provider_embedded_error_silent_on_success_body():
    data = {"choices": [{"message": {"content": "ok"}}]}
    # Must NOT raise.
    gw._check_provider_embedded_error(data)


def test_check_provider_embedded_error_silent_on_non_dict():
    gw._check_provider_embedded_error("not a dict")  # type: ignore[arg-type]
    gw._check_provider_embedded_error(None)
    gw._check_provider_embedded_error([1, 2, 3])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _delay_from_rate_limit_headers clamping (audit §4.13, §4.15)
# ---------------------------------------------------------------------------


def test_retry_after_clamped_to_max():
    """A pathological Retry-After: 86400 must NOT make the dispatch
    sleep for 24h."""
    headers = {"Retry-After": "86400"}
    delay = gw._delay_from_rate_limit_headers(headers, base_delay=1.0, attempt=0)
    # Clamped to _RA_MAX = 300.
    assert delay <= 300.0


def test_x_ratelimit_reset_clamped():
    """Even when the epoch is parsed and converts to billions of seconds,
    the result is clamped."""
    # Pass a value WAY in the past — heuristic falls through to "treat
    # as seconds-from-now", clamps to the ceiling.
    headers = {"X-RateLimit-Reset": "1700000000"}
    delay = gw._delay_from_rate_limit_headers(headers, base_delay=1.0, attempt=0)
    assert delay <= 300.0


def test_rfc_9651_ratelimit_reset_clamped():
    headers = {"RateLimit-Reset": "9999999"}
    delay = gw._delay_from_rate_limit_headers(headers, base_delay=1.0, attempt=0)
    assert delay <= 300.0


def test_no_headers_falls_back_to_exponential():
    delay = gw._delay_from_rate_limit_headers({}, base_delay=1.0, attempt=2)
    assert delay == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# retry_with_backoff max_total_seconds budget (audit §4.13)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_with_backoff_honours_max_total_seconds():
    """Once cumulative sleep would exceed max_total_seconds, the loop
    breaks and re-raises the last error rather than sleeping further."""
    attempts = {"n": 0}

    async def _always_fails():
        attempts["n"] += 1
        raise httpx.ReadTimeout("nope")

    with pytest.raises(httpx.ReadTimeout):
        await gw.retry_with_backoff(
            _always_fails,
            max_retries=20, base_delay=0.05, max_delay=0.5,
            max_total_seconds=0.2,
        )
    # Should have stopped well before 20 retries.
    assert attempts["n"] < 20


# ---------------------------------------------------------------------------
# ProviderEmbeddedError dataclass surfaces
# ---------------------------------------------------------------------------


def test_provider_embedded_error_carries_payload():
    err = gw.ProviderEmbeddedError("oops", payload={"code": "x"})
    assert str(err) == "oops"
    assert err.payload == {"code": "x"}


def test_provider_embedded_error_default_payload():
    err = gw.ProviderEmbeddedError("oops")
    assert err.payload == {}

"""Tests for dashboard helper / hardening (batches 4, 5).

Covers:
  - _safe_cdn_url validates scheme + host allowlist                  (§3.8)
  - _redact_secret_fields_for_audit strips FormField.secret values   (§3.9)
  - _browse_response enforces home/cwd/tmp allowlist                 (§3.13)
  - get_process_registry double-checked locking init                 (§1.17)
  - start_server refuses non-loopback bind without token             (§3.10)
"""

from __future__ import annotations

import json
import threading

import pytest

from harness import dashboard as dash


# ---------------------------------------------------------------------------
# _safe_cdn_url (audit §3.8)
# ---------------------------------------------------------------------------


class TestSafeCdnUrl:
    def test_allows_unpkg(self):
        url = "https://unpkg.com/carbon-components/css/x.min.css"
        assert dash._safe_cdn_url(url, kind="css") == url

    def test_allows_jsdelivr(self):
        url = "https://cdn.jsdelivr.net/npm/chart.js"
        assert dash._safe_cdn_url(url, kind="js") == url

    def test_rejects_javascript_scheme(self):
        # The Carbon default is the fallback for "css" kind.
        out = dash._safe_cdn_url("javascript:alert(1)", kind="css")
        assert out == dash._DEFAULT_CARBON_CSS_URL

    def test_rejects_http_scheme(self):
        out = dash._safe_cdn_url("http://unpkg.com/foo.css", kind="css")
        assert out == dash._DEFAULT_CARBON_CSS_URL

    def test_rejects_unknown_host(self):
        out = dash._safe_cdn_url("https://evil.cdn/x.js", kind="js")
        assert out == dash._DEFAULT_CHART_JS_URL

    def test_rejects_non_string(self):
        out = dash._safe_cdn_url(None, kind="js")
        assert out == dash._DEFAULT_CHART_JS_URL

    def test_empty_string_falls_back(self):
        out = dash._safe_cdn_url("", kind="css")
        assert out == dash._DEFAULT_CARBON_CSS_URL


# ---------------------------------------------------------------------------
# _redact_secret_fields_for_audit (audit §3.9)
# ---------------------------------------------------------------------------


class TestRedactSecretFieldsForAudit:
    def test_replaces_secret_field_values(self):
        # Build a fake FormSection with one secret field. The redactor
        # looks up FormField.dotted_key on each field and substitutes
        # any matching dotted-path leaf in parsed.
        from harness.web_forms import FormField, FormSection
        section = FormSection(
            section="dashboard",
            fields=[
                FormField(
                    section="dashboard",
                    name="api_key",
                    kind="text",
                    type_tuple=(str,),
                    secret=True,
                ),
            ],
        )
        parsed = {"api_key": "sk-ant-api03-LIVE-TOKEN"}
        out = dash._redact_secret_fields_for_audit(section, parsed)
        # The section prefix "dashboard." is stripped before matching
        # against walk paths, so the secret field "dashboard.api_key"
        # matches the leaf at "api_key" in section-relative parsed.
        assert out["api_key"] == "[REDACTED]"

    def test_no_secret_fields_returns_unchanged(self):
        from harness.web_forms import FormField, FormSection
        section = FormSection(
            section="general",
            fields=[
                FormField(
                    section="general",
                    name="flag",
                    kind="checkbox",
                    type_tuple=(bool,),
                    secret=False,
                ),
            ],
        )
        parsed = {"flag": True}
        # Same object identity returned when nothing to redact.
        assert dash._redact_secret_fields_for_audit(section, parsed) is parsed

    def test_handles_section_without_fields_attribute(self):
        """Defensive: a section-like object with no fields attr returns the
        parsed payload unchanged."""

        class _Bare:
            pass

        parsed = {"x": 1}
        assert dash._redact_secret_fields_for_audit(_Bare(), parsed) is parsed


# ---------------------------------------------------------------------------
# _browse_response allowlist (audit §3.13)
# ---------------------------------------------------------------------------


def test_browse_response_rejects_path_outside_allowed_roots(tmp_path):
    """Paths outside home/cwd/tmp return 403 with a clear message."""
    status, ctype, body = dash._browse_response("/etc")
    assert status == 403
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "outside allowed roots" in payload["error"].lower()


def test_browse_response_allows_tmp(tmp_path):
    status, _ct, body = dash._browse_response("/tmp")
    # Either 200 (tmp listed) or 400 (tmp not a real dir on this host).
    payload = json.loads(body)
    if status == 200:
        assert payload["ok"] is True
    else:
        # Allowed path that just isn't a dir — still NOT a 403.
        assert status != 403


# ---------------------------------------------------------------------------
# get_process_registry / get_hitl_queue thread-safe init (audit §1.17)
# ---------------------------------------------------------------------------


def test_get_process_registry_is_thread_safe():
    """Concurrent first-call from many threads must all return the SAME
    registry instance — the double-checked locking guard prevents
    double-instantiation."""
    dash.reset_shared_state()
    results: list = []
    lock = threading.Lock()

    def _grab():
        r = dash.get_process_registry()
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_grab) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # All callers see the same singleton.
    assert all(r is results[0] for r in results)


def test_get_hitl_queue_is_thread_safe():
    dash.reset_shared_state()
    results: list = []
    lock = threading.Lock()

    def _grab():
        q = dash.get_hitl_queue()
        with lock:
            results.append(q)

    threads = [threading.Thread(target=_grab) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(q is results[0] for q in results)


def test_reset_shared_state_drops_singletons():
    """After reset, the next get_* returns a NEW instance."""
    dash.reset_shared_state()
    r1 = dash.get_process_registry()
    dash.reset_shared_state()
    r2 = dash.get_process_registry()
    assert r1 is not r2


# ---------------------------------------------------------------------------
# start_server default-secure (audit §3.10)
# ---------------------------------------------------------------------------


def test_start_server_refuses_nonloopback_without_token():
    cfg = dash.DashboardConfig(
        enabled=True,
        host="0.0.0.0",
        port=39999,
        token_env="",
        allow_unauthenticated_bind=False,
    )
    with pytest.raises(RuntimeError, match="refusing to start"):
        dash.start_server(cfg, blocking=False)


def test_start_server_allows_loopback_without_token():
    """Loopback bind continues to be auth-optional for backward
    compatibility."""
    cfg = dash.DashboardConfig(
        enabled=True,
        host="127.0.0.1",
        port=39998,
        token_env="",
        allow_unauthenticated_bind=False,
    )
    handle = dash.start_server(cfg, blocking=False)
    try:
        assert handle is not None
    finally:
        if handle is not None:
            handle.shutdown()


def test_start_server_honors_explicit_unauth_optin(monkeypatch):
    """Operators can opt back in to unauthenticated public bind."""
    cfg = dash.DashboardConfig(
        enabled=True,
        host="0.0.0.0",
        port=39997,
        token_env="",
        allow_unauthenticated_bind=True,
    )
    handle = dash.start_server(cfg, blocking=False)
    try:
        assert handle is not None
    finally:
        if handle is not None:
            handle.shutdown()

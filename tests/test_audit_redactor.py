"""Tests for redactor audit hardening (batches 1, 4).

Covers:
  - New secret patterns: npm, slack webhook, GCP service-account,
    Azure storage key, Discord bot token, private-key with \\r\\n     (§3.7)
  - Anthropic typed-content list redaction (tool_use/tool_result)    (§3.7)
"""

from __future__ import annotations

import pytest

from harness.redactor import SecretScanner


@pytest.fixture
def scanner():
    return SecretScanner(mode="hash")


# ---------------------------------------------------------------------------
# New patterns (audit §3.7)
# ---------------------------------------------------------------------------


def test_redacts_npm_token(scanner):
    raw = "publish auth: npm_" + ("X" * 36)
    out, result = scanner.redact_text(raw)
    assert "npm_" + ("X" * 36) not in out
    assert result.replacements >= 1


def test_redacts_slack_webhook_url(scanner):
    raw = "Send to https://hooks.slack.com/services/T01ABC/B02DEF/abc123XYZ now"
    out, _ = scanner.redact_text(raw)
    assert "hooks.slack.com/services/T01ABC/B02DEF/abc123XYZ" not in out


def test_redacts_gcp_service_account_json(scanner):
    raw = '''{"type": "service_account", "project_id": "p", "private_key_id": "x", "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvQ\\n-----END PRIVATE KEY-----\\n"}'''
    out, result = scanner.redact_text(raw)
    # The GCP block as a whole should be redacted.
    assert "service_account" not in out or result.replacements >= 1


def test_redacts_azure_account_key(scanner):
    raw = "DefaultEndpointsProtocol=https;AccountName=foo;AccountKey=" + ("A" * 32) + ";EndpointSuffix=core.windows.net"
    out, _ = scanner.redact_text(raw)
    assert "AccountKey=" + ("A" * 32) not in out


def test_redacts_discord_bot_token(scanner):
    raw = "Bot token: MTAxAAAAAAAAAAAAAAAAAAA.abc123.aaaaaaaaaaaaaaaaaaaaaaaaaaa rest"
    out, _ = scanner.redact_text(raw)
    assert "MTAxAAAAAAAAAAAAAAAAAAA.abc123" not in out


def test_private_key_pattern_tolerates_crlf_line_endings(scanner):
    raw = (
        "-----BEGIN RSA PRIVATE KEY-----\r\n"
        + "A" * 60 + "\r\n"
        + "-----END RSA PRIVATE KEY-----\r\n"
    )
    out, result = scanner.redact_text(raw)
    # The captured block should not survive verbatim.
    assert result.replacements >= 1


def test_private_key_pattern_tolerates_inlined_backslash_n(scanner):
    raw = (
        '"key": "-----BEGIN RSA PRIVATE KEY-----\\n'
        + "B" * 60
        + '\\n-----END RSA PRIVATE KEY-----\\n"'
    )
    out, result = scanner.redact_text(raw)
    assert result.replacements >= 1


# ---------------------------------------------------------------------------
# Anthropic typed-content list redaction (audit §3.7)
# ---------------------------------------------------------------------------


def test_redact_messages_handles_typed_content_list(scanner):
    """Anthropic-style message: content is a LIST of typed blocks.
    The redactor must recurse into each block and redact any string
    leaf — the earlier implementation skipped non-string content
    entirely, so secrets in tool_result.content shipped to the LLM."""
    secret_token = "sk-ant-api03-" + "z" * 50
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "intro line"},
                {
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "content": f"API replied: {secret_token} done",
                },
            ],
        }
    ]
    redacted, result = scanner.redact_messages(messages)
    payload_block = redacted[0]["content"][1]
    assert secret_token not in payload_block["content"]
    assert result.replacements >= 1


def test_redact_messages_recurses_into_tool_use_input(scanner):
    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu1",
                    "name": "execute",
                    "input": {"command": "echo sk-ant-api03-" + "z" * 50},
                },
            ],
        }
    ]
    redacted, _ = scanner.redact_messages(messages)
    cmd = redacted[0]["content"][0]["input"]["command"]
    assert "sk-ant-api03-" + "z" * 50 not in cmd


def test_redact_messages_preserves_string_content(scanner):
    """Plain string-content messages still flow through the str path."""
    messages = [{"role": "user", "content": "harmless body"}]
    redacted, _ = scanner.redact_messages(messages)
    assert redacted[0]["content"] == "harmless body"


def test_redact_messages_passthrough_non_dict_block(scanner):
    """Non-dict items inside a typed-content list pass through untouched
    (unless they're strings, which get redacted)."""
    messages = [{"role": "user", "content": ["bare string", 42, None]}]
    redacted, _ = scanner.redact_messages(messages)
    out_content = redacted[0]["content"]
    assert out_content[0] == "bare string"  # no secret pattern matched
    assert out_content[1] == 42
    assert out_content[2] is None

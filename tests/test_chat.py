"""Regression tests for the chat REPL (#8).

The REPL is decoupled from real stdin/stdout via the ``reader`` /
``writer`` injection points on :class:`ChatSession`. Each test feeds
a scripted sequence of lines and inspects the captured output.

The gateway is stubbed so we never make a network call.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.chat import run_chat
from harness.gateway import LLMResponse, ModelSpec, TokenUsage, register_model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StubGateway:
    """Minimal gateway double — scripted dispatch responses, captured
    message lists per call, no HTTP."""

    def __init__(self, responses: list[str]):
        self._scripted = list(responses)
        self._idx = 0
        self.calls: list[list[dict[str, Any]]] = []

    async def dispatch(
        self, *, messages, role, budget_remaining_usd, **_kwargs
    ):
        self.calls.append(list(messages))
        text = self._scripted[min(self._idx, len(self._scripted) - 1)]
        self._idx += 1
        usage = TokenUsage(
            input_tokens=10, output_tokens=5,
            model_name="stub:chat", cost_usd=0.001,
        )
        return (
            LLMResponse(content=text, usage=usage, model="stub:chat"),
            max(0.0, budget_remaining_usd - 0.001),
        )

    async def close(self) -> None:
        return None


def _scripted_reader(lines: list[str]):
    """Returns a reader callable that yields ``lines`` in order, then
    raises ``EOFError`` so the REPL exits cleanly."""
    state = {"i": 0}

    def _read(prompt: str = "") -> str:  # noqa: ARG001
        i = state["i"]
        if i >= len(lines):
            raise EOFError
        state["i"] = i + 1
        return lines[i]

    return _read


def _capturing_writer():
    out: list[str] = []

    def _write(text: str = "") -> None:
        out.append(str(text))

    return out, _write


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_exits_cleanly_on_eof(tmp_path):
    gw = _StubGateway(["ignored"])
    out, write = _capturing_writer()
    rc = await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=1.0,
        reader=_scripted_reader([]),  # EOF immediately
        writer=write,
    )
    assert rc == 0
    assert any("harness chat" in line for line in out)


@pytest.mark.asyncio
async def test_chat_routes_user_text_through_gateway(tmp_path):
    gw = _StubGateway(["Hello! I am an llm."])
    out, write = _capturing_writer()
    rc = await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=1.0,
        reader=_scripted_reader(["hello"]),
        writer=write,
    )
    assert rc == 0
    # The stubbed reply must surface in the writer's output.
    joined = "\n".join(out)
    assert "Hello! I am an llm." in joined
    # Gateway saw exactly one dispatch.
    assert len(gw.calls) == 1


@pytest.mark.asyncio
async def test_chat_handles_exit_command(tmp_path):
    gw = _StubGateway(["ignored"])
    out, write = _capturing_writer()
    await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=1.0,
        reader=_scripted_reader(["/exit"]),
        writer=write,
    )
    assert any("bye" in line.lower() for line in out)
    # /exit must not have triggered a gateway dispatch.
    assert gw.calls == []


@pytest.mark.asyncio
async def test_chat_help_command_prints_usage(tmp_path):
    gw = _StubGateway(["ignored"])
    out, write = _capturing_writer()
    await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=1.0,
        reader=_scripted_reader(["/help"]),
        writer=write,
    )
    joined = "\n".join(out)
    assert "/exit" in joined
    assert "/apply" in joined
    assert "/build" in joined


@pytest.mark.asyncio
async def test_chat_clear_resets_conversation_keeps_system(tmp_path):
    gw = _StubGateway(["one", "two"])
    out, write = _capturing_writer()
    await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=1.0,
        reader=_scripted_reader(["first message", "/clear", "second message"]),
        writer=write,
    )
    # /clear should not produce a gateway dispatch by itself.
    # Two text turns → two dispatches.
    assert len(gw.calls) == 2
    # On the SECOND dispatch the conversation should NOT contain "first message".
    second_call = gw.calls[1]
    flat = "\n".join(str(m.get("content", "")) for m in second_call)
    assert "first message" not in flat
    assert "second message" in flat


@pytest.mark.asyncio
async def test_chat_budget_command_reports_state(tmp_path):
    gw = _StubGateway(["ignored"])
    out, write = _capturing_writer()
    await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=2.5,
        reader=_scripted_reader(["/budget"]),
        writer=write,
    )
    joined = "\n".join(out)
    assert "$2.5" in joined


@pytest.mark.asyncio
async def test_chat_unknown_command_falls_through(tmp_path):
    gw = _StubGateway(["ignored"])
    out, write = _capturing_writer()
    await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=1.0,
        reader=_scripted_reader(["/wat"]),
        writer=write,
    )
    joined = "\n".join(out)
    assert "unknown" in joined.lower() and "/wat" in joined


@pytest.mark.asyncio
async def test_chat_budget_exhausted_blocks_further_dispatch(tmp_path):
    register_model("stub:chat", ModelSpec(
        provider="stub", model_id="chat", context_window=8000,
        input_cost_per_1m=0.5, output_cost_per_1m=1.0,
        api_base_url="", api_key="x",
    ))
    gw = _StubGateway(["one"])
    out, write = _capturing_writer()
    await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=0.0,
        reader=_scripted_reader(["hello"]),
        writer=write,
    )
    joined = "\n".join(out)
    assert "budget exhausted" in joined.lower()
    assert gw.calls == []  # never dispatched


@pytest.mark.asyncio
async def test_chat_save_writes_transcript(tmp_path):
    gw = _StubGateway(["something assistant said"])
    out, write = _capturing_writer()
    dest = tmp_path / "transcript.md"
    await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=1.0,
        reader=_scripted_reader(["a user message", f"/save {dest}"]),
        writer=write,
    )
    assert dest.is_file()
    content = dest.read_text(encoding="utf-8")
    assert "a user message" in content
    assert "something assistant said" in content


@pytest.mark.asyncio
async def test_chat_files_command_lists_empty(tmp_path):
    gw = _StubGateway(["ignored"])
    out, write = _capturing_writer()
    await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=1.0,
        reader=_scripted_reader(["/files"]),
        writer=write,
    )
    joined = "\n".join(out)
    assert "no files modified" in joined.lower()


@pytest.mark.asyncio
async def test_chat_apply_no_assistant_reply_yet(tmp_path):
    gw = _StubGateway(["ignored"])
    out, write = _capturing_writer()
    await run_chat(
        workspace_path=str(tmp_path),
        gateway=gw,
        config={},
        initial_budget_usd=1.0,
        reader=_scripted_reader(["/apply"]),
        writer=write,
    )
    joined = "\n".join(out)
    assert "no assistant reply" in joined.lower()

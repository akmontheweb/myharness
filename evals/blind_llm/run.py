#!/usr/bin/env python3
"""Blind-LLM regression rig (Phase 2.3).

Takes a saved repair prompt that previously HITL'd in a real session and
asks a fresh LLM — with no other context, no session history, no tools —
the single question:

    "Based solely on this prompt, can you identify and describe the bug?"

If the fresh model can locate the bug from the prompt alone, the
harness's prompt-shaping pipeline preserved enough signal. If it can't,
the prompt-shaping ate the signal — a heuristic dropped something
load-bearing or a deferral mechanism deprioritised the real blocker.

The point is to catch regressions in the cascade-defense layers (and any
future filtering / truncation heuristics): land a code change, re-run
the rig, see whether any fixture flips from PASS to FAIL.

Usage:
    python -m evals.blind_llm.run                    # all fixtures
    python -m evals.blind_llm.run --case ts2769_overload   # single
    python -m evals.blind_llm.run --model openai:gpt-4o-mini

Fixture layout (under ``evals/blind_llm/fixtures/<case>/``):
    prompt.txt        — the exact text shown to the failing-session LLM
    expected_bug.txt  — a short reference description of the bug
    rubric.txt        — (optional) one-line directive for the judge
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclasses.dataclass
class CaseResult:
    case: str
    success: bool
    judge_verdict: str
    diagnosis_excerpt: str
    error: Optional[str] = None


_BLIND_USER_PROMPT_TEMPLATE = (
    "You are a software engineer auditing a build-repair prompt. The "
    "prompt below is exactly what a coding LLM saw mid-session when "
    "the build was failing — no session history, no other context, no "
    "tools. Your job is NOT to fix the bug. Your job is to tell me, "
    "from the prompt alone, whether a competent engineer could "
    "locate the bug.\n\n"
    "Respond with STRICT JSON ONLY — no prose, no markdown, no code "
    "fences. Shape:\n"
    '{"can_locate_bug": true | false, '
    '"diagnosis": "<one-sentence description of where you think the '
    "bug is (file:line:symbol) or why you cannot locate it>\"}\n\n"
    "==== BEGIN PROMPT ====\n"
    "{prompt_body}\n"
    "==== END PROMPT ===="
)


_JUDGE_PROMPT_TEMPLATE = (
    "You are grading whether a blind-LLM diagnosis matches the known "
    "bug for this fixture. Be lenient: accept any diagnosis that "
    "identifies the right file AND points at the right symptom (file "
    "alone is not enough; symptom alone is not enough). The blind "
    "LLM only saw the prompt — it didn't have to write the fix.\n\n"
    "Known bug:\n{expected_bug}\n\n"
    "Blind-LLM diagnosis:\n{diagnosis}\n\n"
    "Rubric override (case-specific, optional):\n{rubric}\n\n"
    "Respond with STRICT JSON ONLY:\n"
    '{"match": true | false, "reasoning": "<one short sentence>"}\n'
)


def _list_cases() -> list[Path]:
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(
        d for d in FIXTURES_DIR.iterdir()
        if d.is_dir() and (d / "prompt.txt").is_file()
    )


def _load_case(case_dir: Path) -> dict[str, str]:
    prompt = (case_dir / "prompt.txt").read_text(encoding="utf-8")
    expected = (case_dir / "expected_bug.txt").read_text(encoding="utf-8")
    rubric_path = case_dir / "rubric.txt"
    rubric = (
        rubric_path.read_text(encoding="utf-8").strip()
        if rubric_path.is_file() else "(none)"
    )
    return {
        "prompt": prompt,
        "expected_bug": expected.strip(),
        "rubric": rubric,
    }


async def _dispatch_one(
    *,
    prompt: str,
    model_override: Optional[str],
    role: str,
    budget_usd: float,
) -> tuple[str, float]:
    """Single LLM dispatch via the harness gateway. Returns
    ``(response_text, remaining_budget_usd)``. Reuses the configured
    repair-role routing unless ``model_override`` is given (in which
    case the gateway is told to use that model id)."""
    # Late imports so this script works without the harness fully
    # initialised when invoked standalone.
    from harness.gateway import Gateway, NodeRole
    from harness.cli import _strip_comments, load_raw_config

    config = _strip_comments(load_raw_config())
    gw_config = Gateway.config_from_dict(config)
    if model_override:
        # Quick override knob for ablations — point the repair role at
        # the model we want to grade with.
        gw_config.repair_primary = model_override
    gw = Gateway(gw_config)
    role_enum = NodeRole.REPAIR if role == "repair" else NodeRole.REPAIR
    messages = [{"role": "user", "content": prompt}]
    response, new_budget = await gw.dispatch(
        messages=messages,
        role=role_enum,
        budget_remaining_usd=budget_usd,
    )
    return (response.content or "").strip(), new_budget


def _parse_json_response(raw: str) -> dict:
    """Tolerant strict-JSON parser. Strips markdown fences if present."""
    text = (raw or "").strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {}


async def _run_case(
    case_dir: Path,
    *,
    model_override: Optional[str],
    budget_usd: float,
) -> CaseResult:
    name = case_dir.name
    fixture = _load_case(case_dir)
    # Step 1: blind LLM read the prompt and diagnoses.
    blind_user = _BLIND_USER_PROMPT_TEMPLATE.replace(
        "{prompt_body}", fixture["prompt"],
    )
    try:
        raw, budget_usd = await _dispatch_one(
            prompt=blind_user,
            model_override=model_override,
            role="repair",
            budget_usd=budget_usd,
        )
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            case=name, success=False,
            judge_verdict="error", diagnosis_excerpt="",
            error=f"blind dispatch failed: {exc}",
        )
    parsed_blind = _parse_json_response(raw)
    diagnosis = str(parsed_blind.get("diagnosis", "")).strip()
    if not diagnosis:
        return CaseResult(
            case=name, success=False,
            judge_verdict="malformed_blind_response",
            diagnosis_excerpt=raw[:200],
            error="blind LLM did not return a parseable diagnosis",
        )
    # Step 2: judge LLM grades the diagnosis against the expected bug.
    judge_user = _JUDGE_PROMPT_TEMPLATE.format(
        expected_bug=fixture["expected_bug"],
        diagnosis=diagnosis,
        rubric=fixture["rubric"],
    )
    try:
        raw_judge, _ = await _dispatch_one(
            prompt=judge_user,
            model_override=model_override,
            role="repair",
            budget_usd=budget_usd,
        )
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            case=name, success=False,
            judge_verdict="error", diagnosis_excerpt=diagnosis[:200],
            error=f"judge dispatch failed: {exc}",
        )
    parsed_judge = _parse_json_response(raw_judge)
    matched = bool(parsed_judge.get("match", False))
    return CaseResult(
        case=name, success=matched,
        judge_verdict="match" if matched else "no_match",
        diagnosis_excerpt=diagnosis[:200],
    )


async def _main_async(args: argparse.Namespace) -> int:
    cases = _list_cases()
    if args.case:
        cases = [d for d in cases if d.name == args.case]
        if not cases:
            print(f"error: no fixture named {args.case!r} under {FIXTURES_DIR}",
                  file=sys.stderr)
            return 2
    if not cases:
        print(
            f"warning: no fixtures found under {FIXTURES_DIR}. "
            "Seed one with prompt.txt + expected_bug.txt to start.",
            file=sys.stderr,
        )
        return 0
    results: list[CaseResult] = []
    start = time.monotonic()
    for case_dir in cases:
        result = await _run_case(
            case_dir,
            model_override=args.model,
            budget_usd=float(args.budget),
        )
        results.append(result)
        status = "PASS" if result.success else "FAIL"
        print(f"  [{status}] {result.case}: {result.diagnosis_excerpt}")
        if result.error:
            print(f"         ERROR: {result.error}")
    elapsed = time.monotonic() - start
    n_pass = sum(1 for r in results if r.success)
    print(f"\n{n_pass}/{len(results)} fixtures passed in {elapsed:.1f}s.")
    out_path = Path(args.output) if args.output else None
    if out_path:
        out_path.write_text(
            json.dumps([dataclasses.asdict(r) for r in results], indent=2),
            encoding="utf-8",
        )
        print(f"results written to {out_path}")
    return 0 if n_pass == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        help="Run a single fixture by directory name (e.g. ts2769_overload).",
    )
    parser.add_argument(
        "--model",
        help="Override the repair-role model (e.g. openai:gpt-4o-mini).",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=1.0,
        help="Per-run dollar budget cap (default: 1.0).",
    )
    parser.add_argument(
        "--output",
        help="If given, write per-case results to this JSON file.",
    )
    args = parser.parse_args()
    # Make the harness importable when invoked from the repo root.
    sys.path.insert(0, str(REPO_ROOT))
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())

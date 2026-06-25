# Blind-LLM regression rig (Phase 2.3)

Pin the harness's prompt-shaping pipeline against unintended signal loss.

For each fixture, a fresh LLM (no session history, no tools, no context
beyond the prompt) is asked: *"Based solely on this prompt, can you
locate the bug?"* A second LLM grades the diagnosis against the known
expected bug. If the cascade-defense layers ever regress, fixtures
flip from PASS to FAIL.

## Run

    python -m evals.blind_llm.run                                 # all fixtures
    python -m evals.blind_llm.run --case ts2769_overload_after_fix  # one fixture
    python -m evals.blind_llm.run --model openai:gpt-4o-mini        # ablate

Output is PASS/FAIL per fixture and exit code 0 iff every fixture passed.
Pass `--output results.json` to write a structured per-case record.

## Add a fixture

Create a new directory under `evals/blind_llm/fixtures/<name>/` with:

* `prompt.txt` — the exact text the failing-session LLM saw
* `expected_bug.txt` — short reference description of the actual bug
* `rubric.txt` *(optional)* — case-specific grading directive

Drop a real repair prompt by copying the `## input message N: role=user`
block from a debug dump under `~/.harness/debug/<session>_NNNN_repair_*.txt`.

## Seeded fixtures

* **ts2769_overload_before_fix** — the cascade-deferral pathology that
  triggered the Phase 1 fix. Expected to FAIL on a model that takes the
  prompt at face value (the real blocker is shown only in the deferred
  tail). Useful as a counter-example: confirms the rig can detect
  prompt-shaping starvation.

* **ts2769_overload_after_fix** — same diagnostics, reformatted by the
  cascade-defense layers (survival promotion + small-N short-circuit +
  full semantic_context for TS2769). Expected to PASS.

A PASS/FAIL split between the two fixtures is the empirical evidence
that the layered fix actually helps — not a unit-test assertion, but a
real LLM ablation.

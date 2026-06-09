---
applies_to: [python]
---

## Python Test Generation Guide

Write pytest-style unit tests for the Python source files just modified. Tests exercise the **real implementation** with realistic inputs — **do not write mock objects**. When a side effect is genuinely impractical to invoke directly (filesystem outside `tmp_path`, environment variables, system clock, network), use pytest's built-in fixtures instead of inventing a mock framework.

### File placement
- Project root tests directory is `tests/`. If a package layout uses `src/<pkg>/`, mirror it as `tests/<pkg>/`.
- One test file per source file, named `test_<module>.py`.

### Structure
- Group cases into classes named `Test<Symbol>` so failures surface in a readable hierarchy.
- Function names: `test_<behavior_under_test>` — describe the behaviour, not the input.
- Use `pytest.mark.parametrize` when a single behaviour is exercised across multiple inputs; one test function with N IDs beats N near-identical functions.

### Fixtures the test runner already provides — use these instead of mocks
- `tmp_path` — `pathlib.Path` scoped to the test; use for any filesystem read/write.
- `tmp_path_factory` — session-scoped equivalent.
- `monkeypatch` — set/unset environment variables, `setattr` on attributes, `chdir`.
- `capsys` / `capfd` — capture stdout/stderr to assert on output.
- `caplog` — capture log records; `caplog.records` for structured assertions.

### Style
- `assert` statements only — no `unittest.TestCase` boilerplate.
- One assertion per outcome; many assertions in one test is fine as long as they describe one behaviour.
- For exceptions: `with pytest.raises(ValueError, match="..."):` — match the message so a renamed exception doesn't pass silently.
- For floats: `pytest.approx(expected, rel=1e-6)`.
- For collections: `assert result == expected` — never check `len` and a sample element separately when an equality check works.

### What NOT to do
- Do not use `unittest.mock.patch`, `Mock`, `MagicMock`, `mocker.patch`, or `pytest-mock`. The tests must call the real function.
- Do not stub HTTP — if the code under test makes network calls, the test runner uses a local fake server (e.g., `http.server` in a thread) or marks the test `pytest.mark.network` and the harness skips it deterministically.
- Do not import the production code under a fake name; use the actual import path.

### Minimal example
```python
import pytest
from mypkg.calculator import divide

class TestDivide:
    def test_returns_quotient_for_integers(self):
        assert divide(10, 2) == 5

    def test_raises_on_zero_divisor(self):
        with pytest.raises(ZeroDivisionError, match="cannot divide by zero"):
            divide(1, 0)

    @pytest.mark.parametrize("a,b,expected", [(0, 1, 0), (-4, 2, -2), (7, 7, 1)])
    def test_table(self, a, b, expected):
        assert divide(a, b) == expected
```

---
applies_to: [python]
---

## Build — Python Makefile

### When this skill applies
The workspace is a Python project — detected via `requirements.txt`, `pyproject.toml`, `setup.py`, or any top-level `.py` file. Applies to FastAPI, Django, Flask, library, and CLI projects alike. The harness runs `make build` by default; without a Makefile it falls back to noisy command-adaptation logic that's harder to reproduce locally.

### Always emit a `Makefile` in your first patch
Pick the variant matching the dependency manifest you're also creating (or that already exists). Each variant has separate `build:` and `test:` targets plus a `.PHONY:` line, so operators can run `make test` independently.

**With `requirements.txt`:**
```make
.PHONY: build test all clean

build:
	python3 -m pip install -r requirements.txt

test:
	python3 -m pytest -q

all: build test

clean:
	rm -rf __pycache__ .pytest_cache build dist *.egg-info
```

**With `pyproject.toml`** (editable install — covers Poetry, setuptools, hatch, PDM):
```make
.PHONY: build test all clean

build:
	python3 -m pip install -e .

test:
	python3 -m pytest -q

all: build test

clean:
	rm -rf __pycache__ .pytest_cache build dist *.egg-info
```

**Bare workspace** (no manifest yet — only when you also can't create one):
```make
.PHONY: build test all

build:
	python3 -m pip install pytest

test:
	python3 -m pytest -q

all: build test
```

### Conventions to follow
- Use TAB indentation inside recipes — Make rejects spaces with `*** missing separator. Stop.`
- The `build:` target installs dependencies and nothing else. Don't run tests from `build:` — that's what `test:` is for.
- Declare every target in `.PHONY:` so file-name collisions (`build/` dir, `test/` dir) don't suppress execution.
- Don't shell-pipe `&&` across recipe lines — each recipe line runs in its own subshell. Either keep both commands on one line with `&&`, or split into separate targets.

### Common patches the LLM gets wrong
- Using spaces instead of tabs for recipe indentation (silent fail).
- Calling `pip install` without `python3 -m` prefix — picks up the wrong interpreter when multiple Pythons are installed.
- Mixing `pytest` and `python -m pytest` across targets — pick one (prefer `python3 -m pytest` so the import path matches `build:`).
- Forgetting `.PHONY:` and then debugging why `make test` skipped when a `test/` directory exists.
- Hard-coding a virtualenv path (`venv/bin/pip`) — the harness runs inside a clean Docker container; venvs aren't needed.

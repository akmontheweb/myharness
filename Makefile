# Auto-generated Makefile by harness
# Detected project type: Python

.PHONY: build clean test hooks-install release

build:
	python -m compileall . 2>/dev/null || python3 -m compileall . 2>/dev/null || echo 'Python compile check skipped'

clean:
	@echo "No clean target configured."

test:
	python -m pytest tests/ -q --tb=short

hooks-install:
	python -m pre_commit install
	@echo "pre-commit hook installed. Tests will run before every commit."

# Cut a release: verify clean tree, run tests, bump version, update CHANGELOG,
# tag, and push. Usage:
#     make release BUMP=patch    # 1.1.0 -> 1.1.1 (default)
#     make release BUMP=minor    # 1.1.0 -> 1.2.0
#     make release BUMP=major    # 1.1.0 -> 2.0.0
#
# Prompts for confirmation before tagging. Refuses to release with a
# dirty working tree, with a failing test pack, or with no [Unreleased]
# content in CHANGELOG.md.
BUMP ?= patch
release:
	@python scripts/release.py --bump=$(BUMP)

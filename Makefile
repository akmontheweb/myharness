# Auto-generated Makefile by harness
# Detected project type: Python

.PHONY: build clean

build:
	python -m compileall . 2>/dev/null || python3 -m compileall . 2>/dev/null || echo 'Python compile check skipped'

clean:
	@echo "No clean target configured."

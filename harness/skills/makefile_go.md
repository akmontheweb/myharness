---
applies_to: [go]
---

## Build — Go Makefile

### When this skill applies
The workspace has `go.mod` at the root. Source typically lives under `cmd/<binary>/main.go` (multi-binary) or `main.go` at the root (single-binary). The harness runs `make build` by default; without a Makefile it falls back to noisy command-adaptation logic.

### Always emit a `Makefile` in your first patch

```make
.PHONY: build test all vet clean

build:
	go build ./...

test:
	go test ./...

vet:
	go vet ./...

all: build test

clean:
	go clean ./...
	rm -rf bin/
```

For projects that produce a single binary, optionally add:
```make
bin/myapp: $(shell find . -name '*.go')
	go build -o bin/myapp ./cmd/myapp
```

### Conventions to follow
- Use TAB indentation inside recipes.
- `./...` is the Go convention for "this module and every sub-package" — use it everywhere except when targeting a specific binary path.
- `go build` without `-o` puts the artifact alongside the source; use `-o bin/<name>` to keep the workspace clean.
- Add `vet:` as a separate target — `go vet` catches a different class of bugs than `go test` and is faster.
- Don't add `go mod tidy` to `build:` — it mutates `go.sum` based on the current dep state and is best run as an explicit pre-commit step.

### Common patches the LLM gets wrong
- Spaces instead of tabs for recipe indentation (silent fail).
- Forgetting `./...` and only building the root package — sub-packages silently uncompiled.
- `go build` instead of `go build ./...` — same trap.
- Adding `go install` to a build target (installs into `$GOPATH/bin`, not the workspace).
- Conflating `go test` with `go test -race ./...` — the race detector ~2× slows tests; keep it in a separate `race:` target if needed.

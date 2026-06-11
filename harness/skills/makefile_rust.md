---
applies_to: [rust]
---

## Build — Rust Makefile

### When this skill applies
The workspace has `Cargo.toml` at the root (single crate) or with a `[workspace]` section listing member crates. The harness runs `make build` by default; without a Makefile it falls back to noisy command-adaptation logic.

### Always emit a `Makefile` in your first patch

```make
.PHONY: build test all check clean

build:
	cargo build

test:
	cargo test

check:
	cargo check
	cargo clippy --all-targets --all-features -- -D warnings

all: build test

clean:
	cargo clean
```

For workspace projects, `cargo build` / `cargo test` already recurse into all members — no `--workspace` flag needed (it's the default when run from the workspace root).

For release builds (rare in the harness, but worth knowing), add:
```make
release:
	cargo build --release
```

### Conventions to follow
- Use TAB indentation inside recipes.
- `cargo` commands respect `Cargo.lock` — if one exists, deps are pinned; if not, `cargo build` generates it. Either case is fine.
- Don't `cargo update` from a build target — that mutates the lockfile and breaks reproducibility. Keep updates as an explicit, manual step.
- Add `check:` (compile-only + clippy) as a fast-feedback target separate from `build:` (which downloads and compiles everything).

### Common patches the LLM gets wrong
- Spaces instead of tabs for recipe indentation (silent fail).
- Adding `cargo fmt --check` to `build:` without ensuring `rustfmt` is in the toolchain — the harness's `rust:1.79-slim` image includes it, but custom images may not.
- Conflating `cargo build` and `cargo run` — `make build` should compile only, not execute.
- Using `cargo install` from a Makefile target (installs into `~/.cargo/bin`, not the workspace) — not what you want for build orchestration.

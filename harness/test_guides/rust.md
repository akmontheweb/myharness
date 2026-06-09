---
applies_to: [rust]
---

## Rust Test Generation Guide

Write standard Rust tests for the source files just modified. Use `#[test]` and `cargo test` only. **Do not write mocks** (no `mockall`, no `faux`, no manual trait impls created only for tests). Test the real implementation.

### File placement
- Unit tests: bottom of the module under a `#[cfg(test)] mod tests { ... }` block.
- Integration tests: under `tests/<feature>.rs`, exercising the crate's public surface only.

### Structure
```rust
pub fn divide(a: i32, b: i32) -> Result<i32, String> {
    if b == 0 {
        return Err("cannot divide by zero".into());
    }
    Ok(a / b)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn returns_quotient_for_integers() {
        assert_eq!(divide(10, 2), Ok(5));
    }

    #[test]
    fn returns_error_on_zero_divisor() {
        assert_eq!(divide(1, 0), Err("cannot divide by zero".into()));
    }

    #[test]
    fn table() {
        for (a, b, expected) in [(0, 1, 0), (-4, 2, -2), (7, 7, 1)] {
            assert_eq!(divide(a, b), Ok(expected), "divide({a}, {b})");
        }
    }
}
```

### Real fakes from std
- `tempfile::tempdir()` (the `tempfile` crate is the de-facto standard for filesystem tests, but the crate is **not** a mock — it's a real temp directory).
- `std::env::set_var` / `remove_var` inside a test that sets and unsets in the same body.
- For HTTP, spin up `std::net::TcpListener::bind("127.0.0.1:0")` in a thread and point the production code at the bound port.

### Async
- Add `#[tokio::test]` (or whichever runtime is in scope) for `async fn` tests.
- `await` real futures; do not use `block_on` inside async tests.

### What NOT to do
- No `mockall::mock! { ... }`.
- No `faux::create!` or related macros.
- Do not introduce trait objects (`Box<dyn Trait>`) or generics solely so tests can substitute a fake — the production code stays unchanged.

### Assertions
- `assert_eq!`, `assert_ne!`, `assert!(predicate, "message")`.
- For `Result` and `Option`, prefer pattern matching when the message matters:
  ```rust
  match divide(1, 0) {
      Err(e) if e.contains("zero") => {},
      other => panic!("expected zero-divisor error, got {other:?}"),
  }
  ```

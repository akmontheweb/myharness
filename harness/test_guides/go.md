---
applies_to: [go]
---

## Go Test Generation Guide

Write standard `go test` tests for the Go source files just modified. Use only the standard library — `testing`, `testing/quick`, `net/http/httptest`. **Do not write mocks** (no `gomock`, no `testify/mock`, no hand-rolled interface stubs). Call the real implementation.

### File placement
- Colocate: `foo.go` → `foo_test.go` in the same package.
- Use the same package name as the source for white-box tests, or `<pkg>_test` for black-box tests against the exported surface only.

### Structure
- One top-level `Test<Symbol>` function per exported function/method.
- Use **table-driven tests** when the same behaviour is exercised across inputs:

```go
tests := []struct {
    name     string
    a, b     int
    want     int
    wantErr  bool
}{
    {"positive", 10, 2, 5, false},
    {"negative", -4, 2, -2, false},
    {"zero divisor", 1, 0, 0, true},
}
for _, tt := range tests {
    t.Run(tt.name, func(t *testing.T) {
        got, err := Divide(tt.a, tt.b)
        if (err != nil) != tt.wantErr {
            t.Fatalf("err = %v, wantErr = %v", err, tt.wantErr)
        }
        if got != tt.want {
            t.Errorf("got %d, want %d", got, tt.want)
        }
    })
}
```

### Real fakes from the standard library
- `t.TempDir()` for filesystem.
- `t.Setenv(key, value)` for environment variables.
- `httptest.NewServer(handler)` for HTTP — start a real server in the test, point the code at `server.URL`, defer `server.Close()`.
- `httptest.NewRecorder()` for testing handlers directly.

### What NOT to do
- No `golang/mock` or `gomock`. No interfaces created solely to be mocked.
- No `testify/mock` or `testify/suite` — `assert` and `require` from testify are fine, the mock subpackage is not.
- Do not introduce a build-tag-gated fake implementation just to satisfy the test.

### Error handling
- `if err != nil { t.Fatalf(...) }` for setup failures (Fatalf so the test stops).
- `if got != want { t.Errorf(...) }` for assertion failures (Errorf so the test continues and reports more).

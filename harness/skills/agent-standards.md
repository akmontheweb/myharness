## Agent Standards & Conventions

### Patch Quality
- Every SEARCH block must be an exact, unique substring of the file being patched. Test search blocks mentally before outputting.
- REPLACE_BLOCK should change only the minimum lines needed; never rewrite entire files.
- CREATE_FILE must include the full file content with proper imports at the top.
- Never generate patches that touch more than 3 files in a single response without explicit approval.

### Error Handling
- All new functions must include try/except blocks for external calls (API, file I/O, database).
- Return meaningful error messages, not raw exception strings.
- Use custom exception classes where appropriate; avoid bare `except:`.

### Modularity
- Each file should have a single, clear responsibility.
- Extract reusable logic into utility functions/classes.
- Keep functions under 50 lines. If longer, refactor into sub-functions.
- Use dependency injection rather than hardcoded imports where practical.

### Type Safety
- Python: All function signatures must have type hints (parameters and return types).
- TypeScript/JavaScript: Use TypeScript interfaces/types; avoid `any`.
- Go: Every exported function must have a doc comment.

### Testing
- When creating new modules, suggest the test file structure.
- Test files should mirror the source structure (e.g., `src/auth.py` → `tests/test_auth.py`).
- Use descriptive test names that explain the scenario being tested.

### Documentation
- Every new class and public function must have a docstring.
- Docstrings must describe parameters, return values, and raised exceptions.
- Module-level docstrings should explain the module's purpose.

### Security
- Never include API keys, tokens, passwords, or secrets in generated code.
- Use environment variables for configuration; reference them via `os.environ.get()`.
- Validate all external inputs before processing.
- Sanitize data before logging; never log credentials.

### Performance
- Avoid O(n²) patterns; prefer dict/set lookups over list scans.
- Cache expensive computations where appropriate.
- Use async I/O for network and file operations.

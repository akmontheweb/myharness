---
applies_to: [node]
---

## Node.js Style Guide

### Source
- Node.js Best Practices — Yoni Goldberg (https://github.com/goldbergyoni/nodebestpractices)

### Project structure
- Structure by feature/component, not by technical role. Each component owns its API, service, data access, and tests — no shared `controllers/`, `services/`, `models/` mega-folders.
- Layer each component: routes → service (business logic) → data-access. The web layer (Express/Fastify/Nest handler) holds only HTTP concerns: parsing, validation, status codes.
- Wrap common utilities in a single `lib/` so cross-cutting concerns (logging, config, errors) have one canonical entry.
- Keep config out of code: load environment via a typed config module that fails fast on missing required keys.

### Error handling
- Distinguish **operational** errors (expected failures: bad input, network glitch) from **programmer** errors (bugs). Recover from the former; let the process crash on the latter and rely on a process manager to restart.
- Use a single, central `AppError` (or equivalent) class so handlers can identify operational errors via `instanceof`.
- Always `await` promises or attach `.catch` — every unhandled rejection is a future production incident. Subscribe to `process.on('unhandledRejection')` and `process.on('uncaughtException')` for last-resort logging before exit.
- Throw `Error` objects, never strings or plain objects (lost stack trace).
- Validate inputs at the API boundary (Joi, Zod, ajv) — don't trust internal callers to have validated.

### Code patterns
- Use `async`/`await`; avoid raw callbacks for new code. Avoid `.then(...).then(...)` chains longer than one step.
- Prefer `const`; reserve `let` for genuinely re-assigned bindings. Never use `var`.
- Use named arrow functions for callbacks so stack traces stay readable.
- Use the strict equality operators `===` / `!==` exclusively.
- Don't block the event loop: profile any sync work in hot paths, and offload CPU-bound work to a worker thread or external service.
- Require modules at the top of the file — lazy `require()` inside handlers leaks setup cost into hot paths.

### Production hardening
- Set `NODE_ENV=production` to enable framework optimizations.
- Use a process manager (PM2, systemd, container orchestrator) — never run `node app.js` bare in production.
- Bind to `0.0.0.0` only inside a trusted boundary; bind to `127.0.0.1` behind a reverse proxy otherwise.
- Use gzip / brotli compression at the proxy, not in Node.
- Lock down npm: `npm audit` in CI, lockfile committed, fixed dependency versions where possible.

### Logging
- Use a structured JSON logger (`pino`, `winston`). Never `console.log` in production code paths.
- Log at the right level: `info` for lifecycle, `warn` for recoverable surprises, `error` for failures, `debug` behind a flag.

### Testing
- Each component owns a sibling `*.test.js` (or `__tests__/`) — tests live next to the code, not in a top-level `test/`.
- Use AAA structure: Arrange / Act / Assert. One assertion concept per test.
- Tag tests by purpose: unit (no I/O), integration (real DB/external), e2e (full HTTP).

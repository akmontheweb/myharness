---
applies_to: [express, nest, fastify]
---

## Node.js — Express / Nest / Fastify

### When this skill applies
The workspace has a `package.json` declaring `express`, `@nestjs/core`, or `fastify`. Source under `src/`. TypeScript projects also have `tsconfig.json`; pure JS uses `.mjs`/`.cjs` or `"type": "module"` in package.json.

### File layout (idiomatic)
```
src/
  app.ts             # express() / Fastify() / NestFactory.create()
  index.ts           # listen() bootstrap (or main.ts for Nest)
  routes/            # Express: route modules; Nest: feature folders
  controllers/       # Express controllers; Nest @Controller classes
  services/          # business logic
  middleware/        # auth, error handler, logging
  models/            # Mongoose schemas or Prisma client wrappers
  schemas/           # Zod or Joi validation schemas
  utils/
package.json
tsconfig.json
```

### Conventions to follow
- Always `async/await` — never mix `.then()` chains with `await` in the same function.
- Validation at the boundary: parse request bodies with Zod/Joi/class-validator before the handler logic touches them.
- Error handlers go LAST in the middleware chain (`app.use((err, req, res, next) => ...)`) and have a 4-arg signature so Express recognises them.
- For Nest: one module per feature, providers injected via constructor, no static singletons.
- For Fastify: use schemas in route definitions — Fastify uses them for both validation and OpenAPI generation.
- Environment via `dotenv` + a typed config module; don't read `process.env` scattered across files.

### Common patches the LLM gets wrong
- Forgetting to `await` an async DB call (returns a Promise that the framework serializes as `{}`).
- Adding middleware AFTER `app.listen()` (won't take effect).
- Returning data from an Express handler with `return res.json(...)` and ALSO calling `next()` — double-response.
- Catching `await` errors but not calling `next(err)`, so Express's error pipeline never runs.

### Build / test / migrate
- TypeScript: `tsc --noEmit && jest` (or `vitest`).
- Prisma: `npx prisma migrate dev` (dev) or `npx prisma migrate deploy` (prod).
- Drizzle: `drizzle-kit generate` then `drizzle-kit migrate`.
- Knex: `npx knex migrate:latest`.
- Build/test command typically: `npm test && npx tsc --noEmit` (or `npm run lint && npm test && npm run build`).

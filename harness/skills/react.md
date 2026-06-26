---
applies_to: [react]
---

## Frontend — React + TypeScript + TailwindCSS (Vite-built)

### When this skill applies
The locked web stack. The workspace has `package.json` declaring `react`, `react-dom`, `typescript`, and `tailwindcss`. Build tool is **Vite** (Next.js, CRA, Vue, Angular, Svelte, and plain JavaScript are NOT supported and the harness will refuse to scaffold them). Source under `src/`.

### File layout (idiomatic)
```
src/
  main.tsx          # ReactDOM.createRoot(...).render(...)
  App.tsx           # root component
  components/       # Reusable presentational components
  features/         # Feature-scoped components + hooks + state (preferred over flat by-type folders)
  hooks/            # Cross-cutting custom hooks
  lib/              # API client, utils
  routes/           # If using react-router
  index.css         # Tailwind directives (@tailwind base / components / utilities)
package.json
vite.config.ts
tsconfig.json        # strict: true is mandatory
tailwind.config.ts
postcss.config.js
```

### Conventions to follow
- **TypeScript strict mode is mandatory.** `tsconfig.json` must set `"strict": true`. Avoid `any`; reach for `unknown` and narrow.
- **Tailwind utilities for styling.** Don't introduce a second styling system (no styled-components, no Emotion, no global CSS overrides beyond the Tailwind entry file).
- **Functional components + hooks only.** Class components are legacy; don't introduce them.
- One component per file. Filename matches the component name (PascalCase).
- Co-locate hooks/tests with the component (`Button.tsx`, `Button.test.tsx`).
- TypeScript prop interfaces should mirror the JSON shapes returned by the Python or Java backend. When a backend response schema changes, the frontend interface MUST change in the same patch.
- State management: `useState` for local, `useReducer` for complex local, Context for cross-component, then reach for Zustand only when truly needed.
- Memoization (`useMemo`, `useCallback`, `React.memo`) only when a profiler shows a real problem — premature memoization adds noise.
- Communicate with the backend exclusively via RESTful APIs (typed via the generated interfaces). No backend-rendered HTML, no GraphQL by default.

### Common patches the LLM gets wrong
- **Stale closures in `useEffect`** — referencing a state variable without listing it in the deps array. ESLint's `react-hooks/exhaustive-deps` rule catches this; respect it.
- Missing `key` prop on rendered lists (causes incorrect re-rendering and lost component state).
- Mutating state directly (`state.items.push(...)`) instead of returning a new array (`[...state.items, x]`).
- Setting state in render (infinite re-render loop).
- Forgetting cleanup function in `useEffect` for subscriptions/timers (memory leaks).
- Using `index` as `key` in dynamic lists (breaks identity when items reorder).
- Mixing arbitrary inline `style` props with Tailwind classes — pick utilities; if a value is truly dynamic, set a CSS custom property and reference it from a Tailwind class.

### Build / test
- `npm install && npm run build && npm test` (Vitest is the default test runner).
- Type check: `tsc --noEmit` as a separate step is invaluable in CI.

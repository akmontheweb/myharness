---
applies_to: [react]
---

## Frontend — React

### When this skill applies
The workspace has `package.json` declaring `react` and `react-dom`. Build tool is typically Vite, Next.js, or Create React App. Source under `src/`.

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
package.json
vite.config.ts (or next.config.js)
tsconfig.json
```

### Conventions to follow
- **Functional components + hooks only.** Class components are legacy; don't introduce them.
- One component per file. Filename matches the component name (PascalCase).
- Co-locate hooks/styles/tests with the component (`Button.tsx`, `Button.test.tsx`, `Button.module.css`).
- Use TypeScript types for props (`type Props = { ... }`). Avoid `any`.
- State management: `useState` for local, `useReducer` for complex local, Context for cross-component, then reach for Zustand/Redux/Jotai only when truly needed.
- Memoization (`useMemo`, `useCallback`, `React.memo`) only when a profiler shows a real problem — premature memoization adds noise.

### Common patches the LLM gets wrong
- **Stale closures in `useEffect`** — referencing a state variable without listing it in the deps array. ESLint's `react-hooks/exhaustive-deps` rule catches this; respect it.
- Missing `key` prop on rendered lists (causes incorrect re-rendering and lost component state).
- Mutating state directly (`state.items.push(...)`) instead of returning a new array (`[...state.items, x]`).
- Setting state in render (infinite re-render loop).
- Forgetting cleanup function in `useEffect` for subscriptions/timers (memory leaks).
- Using `index` as `key` in dynamic lists (breaks identity when items reorder).

### Build / test
- Vite: `npm run build && npm test` (Vitest or Jest).
- Next.js: `npm run build && npm test`.
- CRA: `npm run build && npm test -- --watchAll=false`.
- Type check: `tsc --noEmit` as a separate step is invaluable in CI.

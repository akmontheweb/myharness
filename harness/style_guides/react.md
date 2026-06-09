---
applies_to: [react]
---

## React Style Guide

### Source
- React Documentation (https://react.dev/learn)

### Components
- Default to function components. Class components only for legacy or error boundaries (and even those have hook-based alternatives).
- Component names are `PascalCase`. File name matches the component name.
- One component per file as a default; co-locate small helper components in the same file only when they're used solely by the primary export.
- Props are read-only. Never mutate `props.x` or a value derived from props.
- Define props with a `Props` type/interface above the component; destructure in the parameter list (`function User({ name, age }: Props)`).

### State & effects
- Choose state shape so impossible states are unrepresentable. Prefer a small number of `useState` calls over one giant object.
- Derive values during render — don't store derived state. If a value can be computed from props or other state, compute it.
- `useEffect` runs after render to synchronize with external systems (network, subscriptions, DOM APIs, timers). Don't use it to transform data for rendering.
- Every effect must declare its full dependency array. If a dependency causes loops, fix the dependency (memoize, lift state, use a ref) — don't suppress the lint rule.
- Cleanup in the effect return value. Subscriptions, timers, observers all need teardown.
- Reach for `useRef` to hold mutable values that don't trigger re-render, or to access DOM nodes. Mutating `ref.current` during render is forbidden.

### Hook rules
- Call hooks only at the top level of a component or another hook. Never inside loops, conditions, or after early returns.
- Custom hooks start with `use` so the linter can enforce hook rules.
- A custom hook is just a function that calls other hooks — it doesn't have to return JSX.

### Keys & lists
- Every element in a list gets a stable `key`. The key must be unique among siblings and stable across renders — never use the array index when the list can reorder, filter, or insert.

### Performance
- Don't reach for `useMemo` / `useCallback` by default — they have their own cost. Use them when profiling shows a real win or when a child component memoizes by reference identity.
- Use `React.memo` only for components that re-render frequently with the same props.
- Lift state up only as far as the nearest common ancestor that needs it. Higher than that creates render thrash.

### Forms
- Default to controlled inputs. Use uncontrolled (`useRef`) only when the input is write-only or you're integrating with non-React DOM code.

### Imports
- `import { useState, useEffect } from 'react';` — named imports for hooks.
- Don't import the default `React` namespace just to use JSX (modern transform handles it).

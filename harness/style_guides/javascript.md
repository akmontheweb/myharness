---
applies_to: [node, react]
---

## JavaScript Style Guide

### Source
- Airbnb JavaScript Style Guide (https://github.com/airbnb/javascript)

### Variables & references
- Always use `const` for references you do not reassign; `let` only when reassignment is required. `var` is forbidden.
- One `const` / `let` declaration per variable. Group all `const`s, then all `let`s.
- Don't use leading underscores on identifiers to fake privacy — use module scope or `#private` fields.

### Strings
- Single quotes for string literals; backticks for template literals (any interpolation, any multi-line).
- Don't use `eval()` ever; don't use `new Function()` with user input.

### Functions
- Prefer arrow functions for inline callbacks; use named function declarations for top-level definitions so stack traces are readable.
- Never name a parameter `arguments`. Prefer rest (`...args`) over the `arguments` object.
- Use default parameters (`function f(x = 1)`) instead of mutating `arguments` or runtime ternaries.
- Spread, don't `apply`: `f(...args)` not `f.apply(null, args)`.

### Objects & arrays
- Use literal syntax (`{}`, `[]`), never the constructors.
- Use property shorthand and method shorthand inside object literals.
- Use computed property names when the key is dynamic — don't mutate the object after creation.
- Spread to copy (`{...obj}`, `[...arr]`); destructure to read (`const { name } = user;`).
- Iterate arrays with `for...of`, `.map`, `.forEach` — never `for...in` (it walks the prototype chain and yields strings).

### Modules
- One import group per source, no wildcard re-export from index barrels that hide tree-shaking.
- Prefer named exports; reserve default export for the single primary thing a module exposes.
- Don't import the same path twice; combine into one statement.

### Comparisons & control flow
- Strict equality (`===`, `!==`) everywhere. Never use `==`.
- Use truthy/falsy checks for boolean intent; use explicit comparisons for value intent.
- `switch` blocks: each `case` ends in `break`, `return`, or `throw`; default goes last; wrap `case` bodies in `{}` when declaring variables.

### Async
- Use `async`/`await`; avoid raw promise chains beyond a single `.catch`.
- Don't mix callbacks and promises in the same API — pick one.

### Whitespace
- 2-space indent; LF line endings; trailing newline at EOF.
- Semicolons required.
- Spaces inside `{ }` of object literals; no spaces inside `[ ]` or `( )`.
- Trailing commas on multi-line array/object/function-arg lists (better diffs, no syntax penalty in modern engines).

### React-flavored notes (when JSX is in use)
- File extension `.jsx` (or `.tsx`) for files that contain JSX.
- Component filenames in `PascalCase` matching the default export.
- Self-close empty elements (`<Foo />`).

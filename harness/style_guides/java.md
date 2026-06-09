---
applies_to: [java]
---

## Java Style Guide

### Source
- Google Java Style Guide (https://google.github.io/styleguide/javaguide.html)

### Layout & formatting
- One top-level class per source file. Filename matches the class name.
- 2-space indentation. No tabs. Continuation indent is +4 spaces.
- Column limit 100 chars. Wrap at higher syntactic levels first (whole expressions before binary operators).
- One statement per line; one variable per declaration.
- Source file order: license header → package → imports (no wildcards, no static-import wildcards, no line wrapping) → exactly one top-level class.
- Braces always required, even for single-statement `if`/`else`/`for`/`while`. K&R brace style: opening brace on the same line, closing brace on its own line.
- Empty blocks may be concise (`{}`) when not part of a multi-block statement.

### Naming
- `UpperCamelCase` for classes, interfaces, enums, annotations.
- `lowerCamelCase` for methods, fields, parameters, local variables.
- `UPPER_SNAKE_CASE` for constants (`static final` whose value is deeply immutable).
- `lowercase` (no underscores) for package names.
- Type variables: single capital letter optionally followed by a numeral (`T`, `T2`), or a class-style name followed by `T` (`RequestT`).
- Acronyms treated as words: `XmlHttpRequest`, not `XMLHTTPRequest`.

### Methods & fields
- Method modifiers in the canonical order: `public protected private abstract default static final transient volatile synchronized native strictfp`.
- Use `@Override` whenever applicable.
- One blank line between members; multiple blank lines forbidden.
- Prefer immutability: `final` on every field that doesn't need to mutate.
- Annotate nullable parameters and returns with `@Nullable` from JSR-305 or the project's chosen library; otherwise the API contract is non-null.

### Errors & control flow
- Never silently swallow a `catch` block. At minimum, comment why ignoring is correct; usually log or rethrow.
- Don't catch `Exception` or `Throwable` except in framework-level entry points.
- `switch` statements without `break` must comment "fall through". Always include a `default:` case even when exhaustive.
- Prefer enhanced-for (`for (E e : collection)`) over index loops when the index isn't used.

### Generics & types
- Avoid raw types; use bounded wildcards (`List<? extends Number>`) at API boundaries.
- Prefer interfaces over implementations in variable types (`List<String> xs = new ArrayList<>();`).

### Javadoc
- `/** ... */` Javadoc on every `public` class and member. First sentence is a summary fragment terminated by a period.
- Block tags in this order: `@param`, `@return`, `@throws`, `@deprecated`.
- No `@author` tags (per Google guide).

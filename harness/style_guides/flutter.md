---
applies_to: [flutter, dart]
---

## Flutter Style Guide

### Source
- Flutter Style Guide for repository contributors (https://github.com/flutter/flutter/blob/main/docs/contributing/Style-guide-for-Flutter-repo.md)
- Effective Dart (https://dart.dev/effective-dart)

### Philosophy
- Optimize for the reader, not the writer. Performance-, memory-, and resilience-cost a feature has must be obvious from how the call site reads.
- Keep `build()` methods cheap and side-effect-free. The framework can call them many times per second.
- Prefer composition over subclassing — small `StatelessWidget`s composing larger ones beats deep inheritance hierarchies every time.

### Widget structure
- Default to `StatelessWidget`. Reach for `StatefulWidget` only when the widget owns mutable state across rebuilds.
- Make `Widget` subclasses immutable: all fields `final`. Constructor parameters are named (use `{}`) and constructors are `const` whenever the fields are all const-expressible.
- Pass values down via constructor parameters; pass callbacks up to parent state holders. Avoid global state and singletons for application data.
- For shared state across the tree, use `InheritedWidget`, `Provider`, `Riverpod`, or `Bloc` — pick one per project and stay with it.
- Implement `Key` thoughtfully — required when list children reorder, when widgets of the same type swap identity, or when state must persist across rebuilds.

### `build()` discipline
- Keep `build()` short: ideally under ~30 lines. Extract sub-widgets into named `StatelessWidget`s, not into local helper methods that return `Widget` — extracted widgets get their own element identity and rebuild boundary.
- Don't construct expensive objects inside `build()`. Compute once in `initState()` (or wherever state is initialized) and reuse.
- Wrap repeated work with `const` constructors where possible — the framework will skip rebuilds entirely.

### State management
- Call `setState()` only synchronously, only in response to events, and only with the smallest closure that mutates the state.
- Cancel timers, controllers, streams, and animation listeners in `dispose()`. Forgetting this is the #1 source of Flutter memory leaks.
- Don't call `setState()` after `dispose()` — guard with `if (!mounted) return;` after any `await`.

### Async patterns
- Prefer `async`/`await` over raw `.then`. Reserve raw `Future` chaining for cases where you genuinely need parallelism (`Future.wait`).
- Don't swallow errors. Either handle them or propagate. Bare `try { ... } catch (_) {}` is a bug.
- For streams in widgets, use `StreamBuilder` — manual `.listen()` requires manual cleanup in `dispose()`.

### Naming & Dart-side style
- `lowerCamelCase` for variables, parameters, methods.
- `UpperCamelCase` for types (classes, mixins, enums, typedefs).
- `lowercase_with_underscores` for file names, directory names, and library names.
- Avoid `dynamic` and `Object` at API boundaries — prefer generics or sum types.
- Always declare return types on public methods. Local closures may infer.
- Prefer expression bodies (`=>`) for one-line methods.

### Imports & dependencies
- Use `package:your_app/...` imports across feature folders; reserve relative imports for same-folder siblings.
- Group: dart SDK → flutter framework → third-party packages → your own packages → relative — separated by blank lines.

### Formatting
- Run `dart format` (the default 80-column setting). Don't hand-fight it.
- Trailing commas on multi-line constructor calls so `dart format` lays each child on its own line — diffs stay small.

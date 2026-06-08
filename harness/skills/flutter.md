## Mobile — Flutter (Dart)

### When this skill applies
The workspace has `pubspec.yaml` at the root declaring `flutter:` as an SDK dependency, a `lib/` directory, and platform folders (`android/`, `ios/`, `web/`).

### File layout (idiomatic)
```
lib/
  main.dart                 # void main() => runApp(MyApp())
  app.dart                  # MyApp root widget + MaterialApp
  pages/                    # screen-level widgets (or screens/)
  widgets/                  # reusable presentational widgets
  models/                   # data classes / freezed unions
  services/                 # API clients, repositories
  providers/                # Riverpod / Provider / Bloc state
  utils/
test/
  widget_test.dart
android/                    # Gradle project
ios/                        # Xcode project (built on macOS only)
web/                        # Flutter Web entry
pubspec.yaml
analysis_options.yaml       # lint rules (flutter_lints package recommended)
```

### Conventions to follow
- **StatelessWidget vs StatefulWidget**: prefer StatelessWidget. Only use StatefulWidget when you need `setState`, lifecycle hooks (`initState`, `dispose`), or `TickerProvider`.
- For larger state, reach for Riverpod (`flutter_riverpod`) or Bloc — not bare `setState` shotgun-spread across the app.
- Const constructors wherever possible: `const Text('Hello')`, `const SizedBox(height: 8)`. Flutter's analyzer enforces this via `prefer_const_constructors`.
- Async UI work via `FutureBuilder` / `StreamBuilder` or — better — Riverpod's `AsyncValue`.
- One widget per file when the widget exceeds ~50 lines or is used in more than one place.
- Always pass `key: Key(...)` (or `ValueKey(id)`) to list items rendered via `.map` so Flutter can identify them across rebuilds.

### iOS-specific notes (iOS-1 mode)
This harness can fully run `dart format`, `dart analyze`, and `flutter test` in its Linux sandbox. It **cannot** run `flutter build ios` or `xcodebuild` — those require macOS + Xcode. iOS-specific Dart code (Cupertino widgets, platform channels) still gets full static-analysis feedback. The final IPA build must happen on a Mac or CI macOS runner.

### Common patches the LLM gets wrong
- Forgetting `const` on a stateless widget constructor (analyzer flags it; harness will surface it via `dart analyze`).
- Calling `setState` after `dispose` — use `if (mounted) setState(...)`.
- Passing a `BuildContext` across an `await` without checking it's still valid (`if (!mounted) return;`).
- Building widgets inside `build()` that depend on state without listening — should be a Consumer / `context.watch` / ref.watch.
- Putting business logic in widgets instead of a service / notifier.

### Build / test
- Build/test command typically: `dart format --set-exit-if-changed . && flutter analyze && flutter test`.
- Android APK: `flutter build apk` (works in Linux sandbox if Android SDK is present in the image).
- iOS IPA: `flutter build ios` — macOS only, out of harness scope.
- Web: `flutter build web` → static bundle in `build/web/`.

### Mobile routing (M-1)
The harness detects Flutter projects (pubspec.yaml + lib/) and skips the `docker-compose` deployment pipeline automatically — Flutter artifacts (APK/Web bundle) live in `build/` for the user or their CI to pick up. There is no "deploy" step inside the harness for Flutter.

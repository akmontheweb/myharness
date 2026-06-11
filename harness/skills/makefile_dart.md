---
applies_to: [dart, flutter]
---

## Build — Dart / Flutter Makefile

### When this skill applies
The workspace has `pubspec.yaml` at the root. Pure Dart projects (CLIs, libraries) have it without a `flutter:` SDK dependency; Flutter projects declare `flutter:` and ship with `android/`, `ios/`, `web/` platform folders. The harness runs `make build` by default; without a Makefile it falls back to noisy command-adaptation logic.

### Always emit a `Makefile` in your first patch
Pick the variant matching the project type. The `flutter` skill at the language level covers Flutter-specific conventions (StatelessWidget, const constructors, etc.); this skill is just for the build target.

**Pure Dart** (no `flutter:` in pubspec.yaml):
```make
.PHONY: build test all analyze clean

build:
	dart pub get

test:
	dart test

analyze:
	dart analyze

all: build test

clean:
	dart pub cache repair
	rm -rf .dart_tool/ build/
```

**Flutter**:
```make
.PHONY: build test all analyze clean

build:
	flutter pub get

test:
	flutter test

analyze:
	flutter analyze

all: build test

clean:
	flutter clean
```

### Conventions to follow
- Use TAB indentation inside recipes.
- `build:` resolves dependencies (`pub get`); `test:` runs the test runner. Don't conflate them — `pub get` is fast and idempotent but `test` is slow.
- Add `analyze:` as a separate target — the Dart analyzer catches lints and type errors that `dart test` won't surface (analyzer runs on the whole codebase, tests only run what's imported).
- Don't run `flutter build apk` / `flutter build ios` from `make build` — those are platform-artifact builds, not the dev loop. Put them in separate targets if needed (`make apk`, `make ipa`).
- iOS builds require macOS + Xcode; the harness's Linux sandbox cannot run `flutter build ios`. Document that limitation in the Makefile with a comment if you add an `ios:` target.

### Common patches the LLM gets wrong
- Spaces instead of tabs for recipe indentation (silent fail).
- Using `dart` commands in a Flutter project (or vice versa) — Flutter projects must use the `flutter` wrapper because it sets up the embedded Dart SDK and Flutter-specific tooling.
- Adding `flutter pub upgrade` to `build:` — mutates `pubspec.lock` and breaks reproducibility. Keep upgrades manual.
- Calling `dart format --set-exit-if-changed .` in `build:` — fine for CI but slows the inner-loop. Put it in a separate `format-check:` target.
- Forgetting `.PHONY:` and watching `make build` skip when a `build/` directory exists (Flutter creates `build/` on first run, which masks the target).

---
applies_to: [java, maven, gradle]
---

## Build — Java Makefile (Maven / Gradle)

### When this skill applies
The workspace has `pom.xml` (Maven) or `build.gradle` / `build.gradle.kts` (Gradle), plus Java sources under `src/main/java/`. The harness runs `make build` by default; without a Makefile it falls back to noisy command-adaptation logic.

### Always emit a `Makefile` in your first patch
Pick the variant matching the build tool the workspace already uses (or that you're creating).

**Maven** (`pom.xml`):
```make
.PHONY: build test all package clean

build:
	mvn -B -DskipTests compile

test:
	mvn -B test

package:
	mvn -B package

all: build test

clean:
	mvn -B clean
```

**Gradle** (`build.gradle` / `build.gradle.kts`):
```make
.PHONY: build test all clean

build:
	./gradlew assemble

test:
	./gradlew test

all: build test

clean:
	./gradlew clean
```

If `gradlew` isn't yet committed, use `gradle` directly:
```make
build:
	gradle assemble

test:
	gradle test
```

### Conventions to follow
- Use TAB indentation inside recipes.
- Always pass `-B` (batch mode) to Maven so it doesn't ANSI-paint the log — the harness sandbox captures pipe output and ANSI codes can break diagnostic parsing.
- `build:` compiles only (`mvn compile` / `gradle assemble`); `test:` runs the JUnit/TestNG suite. Don't run `mvn package` from `build:` — packaging includes test execution and slows the loop unnecessarily.
- Prefer `./gradlew` over `gradle` once the wrapper is checked in — pins the Gradle version per project.

### Common patches the LLM gets wrong
- Spaces instead of tabs for recipe indentation (silent fail).
- Calling `mvn install` from `build:` — that publishes to the local Maven repo (`~/.m2`) and is slower than `compile`.
- Forgetting `-DskipTests` on `mvn compile` — Maven test compilation can fail for unrelated reasons during early scaffolding.
- Running `./gradlew` without making it executable (`chmod +x gradlew`) — add that to the patch if you're also creating the wrapper.
- Using `gradle build` for the `build:` target — Gradle's `build` task includes `test`, which duplicates effort with `make test`. Use `assemble` (compile only) or `classes` (compile main, no resources).

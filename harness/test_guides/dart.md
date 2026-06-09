---
applies_to: [dart, flutter]
---

## Dart / Flutter Test Generation Guide

Write `package:test` (or `package:flutter_test`) tests for the Dart files just modified. **Do not use mockito or any mock-generating package.** Call the real implementations with real inputs. When a side effect cannot be invoked, prefer a real fake (in-process HTTP server via `HttpServer.bind`, an in-memory file via `MemoryFileSystem` from `file` package only if that package is already in `pubspec.yaml`).

### File placement
- `lib/foo.dart` → `test/foo_test.dart`.
- Flutter widget tests: `test/widgets/<name>_widget_test.dart`.

### Structure
- `group('<Symbol>', () { ... })` per public API.
- `test('<behaviour>', () { ... })` per case.
- `setUp` / `tearDown` for per-test resources.
- For async: `test('...', () async { await ...; expect(...); });` — never return a non-awaited Future.

### Assertions
- `expect(actual, equals(expected))`.
- `expect(actual, isA<MyType>())`.
- `expect(() => fn(), throwsA(isA<ArgumentError>()))`.
- For futures: `expect(future, completion(equals(value)))` or `await expectLater(future, throwsA(...));`.

### Flutter widget tests
- `testWidgets('<behaviour>', (tester) async { await tester.pumpWidget(...); ... });`
- `find.byType(Widget)`, `find.text('Label')`, `find.byKey(Key('id'))`.
- `await tester.tap(find.text('Submit'));` then `await tester.pump();`.

### What NOT to do
- No `package:mockito`, no `Mock<T>`, no `@GenerateMocks([Foo])`.
- No `package:mocktail`.
- No abstract classes introduced to enable mocking — test the concrete class.

### Minimal example
```dart
import 'package:test/test.dart';
import 'package:mypkg/calculator.dart';

void main() {
  group('divide', () {
    test('returns quotient for integers', () {
      expect(divide(10, 2), equals(5));
    });

    test('throws on zero divisor', () {
      expect(() => divide(1, 0), throwsA(isA<ArgumentError>()));
    });

    for (final (a, b, expected) in [(0, 1, 0), (-4, 2, -2), (7, 7, 1)]) {
      test('divide($a, $b) == $expected', () {
        expect(divide(a, b), equals(expected));
      });
    }
  });
}
```

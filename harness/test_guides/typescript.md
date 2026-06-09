---
applies_to: [typescript]
---

## TypeScript Test Generation Guide (Jest + ts-jest)

Same conventions as the JavaScript guide — Jest, real implementations, no mocks. Adapt for TypeScript:

### File placement
- Co-locate next to source as `<name>.test.ts`, or under `__tests__/<name>.test.ts`.

### Imports & types
- `import { divide } from '../src/calculator';` (ES modules form; `ts-jest` accepts it).
- Annotate the test data types when they aren't obvious from inference (e.g. `const cases: ReadonlyArray<[number, number, number]> = [...]`).
- Avoid `any`. If the production code returns a discriminated union, narrow it with `if ('error' in result)` before asserting fields.

### Async
- Use `async`/`await` test functions; do not return promises from sync test functions.
- `await expect(promise).resolves.toEqual(...)` / `.rejects.toThrow(...)`.

### What NOT to do
- No `jest.mock`, no `jest.fn`, no `ts-mockito`, no `sinon`.
- Do not invent `Partial<T>` shapes to stand in for real values; construct full objects (use a factory function defined in the test file if the type has many required fields).

### Minimal example
```typescript
import { divide } from '../src/calculator';

describe('divide', () => {
  it('returns quotient for integers', () => {
    expect(divide(10, 2)).toBe(5);
  });

  it('throws on zero divisor', () => {
    expect(() => divide(1, 0)).toThrow(/cannot divide by zero/);
  });

  it.each<[number, number, number]>([[0, 1, 0], [-4, 2, -2]])(
    'divide(%i, %i) === %i',
    (a, b, expected) => {
      expect(divide(a, b)).toBe(expected);
    },
  );
});
```

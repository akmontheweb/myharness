## Frontend — Angular

### When this skill applies
The workspace has `angular.json` at the root, `package.json` declaring `@angular/core`, and a `src/` directory with `main.ts` + `app/`.

### File layout (idiomatic)
```
src/
  main.ts                   # bootstrapApplication(AppComponent, { providers: [...] })
  index.html
  app/
    app.component.ts        # @Component({...})
    app.component.html
    app.component.scss
    app.config.ts           # ApplicationConfig with providers (standalone API)
    app.routes.ts           # Routes array
    features/
      users/
        users.component.ts
        users.service.ts
        users.routes.ts
        models/
angular.json
tsconfig.json
```

### Conventions to follow
- **Standalone components are the default** in Angular 17+ (`standalone: true` on `@Component`). New code should not use NgModule unless integrating with legacy code that requires it.
- Inject dependencies via constructor params with `private readonly` modifiers. Or use `inject(SomeService)` in standalone APIs.
- Services that hold app state are `providedIn: 'root'` to be singletons. Feature-scoped services declare `providedIn: SomeComponent`.
- RxJS: prefer `async` pipe in templates over manual `.subscribe()` (avoids leak risk). Always `unsubscribe()` if you do subscribe manually — use `takeUntilDestroyed()` (Angular 16+) or a `Subject` + `ngOnDestroy`.
- Use Angular Signals (Angular 16+) for new local-component state in preference to BehaviorSubject when not crossing component boundaries.

### Common patches the LLM gets wrong
- Subscribing in a component without unsubscribing — memory leak. Use `async` pipe or `takeUntilDestroyed()`.
- Mixing standalone components and NgModule-declared components incorrectly (a standalone component imported via `imports:` not via `declarations:`).
- Calling a function inside a template (`{{ getValue() }}`) — runs on every change detection cycle. Compute once and store, or use a pipe.
- Using `(click)` on a non-interactive element without `tabindex` + `(keydown.enter)` (a11y).
- Mutating an observable's emitted value instead of mapping to a new one.

### Build / test
- `ng build` → production bundle in `dist/`.
- `ng test` runs Karma + Jasmine (or Jest if configured).
- `ng lint` runs ESLint with the angular-eslint plugin.
- Build/test command typically: `ng build && ng test --watch=false && ng lint`.

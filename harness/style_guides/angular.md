---
applies_to: [angular]
---

## Angular Style Guide

### Source
- Angular Coding Style Guide (https://angular.dev/style-guide)

### File & folder layout
- Feature-folder structure: each feature owns a folder with its components, services, models, and tests. Don't shard by technical type (`components/`, `services/`) at the root.
- One concept per file. Component, service, directive, pipe, module — each in its own file.
- File names use kebab-case with a role suffix: `user-profile.component.ts`, `auth.service.ts`, `not-found.page.ts`, `data-store.ts`.
- Test file is the source filename + `.spec.ts` next to it.
- Keep files under ~400 LOC. Split when responsibilities grow.

### Naming
- Class names match the file's role: `UserProfileComponent`, `AuthService`. Use `PascalCase` for classes; `camelCase` for properties, methods, locals.
- Symbol name and file name should align (`UserProfileComponent` → `user-profile.component.ts`).
- Selector prefixes (`app-`, project-prefix) on every component selector to avoid collisions with future HTML elements and third-party libraries.

### Components
- Use standalone components for new code (`standalone: true`); reach for NgModules only when integrating with legacy code that requires them.
- Use the `inject()` function for DI inside component bodies; constructor injection remains acceptable but `inject()` composes better with helpers.
- Use signals for component state (`signal<T>()`, `computed()`); use observables for streams crossing component boundaries.
- Keep components small and focused: template, minimal logic, delegate to services. Anything more than glue and presentation belongs in a service.
- Prefer the new control-flow syntax (`@if`, `@for`, `@switch`) over the legacy structural directives (`*ngIf`, `*ngFor`).
- `@for` loops require a `track` expression. Pick a stable identifier; never use `$index` for collections that can reorder.

### Services
- Provide singletons at the root: `@Injectable({ providedIn: 'root' })`. Local providers only when scope is genuinely component-tree-local.
- Services hold business logic and state; components hold UI logic and event glue.
- Don't expose subjects from services — expose the observable / signal and keep the setter private.

### Templates
- Bind directly to fields (`{{ user.name }}`), not via method calls in templates — method calls re-evaluate every change-detection tick.
- Use trackBy / `track` in iterations.
- Keep templates declarative — pull complex expressions into a computed signal or component property.
- Use the async pipe (`obs$ | async`) for observables rendered in templates; avoid manual `.subscribe()` in components.

### Lifecycle & change detection
- Default to `ChangeDetectionStrategy.OnPush` for new components — it eliminates a whole class of accidental re-render bugs and pairs naturally with signals and observables.
- Clean up subscriptions: takeUntilDestroyed, `DestroyRef`, or unsubscribe in `ngOnDestroy`. Leaking a subscription is the most common Angular memory bug.

### Routing
- Use lazy-loaded routes for feature areas: `loadComponent: () => import('...').then(m => m.X)`.
- Co-locate route configuration with the feature it loads.

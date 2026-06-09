---
applies_to: [vue]
---

## Vue Style Guide

### Source
- Vue Style Guide (https://vuejs.org/style-guide/)

### Priority A — essential
- Component names always multi-word (`TodoItem`, never `Todo`). Avoids collisions with current and future HTML elements.
- Prop definitions are detailed objects, not bare strings: `name: { type: String, required: true }`. Bare arrays of names are forbidden outside prototypes.
- Always `key` `v-for`: `<li v-for="item in items" :key="item.id">`.
- Never put `v-if` and `v-for` on the same element. Wrap with a `<template v-for>` and put `v-if` inside, or compute the filtered list in a computed property.
- Component-scoped styles only — `<style scoped>`, CSS modules, or a BEM-like convention.
- Use a `private`-prefixed key (e.g. `$_componentName_propName`) when augmenting plugins with private properties.

### Priority B — strongly recommended
- One component per file in single-file-component projects.
- Single-file component filenames are either `PascalCase` (`UserCard.vue`) or `kebab-case` (`user-card.vue`). Pick one and stay consistent across the project.
- Base components — UI primitives that wrap a single element — are named with a project-wide prefix like `Base` or `App` (`BaseButton`, `AppIcon`).
- Single-instance components (the app shell, the global toaster) are prefixed `The` (`TheHeader`, `TheSidebar`).
- Tightly-coupled child components share their parent's name as a prefix (`TodoList`, `TodoListItem`).
- Order word-by-word from most general to most specific: `SearchButtonClear`, not `ClearSearchButton`.
- In templates, use `PascalCase` for component names (`<UserCard />`); in DOM templates, `kebab-case` is the only legal form.

### Composition API (Vue 3)
- Prefer `<script setup>` — it's terser, has better type inference, and works first-class with TypeScript.
- Reactive primitives via `ref`; reactive objects via `reactive`. Don't mix the two ways of accessing the same value.
- Destructuring `reactive(...)` loses reactivity — use `toRefs` when handing reactive object members to a child function or template.
- Computed properties are read-only by default. When you need a writable computed (e.g. v-model proxy), use the get/set form.
- Watchers (`watch`, `watchEffect`) replace `methods` for side effects. Use `watchEffect` when the dependencies are obvious from the body, `watch` when you need the old value or explicit triggers.

### Templates
- Prefer shorthand directives: `:href` not `v-bind:href`, `@click` not `v-on:click`.
- Multi-attribute elements span multiple lines — one attribute per line — to keep diffs small.
- Keep expressions in templates simple — anything beyond a property access or method call should be a computed property.

### Single-file component order
- `<script>` → `<template>` → `<style>` (the official recommendation). Or `<template>` first if your team prefers visual-first reading — be consistent.

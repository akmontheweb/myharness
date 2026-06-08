---
applies_to: [vue]
---

## Frontend — Vue 3

### When this skill applies
The workspace has `package.json` declaring `vue` (^3.x). Source under `src/`. Build tool is typically Vite (`create-vue` scaffold) or Nuxt.

### File layout (idiomatic)
```
src/
  main.ts           # createApp(App).mount('#app')
  App.vue           # root SFC
  components/       # Single-File Components (.vue)
  composables/      # use* functions (Vue 3 composition API hooks)
  views/            # route-level components
  router/           # vue-router config
  stores/           # Pinia stores
package.json
vite.config.ts
tsconfig.json
```

### Conventions to follow
- **Composition API + `<script setup>`** is the default for new Vue 3 code. Options API is still supported but should not be introduced into a composition-API codebase (and vice versa).
- Single-File Components: `<template>`, `<script setup lang="ts">`, `<style scoped>`. Keep them under ~300 lines; extract sub-components or composables when larger.
- State management: Pinia (NOT Vuex, which is legacy). One store per feature.
- Refs: `ref()` for primitives, `reactive()` for objects. Don't destructure a `reactive()` object — that loses reactivity. Use `toRefs()` if you must.
- Use `v-for` with an explicit `:key`. Don't `v-if` and `v-for` on the same element (precedence is confusing — wrap one in a `<template>`).

### Common patches the LLM gets wrong
- Mixing Options API (`data()`, `methods`) with `<script setup>` in the same file — syntactically wrong.
- Destructuring `props` defined via `defineProps` — destructured props lose reactivity. Use `toRefs(props)`.
- Mutating a prop directly. Use `emit('update:propName', value)` and `v-model` on the parent.
- Forgetting `defineEmits(['eventName'])` when using `emit()` in `<script setup>`.
- Using Composition API patterns inside Options API components (or vice versa).

### Build / test
- Vite: `npm run build && npm test` (Vitest is the standard for Vue 3).
- Nuxt: `nuxt build` for production, `nuxt typecheck` for TS check.
- Vue Test Utils (`@vue/test-utils`) for component tests; Cypress / Playwright for e2e.

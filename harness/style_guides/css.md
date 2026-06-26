---
applies_to: [react, css, tailwind]
---

## CSS Style Guide

### Source
- Tailwind CSS Best Practices (https://tailwindcss.com/docs/styling-with-utility-classes)

### Use utilities first
- Compose UI from utility classes in the markup. Don't reach for a custom class until you've reused the same combination at least three times in a way that isn't obvious from the template.
- Don't fight the system: `mx-4` over inline styles, `text-sm` over a hand-rolled font size, `grid grid-cols-3` over a custom layout class.
- Reserve `@apply` and component layer classes for genuinely repeated patterns where extracting a component (not a CSS class) isn't possible — usually generic primitives like `.btn` or `.card`.

### Class ordering
- Use Tailwind's official Prettier plugin so class order is automatic. Don't hand-sort against the convention.
- Group conceptually when wrapping long class lists: layout → spacing → sizing → typography → color → effects → interactivity → responsive/state variants — but defer to the formatter.

### Responsive & state variants
- Mobile-first: write the base class for the smallest viewport, then layer larger breakpoints (`md:`, `lg:`).
- Use state variants instead of writing your own pseudo-class rules: `hover:`, `focus-visible:`, `disabled:`, `aria-expanded:`, `data-[state=open]:`. They scale better and keep style next to markup.
- Use `dark:` for dark-mode treatments. Don't roll a parallel media query.

### Custom CSS — when you do write it
- Use logical properties (`padding-inline`, `margin-block`) for RTL/internationalization safety.
- Use CSS custom properties (`--brand-primary`) for theme tokens; map them through Tailwind's `theme` config so utilities pick them up.
- Avoid `!important` — if you need it, you're fighting specificity. Restructure instead.
- BEM-style class names (`block__element--modifier`) when you do write a custom class, so specificity stays flat.
- Single-class selectors are best (`.card`); avoid descendant selectors deeper than two levels.

### Layout
- CSS Grid for 2-D layouts; Flexbox for 1-D toolbars and stacks.
- Avoid fixed widths; prefer `min-w-*`, `max-w-*`, and intrinsic sizing.
- Use `gap` instead of margin-between-children tricks.

### Performance
- Purge unused classes — Tailwind's content config controls this; misconfiguring it ships hundreds of kilobytes of dead CSS.
- Avoid runtime style generation; prefer build-time utilities.

### Accessibility
- Never remove focus outlines without providing a replacement. `focus-visible:ring-2` is the canonical pattern.
- Maintain at least WCAG AA contrast for text. The Tailwind palette pairs reliably (`text-slate-700` on `bg-white`, `text-slate-200` on `bg-slate-900`).
- Hide content from screen readers only with `aria-hidden` or `sr-only`; never with `display: none` if you still want it announced.

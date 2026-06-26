---
applies_to: [react, html, css, tailwind]
---

## Composite Web Design System (Premium Light Mode)

### Source
- Tailwind UI (Refirme aesthetic) — https://tailwindui.com / https://tailwindcss.com/docs
- IBM Carbon Design System — https://carbondesignsystem.com
- Ant Design — https://ant.design/docs/spec/introduce
- WCAG 2.2 contrast — https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum

### Philosophy
This is the harness default when building web UI. It blends Tailwind's editorial polish, Carbon's geometric clarity, and Ant Design's exhaustive component coverage into a single premium light-mode system. Use these exact tokens unless the project ships its own `style_guides/web-design-system.md`. Every text-on-background pair below passes WCAG 2.2 AA (≥ 4.5:1 body, ≥ 3:1 large text / non-text UI).

---

## MODULE 1 — Light Mode Palette

### Neutrals (Tailwind slate × Carbon Cool Gray)

| Token | Hex | Role |
|---|---|---|
| `neutral-50`  | `#F8FAFC` | App background — page canvas |
| `neutral-100` | `#F1F5F9` | Card / panel surface, table zebra stripe |
| `neutral-200` | `#E2E8F0` | Subtle dividers, table cell borders |
| `neutral-300` | `#CBD5E1` | Default input borders, button outlines |
| `neutral-400` | `#94A3B8` | Placeholder text, disabled labels |
| `neutral-500` | `#64748B` | Secondary text, helper copy, icons |
| `neutral-600` | `#475569` | Body text (default) |
| `neutral-700` | `#334155` | Section headings, table header text |
| `neutral-800` | `#1E293B` | Display headings, primary emphasis |
| `neutral-900` | `#0F172A` | Max-contrast text, navigation rail |

Contrast on white: `neutral-600` → 7.6:1 (AAA), `neutral-500` → 5.2:1 (AA). `neutral-400` is non-text UI only (3.1:1).

### Accent — Brand Primary (Premium Indigo / Cobalt)

| Token | Hex | Role |
|---|---|---|
| `primary-50`  | `#EEF2FF` | Ghost button hover, primary tag tint |
| `primary-100` | `#E0E7FF` | Subtle highlight, focused chip background |
| `primary-200` | `#C7D2FE` | Disabled primary border |
| `primary-500` | `#6366F1` | Light-mode accent on neutral surfaces |
| `primary-600` | `#4F46E5` | **Primary button default, link color** |
| `primary-700` | `#4338CA` | Primary button hover |
| `primary-800` | `#3730A3` | Primary button active / pressed |
| `primary-900` | `#312E81` | Reserved for print / high-contrast contexts |

`primary-600` on white = 5.97:1 (AA normal, AAA large). White on `primary-600` = 5.97:1.

### Semantic States — Background tint + matched text

| State | Background | Border | Text / Icon | Solid fill |
|---|---|---|---|---|
| Success | `#ECFDF5` | `#A7F3D0` | `#065F46` (11.2:1) | `#059669` |
| Warning | `#FFFBEB` | `#FDE68A` | `#92400E` (7.9:1)  | `#D97706` |
| Error   | `#FEF2F2` | `#FECACA` | `#991B1B` (9.1:1)  | `#DC2626` |
| Info    | `#EFF6FF` | `#BFDBFE` | `#1E40AF` (8.6:1)  | `#2563EB` |

Solid-fill columns use white text — every pair clears 4.5:1.

---

## MODULE 2 — Typography & Spacing (4 / 8 px grid)

Font family: `Inter` for UI, `IBM Plex Sans` fallback for data work; system stack as last resort. Mono: `JetBrains Mono` / `IBM Plex Mono`.

### Type Scale

| Token | Size | Line height | Weight | Tracking | Use |
|---|---|---|---|---|---|
| `display` | `3rem` / 48 px      | `3.5rem` / 56 px | 700 | `-0.025em` | Marketing hero |
| `h1` | `2.25rem` / 36 px       | `2.75rem` / 44 px | 700 | `-0.02em`  | Page title |
| `h2` | `1.875rem` / 30 px      | `2.25rem` / 36 px | 700 | `-0.015em` | Section title |
| `h3` | `1.5rem` / 24 px        | `2rem` / 32 px    | 600 | `-0.01em`  | Card / dialog title |
| `h4` | `1.25rem` / 20 px       | `1.75rem` / 28 px | 600 | `0`        | Sub-section |
| `h5` | `1.125rem` / 18 px      | `1.75rem` / 28 px | 600 | `0`        | Form group header |
| `body` | `1rem` / 16 px        | `1.5rem` / 24 px  | 400 | `0`        | **App default** |
| `small` | `0.875rem` / 14 px   | `1.25rem` / 20 px | 400 | `0`        | Form labels, table cells |
| `caption` | `0.75rem` / 12 px  | `1rem` / 16 px    | 500 | `0.02em`   | Table header, meta |
| `overline` | `0.6875rem` / 11 px | `1rem` / 16 px   | 600 | `0.08em` uppercase | Section eyebrow |

Carbon influence: strict step-up in weight at the heading boundary (400 → 600 → 700). Tailwind influence: negative tracking on large headings, `Inter` as the editorial face.

### Spacing Scale (8 px base, 4 px half-step)

| Token | rem | px | Common use |
|---|---|---|---|
| `0.5` | `0.125rem` | 2 | Icon nudge |
| `1`   | `0.25rem`  | 4 | Icon-to-label gap |
| `2`   | `0.5rem`   | 8 | Input internal padding (vertical) |
| `3`   | `0.75rem`  | 12 | Input padding (horizontal) |
| `4`   | `1rem`     | 16 | Standard component gap |
| `6`   | `1.5rem`   | 24 | **Card padding (default)** |
| `8`   | `2rem`     | 32 | Section gap inside a page |
| `10`  | `2.5rem`   | 40 | Page-content top offset |
| `12`  | `3rem`     | 48 | Major section divider |
| `16`  | `4rem`     | 64 | Hero / empty-state padding |
| `20`  | `5rem`     | 80 | Marketing block gap |
| `24`  | `6rem`     | 96 | Page-top hero gap |

Layout-level spacing uses even multiples of `2` (8 px). `0.5` / `1` are reserved for intra-component micro-adjustment. Card and dialog padding is always `6` (24 px); table cell padding is `2` × `4`.

### Radius & Elevation

| Token | Value |
|---|---|
| `radius-sm` | `4px` (tags, table cells) |
| `radius-md` | `6px` (**inputs, buttons — default**) |
| `radius-lg` | `8px` (cards, panels) |
| `radius-xl` | `12px` (modals, drawers) |
| `radius-full` | `9999px` (avatars, pills) |

Light-mode shadows — slate-tinted, never pure black:

| Token | Value |
|---|---|
| `shadow-xs` | `0 1px 2px 0 rgb(15 23 42 / 0.04)` |
| `shadow-sm` | `0 1px 3px 0 rgb(15 23 42 / 0.06), 0 1px 2px -1px rgb(15 23 42 / 0.04)` |
| `shadow-md` | `0 4px 6px -1px rgb(15 23 42 / 0.06), 0 2px 4px -2px rgb(15 23 42 / 0.04)` |
| `shadow-lg` | `0 10px 15px -3px rgb(15 23 42 / 0.08), 0 4px 6px -4px rgb(15 23 42 / 0.04)` |
| `shadow-xl` | `0 20px 25px -5px rgb(15 23 42 / 0.10), 0 8px 10px -6px rgb(15 23 42 / 0.04)` |

---

## MODULE 3 — Component Specification

### Text Input (Ant structure + Tailwind polish)

```css
.input {
  background: #FFFFFF;
  border: 1px solid #CBD5E1;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 0.875rem;
  line-height: 1.25rem;
  color: #1E293B;
  transition: border-color 120ms ease, box-shadow 120ms ease, background-color 120ms ease;
}
.input::placeholder         { color: #94A3B8; }
.input:hover                { border-color: #94A3B8; }
.input:focus,
.input:focus-visible {
  outline: none;
  border-color: #4F46E5;
  box-shadow: 0 0 0 3px rgb(79 70 229 / 0.18);
}
.input[aria-invalid="true"]        { border-color: #DC2626; }
.input[aria-invalid="true"]:focus  { box-shadow: 0 0 0 3px rgb(220 38 38 / 0.18); }
.input:disabled {
  background: #F1F5F9; color: #94A3B8;
  cursor: not-allowed; border-color: #E2E8F0;
}
```

**Floating label (Ant):** label positioned `top: 50%; left: 12px; color: neutral-500`. On focus OR when input has value, transition to `top: 0; transform: translateY(-50%) scale(0.85); background: #FFFFFF; padding: 0 4px; color: primary-600` so it notches into the top border.

**Clear icon (Ant):** when input has a value, render `×` button at `right: 8px; opacity: 0.6`; hover `opacity: 1; color: neutral-700`. Hide on disabled. Always keyboard-reachable (`type="button"`, `aria-label="Clear"`).

**Sizing:** default `40px` tall, compact `32px`, large `48px`. Match button heights at each size.

### Buttons (Carbon geometry + Tailwind micro-interactions)

Heights `32 / 40 / 48 px` (sm/md/lg). Horizontal padding `12 / 16 / 20 px`. Radius `6px`. Left/right padding always equal — Carbon strictness.

```css
.btn-primary {
  height: 40px; padding: 0 16px;
  background: #4F46E5; color: #FFFFFF; border: 1px solid #4F46E5;
  border-radius: 6px; font-weight: 500; font-size: 0.875rem;
  transition: background-color 120ms ease, transform 80ms ease, box-shadow 120ms ease;
}
.btn-primary:hover         { background: #4338CA; border-color: #4338CA; }
.btn-primary:active        { background: #3730A3; border-color: #3730A3; transform: translateY(0.5px); }
.btn-primary:focus-visible { outline: none; box-shadow: 0 0 0 3px rgb(79 70 229 / 0.30); }
.btn-primary:disabled      { background: #E0E7FF; border-color: #E0E7FF; cursor: not-allowed; }

.btn-secondary {
  height: 40px; padding: 0 16px;
  background: #FFFFFF; color: #334155;
  border: 1px solid #CBD5E1; border-radius: 6px;
}
.btn-secondary:hover         { background: #F1F5F9; border-color: #94A3B8; }
.btn-secondary:active        { background: #E2E8F0; }
.btn-secondary:focus-visible { box-shadow: 0 0 0 3px rgb(79 70 229 / 0.20); border-color: #4F46E5; }

.btn-ghost {
  background: transparent; color: #4F46E5;
  border: 1px solid transparent; border-radius: 6px;
}
.btn-ghost:hover         { background: #EEF2FF; }
.btn-ghost:active        { background: #E0E7FF; }
.btn-ghost:focus-visible { box-shadow: 0 0 0 3px rgb(79 70 229 / 0.20); }
```

Destructive variant: swap `primary-*` for `error-*` on `.btn-primary` (`#DC2626 → #B91C1C → #991B1B`). Icon-only buttons use square dimensions and a tooltip on hover.

### Data Tables (pure IBM Carbon)

| Density | Row height | Cell vertical padding |
|---|---|---|
| Compact / dense | 32 px | 4 px |
| Default | 40 px | 8 px |
| Spacious | 48 px | 12 px |

```css
.table {
  width: 100%; border-collapse: separate; border-spacing: 0;
  font-size: 0.875rem; color: #475569; background: #FFFFFF;
}
.table thead th {
  height: 40px; padding: 0 16px; text-align: left;
  font-size: 0.75rem; font-weight: 600; letter-spacing: 0.02em;
  color: #334155; background: #F1F5F9;
  border-bottom: 1px solid #E2E8F0;
}
.table tbody td                          { padding: 8px 16px; border-bottom: 1px solid #E2E8F0; }
.table tbody tr:nth-child(even) td       { background: #F8FAFC; }
.table tbody tr:hover td                 { background: #EEF2FF; }
.table tbody tr[aria-selected="true"] td { background: #E0E7FF; border-left: 2px solid #4F46E5; }
.table th:first-child, .table td:first-child { padding-left: 24px; }
.table th:last-child,  .table td:last-child  { padding-right: 24px; }
```

Rules carried from Carbon:
- **Single-row borders only.** Don't outline columns — vertical lines fragment the eye.
- **Sticky header** on scrollable tables (`position: sticky; top: 0; z-index: 1`).
- **Numeric columns right-align** with `font-variant-numeric: tabular-nums;` for column scanning.
- **Sort affordance:** 12 px `chevron-up-down` in `neutral-500`; active sort uses `primary-600` with directional chevron.
- **Empty state** centered in body — 64 px illustration slot, `h4` headline, `body` description, single primary CTA.

### Modals & Drawers (Ant structure + Tailwind shadows)

**Backdrop:** `background: rgb(15 23 42 / 0.5); backdrop-filter: blur(2px);`. Click-to-dismiss unless destructive.

```css
.modal {
  background: #FFFFFF; border-radius: 12px;
  box-shadow:
    0 20px 25px -5px rgb(15 23 42 / 0.10),
    0 8px 10px -6px rgb(15 23 42 / 0.04);
  width: min(32rem, calc(100vw - 32px));
  max-height: calc(100vh - 64px);
  display: flex; flex-direction: column;
}
.modal__header {
  padding: 16px 24px; border-bottom: 1px solid #E2E8F0;
  display: flex; align-items: center; justify-content: space-between;
  font-size: 1.125rem; font-weight: 600; color: #1E293B;
}
.modal__body   { padding: 24px; overflow-y: auto; color: #475569; }
.modal__footer {
  padding: 16px 24px; border-top: 1px solid #E2E8F0;
  display: flex; justify-content: flex-end; gap: 8px;
}
```

**Footer order (Ant):** cancel/secondary on the left of the primary, primary on the right. Destructive confirmation uses the destructive primary variant.

**Drawer:** identical chrome, slide-from-right, widths `24 / 32 / 48rem` (sm/md/lg). `transform: translateX(0)` open, `translateX(100%)` closed, `transition: transform 240ms cubic-bezier(0.2, 0.8, 0.2, 1)`. Leading-edge radius only (`border-radius: 12px 0 0 12px`).

**Accessibility:** trap focus; close on `Escape`; restore focus to opener. `role="dialog"`, `aria-modal="true"`, `aria-labelledby` → header text.

---

## MODULE 4 — Interaction & States Matrix

| Element | Default | Hover | Focus (visible) | Active / Pressed | Disabled |
|---|---|---|---|---|---|
| Primary button | bg `primary-600` | bg `primary-700` | ring `primary-600 / 30%` | bg `primary-800`, translateY 0.5 px | bg `primary-100`, white label, `cursor: not-allowed` |
| Secondary button | bg white, border `neutral-300` | bg `neutral-100`, border `neutral-400` | ring `primary-600 / 20%`, border `primary-600` | bg `neutral-200` | bg `neutral-50`, text `neutral-400` |
| Ghost button | transparent | bg `primary-50` | ring `primary-600 / 20%` | bg `primary-100` | text `neutral-400` |
| Text input | bg white, border `neutral-300` | border `neutral-400` | border `primary-600`, ring `primary-600 / 18%` | (same as focus) | bg `neutral-100`, text `neutral-400`, border `neutral-200` |
| Checkbox / radio | border `neutral-400`, bg white | border `primary-600` | ring `primary-600 / 20%` | bg `primary-600`, check white | opacity 0.5, `cursor: not-allowed` |
| Table row | bg white / `neutral-50` zebra | bg `primary-50` | row outline `primary-600 / 30%` | bg `primary-100`, left-bar `primary-600` (selected) | text `neutral-400` |
| Card | bg white, `shadow-xs` | `shadow-md`, translateY -1 px (link cards only) | ring `primary-600 / 30%` | `shadow-sm` | bg `neutral-50`, opacity 0.6 |
| Link inline | `primary-600`, no underline | underline | ring `primary-600 / 30%` | `primary-800` | `neutral-400` |
| Tag / chip | bg `neutral-100`, text `neutral-700` | bg `neutral-200` | ring `primary-600 / 20%` | bg `neutral-300` | opacity 0.6 |

**Universal rules:**
- Focus rings are always 3 px and always `primary-600` at 18–30% alpha. Never use `outline: none` without a `:focus-visible` ring — keyboard navigation breaks.
- Solid-fill controls darken by one neutral step on hover (or `+100` on primary). Transparent controls fill with the `*-50` tint.
- Active / pressed shifts add a `0.5 px` downward translate for tactile feedback and darken one step beyond hover.
- Disabled combines four signals: `cursor: not-allowed`, `opacity 0.5–0.6` OR `neutral-50/100` fill, `text: neutral-400`, `aria-disabled="true"`. Never use `pointer-events: none` alone — it kills screen-reader announcements.
- Transitions: `120ms` color / background, `80ms` transform, `240ms` surface movement. Easing: `cubic-bezier(0.2, 0.8, 0.2, 1)` for entrance, linear for color.

---

## MODULE 5 — `tailwind.config.js` Extension

Drift-free with Modules 1–2 — same hex codes, same scale.

```js
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{js,jsx,ts,tsx,html}'],
  theme: {
    extend: {
      colors: {
        neutral: {
          50:'#F8FAFC', 100:'#F1F5F9', 200:'#E2E8F0', 300:'#CBD5E1', 400:'#94A3B8',
          500:'#64748B', 600:'#475569', 700:'#334155', 800:'#1E293B', 900:'#0F172A',
        },
        primary: {
          50:'#EEF2FF', 100:'#E0E7FF', 200:'#C7D2FE',
          500:'#6366F1', 600:'#4F46E5', 700:'#4338CA', 800:'#3730A3', 900:'#312E81',
        },
        success: { 50:'#ECFDF5', 200:'#A7F3D0', 600:'#059669', 800:'#065F46' },
        warning: { 50:'#FFFBEB', 200:'#FDE68A', 600:'#D97706', 800:'#92400E' },
        error:   { 50:'#FEF2F2', 200:'#FECACA', 600:'#DC2626', 800:'#991B1B' },
        info:    { 50:'#EFF6FF', 200:'#BFDBFE', 600:'#2563EB', 800:'#1E40AF' },
      },
      fontFamily: {
        sans: ['Inter','IBM Plex Sans','-apple-system','Segoe UI','Roboto','sans-serif'],
        mono: ['JetBrains Mono','IBM Plex Mono','ui-monospace','monospace'],
      },
      fontSize: {
        caption:   ['0.75rem',   { lineHeight:'1rem',     letterSpacing:'0.02em',  fontWeight:'500' }],
        small:     ['0.875rem',  { lineHeight:'1.25rem',  fontWeight:'400' }],
        body:      ['1rem',      { lineHeight:'1.5rem',   fontWeight:'400' }],
        h5:        ['1.125rem',  { lineHeight:'1.75rem',  fontWeight:'600' }],
        h4:        ['1.25rem',   { lineHeight:'1.75rem',  fontWeight:'600' }],
        h3:        ['1.5rem',    { lineHeight:'2rem',     letterSpacing:'-0.01em',  fontWeight:'600' }],
        h2:        ['1.875rem',  { lineHeight:'2.25rem',  letterSpacing:'-0.015em', fontWeight:'700' }],
        h1:        ['2.25rem',   { lineHeight:'2.75rem',  letterSpacing:'-0.02em',  fontWeight:'700' }],
        display:   ['3rem',      { lineHeight:'3.5rem',   letterSpacing:'-0.025em', fontWeight:'700' }],
      },
      spacing: {
        0.5:'0.125rem', 1:'0.25rem', 2:'0.5rem', 3:'0.75rem', 4:'1rem',
        5:'1.25rem', 6:'1.5rem', 8:'2rem', 10:'2.5rem', 12:'3rem',
        16:'4rem', 20:'5rem', 24:'6rem',
      },
      borderRadius: { sm:'4px', md:'6px', lg:'8px', xl:'12px', full:'9999px' },
      boxShadow: {
        xs:'0 1px 2px 0 rgb(15 23 42 / 0.04)',
        sm:'0 1px 3px 0 rgb(15 23 42 / 0.06), 0 1px 2px -1px rgb(15 23 42 / 0.04)',
        md:'0 4px 6px -1px rgb(15 23 42 / 0.06), 0 2px 4px -2px rgb(15 23 42 / 0.04)',
        lg:'0 10px 15px -3px rgb(15 23 42 / 0.08), 0 4px 6px -4px rgb(15 23 42 / 0.04)',
        xl:'0 20px 25px -5px rgb(15 23 42 / 0.10), 0 8px 10px -6px rgb(15 23 42 / 0.04)',
        focus:'0 0 0 3px rgb(79 70 229 / 0.20)',
      },
      transitionTimingFunction: { emphasized: 'cubic-bezier(0.2, 0.8, 0.2, 1)' },
      transitionDuration: { 80:'80ms', 120:'120ms', 240:'240ms' },
    },
  },
  plugins: [require('@tailwindcss/forms'), require('@tailwindcss/typography')],
};
```

### Overriding this guide per project

Drop your own `{workspace}/style_guides/web-design-system.md`. The loader's two-tier precedence means your file replaces this default entirely for that repo.

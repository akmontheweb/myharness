---
applies_to: [react, vue, angular, html]
---

## HTML Style Guide

### Source
- MDN Web Docs — HTML Reference & Layout Guides (https://developer.mozilla.org/en-US/docs/Web/HTML)

### Document structure
- Start every page with `<!DOCTYPE html>` (HTML5 doctype, lowercase or uppercase — be consistent).
- Specify the language: `<html lang="en">`. Screen readers and translation tools use it.
- Always include a `<title>` and a `<meta charset="utf-8">` early in `<head>`.
- Use a `<meta name="viewport" content="width=device-width, initial-scale=1">` on any document that ships to the browser — assume mobile first.

### Semantic markup
- Reach for semantic elements before generic ones: `<header>`, `<nav>`, `<main>`, `<article>`, `<section>`, `<aside>`, `<footer>`, `<figure>`, `<figcaption>`, `<time>`. A `<div>` is the choice of last resort.
- Headings communicate document structure — start at `<h1>` and don't skip levels. One `<h1>` per page (or per `<main>` landmark).
- Use `<button>` for actions, `<a href>` for navigation. Never style a `<div>` as a button — keyboard and assistive-tech behavior breaks.
- Use `<ul>` / `<ol>` for lists; `<dl>` for name/value pairs.
- Use `<table>` only for tabular data. Decorative or layout grids belong in CSS.

### Forms & inputs
- Every form control needs a `<label>` — either wrapping or referenced by `for`/`id`.
- Pick the right `type`: `email`, `tel`, `number`, `url`, `date`. Mobile keyboards and browser validation depend on it.
- Mark required fields with `required` plus a visible cue; use `aria-required` only when the native attribute is unavailable.
- Provide `autocomplete` hints — they're an accessibility win, not just a convenience.

### Accessibility
- Every image carries `alt`. Decorative images get `alt=""`; informative images describe content, not appearance.
- Don't use `tabindex` greater than 0 — it scrambles tab order. `tabindex="-1"` to make non-interactive elements focusable from script is fine.
- Use ARIA only when no native element does the job; ARIA on a semantic element is usually wrong and often actively harms screen readers.
- Ensure interactive elements have visible focus styles. Don't suppress `:focus-visible` without providing an alternative.

### Modern layout
- Use CSS Grid for two-dimensional layouts (page-level shells, dashboards), Flexbox for one-dimensional layouts (toolbars, navigation rows).
- Avoid float-based layout in new code.
- Prefer logical properties (`margin-inline`, `padding-block`) over physical ones (`margin-left`, `padding-top`) — they survive RTL/vertical writing modes.

### Formatting
- Lowercase element names and attribute names.
- Quote all attribute values (double quotes preferred).
- Close all void elements explicitly in XHTML/JSX contexts (`<img />`); in plain HTML, the closing slash is optional but the void element rules apply.
- 2-space indentation, mirroring the rest of the front-end stack.

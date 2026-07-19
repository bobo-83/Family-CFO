# ADR 0035: Angular Material for the dashboard UI

## Status

Accepted (UI migration; Phase 1 foundation shipped).

## Context

The dashboard (`apps/web`) has a custom design system (M20): CSS custom
properties in `styles.scss` for color, radius, and type. It is coherent
*where it is used*, but adoption drifted — roughly 15 of 20 pages hardcode
hex colors and roll their own cards/buttons/forms/tables instead of the
tokens, so pages look subtly (sometimes not so subtly) different from each
other. The request: one consistent, modern theme across every page.

Two ways to get there: (a) tighten the existing custom design system, or
(b) adopt a component library — Angular Material. Material was chosen: it
gives accessible, battle-tested components (forms, tables, dialogs, menus,
date pickers) with one M3 theme, removing the per-page bespoke CSS that
caused the drift in the first place.

## Decision

1. **Angular Material 3 (M3), themed to the brand.** `@angular/material` +
   `@angular/cdk` at the Angular version. The M3 theme's palette is
   *generated from the existing brand accent* (`#4f46e5`) via
   `ng generate @angular/material:m3-theme`, so Material components inherit
   the app's indigo identity rather than looking like stock Material. The
   theme lives in `styles.scss` (`mat.theme(...)`), fed by
   `src/_theme-colors.scss`.

2. **Animations via `provideAnimationsAsync()`** in `app.config.ts` —
   zoneless-compatible, and kept off the initial bundle until a component
   needs it.

3. **Phased migration, page by page.** The old design tokens stay in
   `styles.scss` so un-migrated pages are untouched while the rollout
   proceeds. Each page converts its bespoke HTML/SCSS to Material
   components in its own small PR. Phase 1 (this) is the foundation +
   `login` as the reference conversion. When the last page is migrated the
   now-dead custom element baselines can be removed, leaving the tokens
   only as inputs to the Material theme.

4. **Tests stay green throughout.** Page specs assert component logic and
   stable selectors (`input[formControlName]`, `button[type=submit]`),
   which Material preserves (`matInput` is still an `<input>`; `mat-*-button`
   is still a `<button>`). No test rewrites required for the conversion
   itself.

## Migration recipe (per page)

For each `pages/<x>/`:

1. In `<x>.ts`, add the Material modules the template needs to `imports`:
   `MatCardModule`, `MatFormFieldModule` + `MatInputModule`,
   `MatButtonModule`, `MatTableModule`, `MatMenuModule`, `MatDialog`, …
2. In `<x>.html`, replace bespoke markup:
   - card wrappers → `<mat-card appearance="outlined">` with
     `mat-card-header`/`-title`/`-content`.
   - `<label><span>…</span><input></label>` → `<mat-form-field
     appearance="outline"><mat-label>…</mat-label><input matInput></mat-form-field>`
     with `<mat-error>` for validation.
   - buttons → `mat-flat-button` (primary action), `mat-stroked-button`
     (secondary), `mat-icon-button` (icon-only), `mat-button` (text).
   - hand-rolled tables → `<table mat-table>` or keep simple lists with
     Material list components.
3. In `<x>.scss`, delete hardcoded colors and bespoke component CSS; keep
   only layout. Reference Material system vars where needed
   (`var(--mat-sys-on-surface-variant)`, `var(--mat-sys-error)`).
4. `npm test` + `npm run build`; the page's SCSS budget warning should
   drop as bespoke CSS is deleted.

## Consequences

- New runtime deps (`@angular/material`, `@angular/cdk`, `@angular/animations`)
  and a modest CSS/JS bundle increase, offset over time as bespoke page CSS
  is deleted.
- One accessible, consistent theme; forms/tables/dialogs stop being
  reinvented per page.
- The migration is long (≈19 pages remaining) but each page is an isolated,
  reviewable PR — no big-bang rewrite, and the app stays shippable between
  PRs.
- Ownership: while the migration is in flight it must be driven by a single
  contributor to avoid whole-dashboard merge conflicts.

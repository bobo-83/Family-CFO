# 0062 — iOS overflow screens live in our own More tab

Date: 2026-07-22
Status: Accepted

## Context

The app grew to up to eleven tabs (role-dependent). iPhone shows four plus a
system-provided "More" tab for the overflow — and that system container wraps
its own navigation controller around whatever it hosts. Screens that own a
`NavigationStack` (Settings and everything reached from it, e.g. the AI
runtime page) rendered TWO stacked navigation bars with two back buttons
(user report, 2026-07-22).

## Decision

The tab bar never overflows: primary tabs are Advisor, Overview, Accounts,
Bills, plus our own **More** tab — a single `NavigationStack` hosting a list
of the secondary screens (Income, Categories, Debts, Review, Budgets, Goals,
Settings). Pushed screens do NOT create their own stack (Income, Categorize,
Review, and Settings had body-level stacks removed); sheets inside them may
still own one, since a sheet is a new navigation context. The Review badge
moved to the More tab and its row.

## Rejected options

- **Keep the system More tab** — its extra navigation controller is not
  removable, and stripping the screens' stacks instead would break them on
  layouts where they ARE top-level tabs (iPad shows all tabs).
- **Fewer features per role so tabs always fit** — backwards: navigation
  should serve the features.

## Invariant

A screen reachable by push must never create its own `NavigationStack`; the
presenting context (tab root, More stack, or sheet) owns navigation. One nav
bar, one back button, everywhere.

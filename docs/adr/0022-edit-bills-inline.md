# ADR 0022: Edit bills inline from the Bills tab (M110)

## Status

Accepted. Extends the Bills model (ADR 0020) and the undo framework (ADR 0021).

## Context

The Bills tab let users add a bill (`+` → form), delete it (swipe), and file it
under a category (swipe → picker), but there was **no way to correct an existing
bill** — a wrong amount, a renamed subscription, a shifted due date meant delete +
re-add, which loses the row's identity and its undo history. The user asked to "make
edit to my bills."

The backend already had everything: `updateBill` (PATCH `/bills/{id}`) accepts
name / amount / frequency / next-due-date / category, and it writes an **undoable**
`bill.updated` audit event (ADR 0021). Only the iOS UI was missing.

## Decisions

1. **Tap a bill row to edit it** — the row shows a chevron and opens a sheet. Swipe
   still delete/categorizes; tap is the natural "open" gesture and keeps the
   destructive action behind a swipe.
2. **One `BillFormView` for add and edit**, driven by a `mode` (`.add` / `.edit(bill)`).
   The two flows differ only in title, button label ("Add" vs "Save"), and whether
   fields start blank or pre-filled — sharing the form keeps them identical (the
   "uniform experience" rule). It replaces the old single-purpose `AddBillView`.
3. **Reuse `updateBill`; add no endpoint.** The iOS `BillsAPI.updateBill` wraps the
   same operation `setBillCategory` already uses.
4. **Category is set-only from the form, on edit as on add.** The generated client
   omits a nil `categoryId` rather than sending `null`, so picking "None" on an
   already-filed bill *keeps* the current category — it does not clear it. Clearing
   a category stays a dashboard action, matching the existing Categorize constraint.
   The form says nothing misleading: "None" simply means "don't set one here."

## Invariant

> Editing a bill goes through `updateBill`, so every edit is an undoable
> `bill.updated` action. The edit form can set but never clear a bill's category.

## Rejected

- **A separate `EditBillView`** — duplicates the form; drifts from Add over time.
- **Inline field editing on the row** — cramped, no clear commit point, and no
  single sheet to reuse for Add.
- **Send `null` to clear the category from the form** — the generated client can't,
  and clearing is deliberately a dashboard action; don't special-case it here.

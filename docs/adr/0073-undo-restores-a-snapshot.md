# ADR 0073: Undo restores a snapshot, it does not invert an operation

## Status

Accepted. Refines ADR 0023 (every mutation is undoable) by defining what
"undone" has to mean.

## Context

ADR 0021 built the undo framework and ADR 0023 made coverage a rule enforced in
code: every audited action is classified UNDOABLE / IRREVERSIBLE / PENDING, and
an UNDOABLE action must carry an undo token or the write raises. Both are about
**whether** an undo exists. Neither says what it must **do**.

Left undefined, tokens were written to invert the operation the author had in
mind, and each captured a little less than the action actually changed. An
audit-coverage review of all 109 state-changing routes found six that are
classified UNDOABLE and cannot restore the prior state:

- **`card_statement.recorded`** mints a *delete* token, but the write is an
  upsert. Correcting a statement and then undoing **deletes** it, destroying the
  original figures rather than restoring them — the undo button causes the data
  loss it exists to prevent.
- **`income_profile.deleted`** cascade-deletes every RSU grant and vest event
  for the earner; the token serialises only the profile's own fields. The undo
  appears to succeed and the schedule is permanently gone.
- **`account.updated`** omits `next_payment_due_date`, which the same PATCH
  writes. Adding an emergency-fund designation to an account that had none is
  also not undoable, because `update_account` reads `None` as "leave unchanged".
- **`household.updated`** carries three fields; the endpoint can change five.
  `reserve_committed_savings` and `language` are audited as changed and silently
  not restored.
- **`category.deleted`** nulls `category_id` on every referencing transaction
  and deletes the budget envelope; the token restores only the name, under a new
  id.
- **`account.deleted`** drops the account's balance history, which no token
  carries.

Every one is the same defect: **a token that captures less than the action
changed**. Because there was no stated rule, each was individually plausible and
only visible to someone reading the write path and the token side by side.

## Decision

**An undo restores the state as it was immediately before the action. It does
not undo the operation; it returns to a point in time.**

Concretely:

1. **A token carries prior VALUES, not an inverse instruction.** "Delete what
   was created" is acceptable only where creation is genuinely the whole change.
   An upsert must carry what it overwrote.
2. **The footprint is everything the action changed**, including cascades. If
   deleting a profile removes its grants, the token carries the grants. If a
   token cannot express the footprint, the action is **IRREVERSIBLE** — that is
   an honest answer and a silently partial undo is not.
3. **Later edits are not protected.** Returning to a point in time overwrites
   what happened after it; that is what the user asked for. An undo is not
   "reverse only my changes" — it is the same operation a restore performs
   (issue #62), at the granularity of one action instead of a database.
4. **IRREVERSIBLE is a legitimate result, not a failure.** Where reversal cannot
   hold — a bank sync's imported rows return on the next sync, because ADR 0015
   dedupes against what is *stored* — the honest classification is IRREVERSIBLE
   with the reason recorded beside the entry. An undo that silently reverts
   overnight is worse than none.

## Enforcement

ADR 0023's gate checks that a token exists. This rule is about what it contains,
which a type cannot express — so it is enforced by test:

> For every UNDOABLE action: capture the state, perform the action, undo it,
> and assert the state matches what was captured.

A round-trip test per action is the only check that catches a token capturing
too little, because the token always *looks* plausible next to the code that
built it.

## Consequences

- **Tokens get larger**, and some become unbounded. A bulk operation touching
  tens of thousands of rows cannot carry every prior value in one `undo_token`
  text column. Where that is reached, either bound the operation, spill the
  snapshot elsewhere and reference it, or classify IRREVERSIBLE above the bound.
  What is not acceptable is a token that silently covers part of the change.
- **The six actions above are now defects** with something to be measured
  against, rather than judgement calls.
- **Undo and restore become the same idea** at different scales, which is why
  #62's restore records the boundary it crossed: both answer "put it back as it
  was", and both owe the reader an honest account of what that discarded.

## Rejected

- **Operation-inverse semantics** ("undo only the fields this action wrote,
  leave later edits alone") — defensible in isolation, but it makes undo's
  meaning depend on what happened afterwards, so the same button produces a
  different result depending on history. A point in time is predictable; a
  partial reversal is not.
- **Leaving it to each author's judgement** — that is the status quo, and it
  produced six actions that claim to be undoable and are not.

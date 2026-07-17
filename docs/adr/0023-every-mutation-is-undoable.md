# ADR 0023: Every mutation is undoable (undo-completeness rule) (M110)

## Status

Accepted. Extends ADR 0021 (the undo framework) and makes its coverage a rule
enforced in code, not a per-endpoint choice.

## Context

ADR 0021 built the undo framework: a mutation records an `undo_token` on its audit
event, and the Activity screen offers **Undo**. But wiring the token was left to
each write path, so coverage drifted. A user filed a bug: editing a transaction's
**note** showed "Edited the note on …" in Activity with **no Undo button**, while
category and delete actions right next to it were undoable. Auditing every
`write_audit` call showed the token was wired for bills, categories, accounts and
budgets — but **not** for transaction note/duplicate edits, transaction deletes,
income, memories, members, connections, income-analysis, or household edits.

Nothing prevented a new mutation from shipping without undo, and nothing flagged
the ones that already had.

## Decisions

1. **Undo is the default; every state change must be reversible.** A user action
   that only changes household data (a note, a category, a bill, an income source,
   a memory, an account…) records an `undo_token` and can be reversed from Activity.
2. **Classify every audit action, and enforce it at write time.**
   `undo_actions.UNDO_POLICY` maps **every** audit action to one of:
   - `UNDOABLE` — records a token; reversible now.
   - `IRREVERSIBLE` — a real-world side effect that genuinely cannot be reversed
     (a login, a paired/revoked device, a snapshot written to or deleted from the
     NAS, a revealed secret, a generated document) **or** a change that would
     require replaying a secret we refuse to store (backup credentials). Each entry
     carries a one-line reason.
   - `PENDING` — should be undoable, not yet wired. Tracked debt, frozen so it can
     only shrink.
   `audit.write_audit` calls `require_classified(action)` and **rejects an
   unclassified action**, and rejects an `UNDOABLE` action written **without** a
   token. A new mutation therefore cannot ship without a deliberate undo decision.
3. **Transactions are fully undoable (this bug).** Every `transaction.updated`
   edit restores the transaction's prior field values (note, merchant, description,
   category, duplicate flag, amount, account, date); `transaction.deleted`
   re-inserts the row with its **original id and aggregator id**, note, attachment,
   category and duplicate flag, so references survive and bank dedupe still
   recognises it. Income and advisor memories are wired the same way via the
   framework's generic recreate/restore ops.

## Enforcement (prevents recurrence)

- Runtime: `write_audit` raises on an unclassified action or an `UNDOABLE` action
  missing its token.
- Tests (`test_undo_completeness.py`): AST-scans the package for every action the
  code emits and asserts each is classified; asserts the `PENDING` set only shrinks
  (a new action may not join it); asserts `write_audit` rejects the two error cases.

## Invariant

> Every audit action is classified in `UNDO_POLICY`. A state-changing action is
> `UNDOABLE` and records a token; an action may be `IRREVERSIBLE` only for a
> genuine external side effect (or an un-replayable secret). `PENDING` is tracked
> debt that only shrinks — new mutations are never `PENDING`.

## Rejected

- **Leave undo per-endpoint (the status quo)** — coverage silently drifted; that's
  the bug.
- **Mark the unwired actions `IRREVERSIBLE`** — dishonest; income/member/memory
  edits *are* reversible. `PENDING` names the debt without hiding it.
- **A soft lint instead of a runtime guard** — a lint is skippable; the write-time
  check makes shipping a non-undoable mutation fail a test that already exercises
  the endpoint.

## PENDING drained (M117)

The tracked debt was fully wired in M117 — the PENDING set is now **empty** and
the completeness test freezes it empty, so a new mutation must ship UNDOABLE.
Notable reversal semantics added:

- **transaction.created** → delete; **attachment_added** → restore the prior
  transaction fields (clears the attachment and the auto-filled note; the file
  on disk is orphaned, not data).
- **bill_suggestion.dismissed** → remove the dismissal row (the suggestion
  reappears); **account.balance_recorded** → delete the snapshot (the prior one
  becomes current).
- **income_override.set** → restore the previous verdict or clear;
  **income_profile.created/deleted** → delete/recreate;
  **income_tax_settings.updated** and **household.updated** → restore prior
  settings.
- **member.created** → remove the membership (the user row is left — harmless,
  and removal is itself undoable); **member.removed** → re-insert the
  membership (the user row survives removal by design);
  **member.role_changed** → restore the prior role.
- **ai_runtime.updated** → restore the prior config (or clear it when it was
  the first); **connection.created** → delete the connection;
  **import.applied** → flip the import's transactions back to pending and
  restore its status.

Two actions were honestly reclassified IRREVERSIBLE rather than pretend:
**ai_runtime.model_applied** (an operational model swap ran — vLLM reload,
model downloads) and **import.discarded** (staged rows were bulk-deleted;
re-uploading the file is the honest redo — stuffing an unbounded row set into
an undo token is not).

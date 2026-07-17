# ADR 0021: Activity-log undo framework (M108)

## Status

Accepted. Extends the M101 Activity/History screen.

## Context

The Activity screen lists every recorded action. Users expect to **undo** the
reversible ones (delete a bill, rename an account, recategorize) directly from
there. Initially only a transaction recategorize was undoable — everything else
showed no Undo, which read as inconsistent and unforgiving.

## Decision

- **Each reversible mutation records an `undo_token`** (JSON) on its audit event
  describing how to reverse it. A single dispatcher, `undo_actions.reverse`, applies
  the inverse. Three generic shapes cover the household's own records:
  - `create` → reverse is `delete` (`{op:"delete", entity, id}`)
  - `delete` → reverse is `recreate` (`{op:"recreate", entity, data}`)
  - `update` → reverse is `restore` prior fields (`{op:"restore", entity, id, data}`)
  - plus a transaction-recategorize special case (restore the prior category id).
- **Covered:** bills, categories, accounts, budgets (create/update/delete) and
  transaction recategorize. New actions are a **one-line token** on the audit event,
  not a bespoke handler — the reverse logic is centralized and tested.
- **Inherently-irreversible actions record no token** — a login, a backup or restore
  that already ran, a revealed key. The UI shows Undo only when
  `undo_token is not None and reverted_at is None`, so these correctly have none.
- **The low-level write functions stay unconditional**; the undo policy lives in
  `undo_actions` so `create_*/update_*/delete_*` remain directly unit-testable.

## Invariant (prevents recurrence)

> **A reversible data mutation MUST record an `undo_token`; an irreversible action
> MUST NOT. Add reversibility by emitting a token via `undo_actions`, never by
> special-casing the undo endpoint.**

## Guardrail tests

- `test_undo_actions.py` — reverses bill delete/create/update, category delete,
  budget update; asserts an unknown/irreversible token is *refused* (not a silent
  no-op).

## Rejected

- **Per-action bespoke undo handlers** — unmaintainable; every new action would touch
  the undo endpoint.
- **Full event-sourcing / reverse-op log** — overkill for reversing simple CRUD on
  household records.

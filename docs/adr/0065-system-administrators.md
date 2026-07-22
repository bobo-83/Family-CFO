# 0065 — System administrators: box-global powers live on users, not roles

Date: 2026-07-22
Status: Accepted

## Context

One vLLM serves every household on the box, so a model swap is box-GLOBAL:
it restarts the AI for everyone, downloads tens of GB, and repoints every
household's runtime config. Yet the swap was guarded by a HOUSEHOLD role
right (`ai_runtime.manage`) — which the demo household's Admin also held,
and the demo's credentials are public (repo docs). Any visitor could swap
the real family's model. An earlier hotfix stripped the right from the demo
role; the user rejected that as the wrong shape ("the proper fix is a new
system admin role") — correctly: the flaw was scoping a box-global power to
a per-household concept at all.

## Decision

- **`system_admins` table** (migration 0066): a USER-scoped roster,
  independent of households. A household admin can also be a system admin —
  the grant is additive, by email, targeting existing users.
- **Bootstrap**: whoever creates the FIRST household on a fresh box becomes
  its first system admin automatically (there is nobody else to grant it).
  The demo seed bypasses this path, so showcase credentials never qualify.
  Existing deployments do the one-time grant operationally (SQL insert),
  after which the roster manages itself.
- **Management API**: `GET/POST /system/admins`, `DELETE
  /system/admins/{user_id}` — all require being on the roster; the last
  admin can never be revoked (self-lockout guard, enforced on the undo path
  too). Grant and revoke are audited and UNDOABLE (ADR 0023). Management UI
  lives on the dashboard's Users page (admin surface, ADR 0025 exception).
- **Rights resolution**: `ai_runtime.manage` and the new `system.admin`
  pseudo-right are injected into a session's effective rights exactly when
  the user is on the roster — and stripped from any household role that
  still carries the legacy string. Clients keep gating on rights and needed
  no changes for the runtime pages (a fresh grant takes effect at next
  sign-in for stored login state; server checks are per-request).
- `ai_runtime.manage` left `ALL_RIGHTS`/presets; the role editor silently
  drops box rights from submitted role definitions instead of rejecting
  legacy payloads.

## Rejected options

- **Stripping the right from the demo role only** (the hotfix) — fights the
  whole test suite's rights model and leaves the category error in place:
  any future household could still hold a box-global power.
- **A deployment env var naming an operator household** — households are the
  wrong unit; operators are people, and the roster needs management UX
  (grant a second admin, revoke a departed one) that env vars can't offer.

## Amendment (same day): backups are box-global too

Backup, restore, key reveal, and backup deletion operate on the WHOLE box
database — every household — via SMB to the family's NAS (M98), yet were
gated by the household right `backups.manage`, which the demo Admin also
held (including "reveal encryption key"). `backups.manage` joined
`BOX_RIGHTS`: only system admins hold it, resolution/role-editor/client
gating all follow automatically. The scheduler's automatic backups are
server-side and unaffected. Household-scoped data stays household-gated —
e.g. the advisor's study job (checked the same day) is per-household
end-to-end and needs no box gating.

## Invariant

A box-global power is granted only through the system-admin roster. No
household role may ever confer one, and the roster never empties. Current
box rights: `system.admin`, `ai_runtime.manage`, `backups.manage`.

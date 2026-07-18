# ADR 0034: Rights-based roles — custom roles per household, screens guarded by rights

## Status

Accepted.

## Context

Permissions were hardcoded role names: `require_role("owner", "adult")` at ~80
endpoints and a fixed `RolePolicy` on iOS, with four fixed roles
(`owner/adult/viewer/child`). That model can't answer the household's real
questions: *"my wife manages budgets and bills but shouldn't touch accounts,
loans, or backups"* — an `adult` could manage accounts, and nothing but `owner`
existed above it. Custom roles were impossible, and screen sections were gated by
role-name checks scattered across both clients.

## Decision

**Three layers: RIGHTS (atomic capabilities) are bundled into ROLES (built-in
presets + household-defined custom roles) which are assigned to USERS. Every API
mutation and every client screen/section is guarded by a right — never by a role
name. Sign out is never gated.**

### Rights catalog (code-defined, `family_cfo_api/rights.py`)

- `finances.view` — see money screens (all presets include it; enforced at
  client navigation; server view endpoints require membership)
- `advisor.use` — chat, voice, photo scans
- `transactions.manage` — categorize, review, notes/attachments
- `bills.manage`, `budgets.manage`, `goals.manage`, `categories.manage`,
  `income.manage`
- `imports.manage` — statement/CSV imports (can change balances → not in User)
- `accounts.manage` — add/edit/remove accounts AND loans, balances, scans
- `connections.manage` — bank sync
- `members.manage`, `roles.manage`, `devices.manage` (revoke/pair-for-others;
  pairing your own device needs only membership)
- `backups.manage`, `audit.view` (activity + undo), `household.settings.manage`
  (tax, emergency fund, household prefs), `ai_runtime.manage`, `reports.manage`

### Roles

- Stored per household (`roles` table: name, rights, `built_in`). **Each
  household defines its own custom roles.**
- Built-in presets seeded per household: **Admin** (every right, immutable,
  undeletable), **User** (view + advisor + transactions/bills/budgets/goals/
  categories/income — no accounts, imports, connections, or any admin right),
  **Viewer** (view + advisor), **Child** (view).
- Custom roles: any subset of rights; deletable only while unassigned. Role
  create/update/delete is audited and undoable (ADR 0023).
- Legacy mapping: `owner→Admin`, `adult→User`, `viewer→Viewer`, `child→Child`.
  Migration `0060` seeds presets and backfills `role_id`. NOTE: an `adult`
  deliberately LOSES accounts/imports/connections management — that is the point
  of the change. The legacy `role` string remains derived for wire compat.

### Enforcement

- Server: `require_right("x")` / `require_any_right(...)` replace `require_role`
  everywhere; `SessionContext` carries the resolved `rights` set.
- Clients receive `rights` in the auth-session and device-credential responses
  and gate navigation, tabs, and in-screen sections (add/edit/delete buttons)
  with them. The server remains the actual guard; client gating is UX.
- Settings stays visible to everyone; each SECTION inside is right-gated;
  **Sign out / Unpair is always available**.
- Old iOS pairings that stored only a legacy role fall back to that role's
  preset rights.

## Invariant

> No endpoint or client surface checks a role NAME for permission — only rights.
> Admin is a complete, immutable superset; sign out is never right-gated; a
> custom role can never grant a right its creator's household doesn't define.

## Rejected

- **Two hardcoded roles (Admin/User).** Doesn't answer "should my wife restore
  backups?" for the NEXT household; rights make the bundles editable.
- **Per-user rights (no roles).** Auditing "who can do what" degrades into N
  user-by-user reviews; roles keep assignments legible.
- **DB-defined rights catalog.** Rights only mean something where code enforces
  them; a DB row can't invent an enforcement point. Catalog lives in code,
  bundles live in the DB.
- **Gating view endpoints per right server-side.** ~100 GETs for no threat-model
  gain inside a family household; membership already gates reads. Revisit if
  households ever need read-secrecy between members.

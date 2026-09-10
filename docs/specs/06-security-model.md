# Security Model

Family CFO is designed for local self-hosted use with sensitive household financial data.

## Data Classification

### Restricted

- Credentials
- Session secrets
- Pairing secrets
- Financial documents
- Bank statements
- Tax documents
- Investment data
- Payroll data

### Sensitive

- Transactions
- Account balances
- Goals
- Bills
- Reports
- AI conversation history

### Internal

- Runtime configuration
- Model configuration
- Audit events

### Public

- Documentation
- Open source code
- Synthetic test fixtures

## Required Controls

- HTTPS for app-to-server communication.
- Secure pairing for mobile clients.
- Local authentication.
- Rights-based access control (ADR 0034): atomic RIGHTS are bundled into ROLES
  (built-in Admin/User/Viewer/Child presets plus per-household custom roles)
  assigned to users. Every mutation endpoint and every client screen/section is
  guarded by a right, never a role name; Admin holds every right and is
  immutable; sign out is never gated.
- Encrypted backups.
- Secret redaction in logs.
- No telemetry.
- No cloud AI calls for sensitive data unless a future explicit opt-in ADR permits it.
- **No tool — human-run or AI — asks the user for a secret.** See below.

## Credential Handling (Humans and AI Agents Alike)

Nothing in this system may **ask the user for a password, passphrase, private
key, or API token**, and nothing may accept one if offered. This applies to the
application, the operational scripts, and to AI coding agents working on the
repository (`AGENTS.md`).

The reasoning is the same one that keeps the household's finances on their own
hardware: a secret that is typed into a prompt has been disclosed. It comes to
rest in transcripts, shell history, process listings, scrollback and logs — all
of which outlive the task, and none of which the user is thinking about at the
moment they are asked. An agent that asks is not being helpful; it is
manufacturing an exposure that the design should have made unnecessary.

**Required instead:**

| Need | Correct mechanism | Never |
|---|---|---|
| SSH to the box | `~/.ssh/config` + `ssh-agent`; key authorised once by the user's own `ssh-copy-id` | Prompting for a password; storing `SSH_KEY`/passwords in a config file |
| Device credential (iPhone) | QR pairing → revocable token in the Keychain (M83) | Typing a server password into the app |
| Any third-party CLI | That tool's own login (`gh auth login`, `docker login`), run by the user | An agent collecting the secret and passing it on |
| Deploy configuration | `.deploy.env` — a **destination**, not a credential | A secret in any committed or gitignored repo file |

Operational scripts must **defer** to the platform's credential store rather
than reimplement or override it: `scripts/deploy.sh` and `scripts/patch.sh`
leave `SSH_USER`/`SSH_PORT`/`SSH_KEY` unset so `ssh` resolves them from
`~/.ssh/config`, which is what allows a deploy to run with no secret anywhere in
the repo.

If a change cannot work without a secret passing through a prompt, that is a
design defect and the design must change — not the rule. If a secret is
disclosed regardless, treat it as an incident: say so, and rotate it.

## Incomplete Aggregate Privacy Boundary (M124, ADR 0076)

Qualified aggregates do not change authentication, authorization, encryption,
network, or household-isolation boundaries. `HouseholdLockedError` remains HTTP
423; aggregate qualification catches only the known unreadable-cell exception
and must not turn a locked household, database failure, crypto failure,
cancellation, or unexpected error into a partial response.

Unreadable source identity `(household_id, table, row_id, column)` exists only
inside the current request so counts can be deduplicated and scoped. Public
responses, advisor payloads, calculation JSON, reviews, memories, warnings, and
ordinary logs expose only counts and generic repair language. They never expose
ciphertext, plaintext, sealed account/merchant names, guessed amounts, or source
table/row/column identity. Controlled server diagnostics may log household,
table, row, and column identifiers needed for repair, but never the token or
neighboring sealed text.

Background strict readers isolate `sealed_amount_unreadable` at household/job
scope, record only a non-sensitive retryable skip, preserve prior durable output
(including vectors and cached reviews), and continue to other households. They
do not persist invented zeros. Synthetic corruption fixtures contain no
personal data and require no secrets.

## Box-Global Backup Security Boundary (issue #116, ADR 0077)

Backup configuration, inventory, recovery status, destination checks, create,
restore, maintenance, and local/remote deletion are whole-box operations. Every
HTTP route requires `BACKUPS_MANAGE`; ADR 0065 grants that box right only to a
system administrator, regardless of the administrator's active household role.
A household role never implies it.

Required controls:

- The persisted singleton and retention journal carry no household foreign key.
  Existing household-scoped audit records may identify the acting administrator,
  but automatic pruning is recorded only in the box-global operational journal.
- SMB passwords stay encrypted at rest and are never returned, logged, journaled,
  included in audit summaries, or captured for undo. Omitted or JSON-null
  password preserves the ciphertext; explicit empty input retains the existing
  password-clear behavior without clearing unrelated destination fields.
- Public responses, audits, journal detail, and ordinary logs expose stable
  allowlisted reason codes and constant friendly text only. Raw exceptions, UNC
  paths, host/share identity in error text, usernames, credentials, archive
  contents, and financial data are prohibited.
- Local archive paths must resolve beneath `Settings.backup_dir`; remote
  restore/delete retains basename and `.enc` validation. Automatic management
  recognizes only supported Family CFO archive names. Orphaned, unrecognized,
  unreadable, size-mismatched, future-version, and other anomalous evidence is
  protected rather than deleted.
- One cross-process operation lock excludes backup, restore, maintenance, and
  delete. The synchronous I/O owner retains the lock across HTTP cancellation,
  observes a positive I/O deadline, and revalidates PostgreSQL advisory-lock
  ownership before destructive phases. Connection loss fails closed.
- Local and SMB writes use same-destination partial files and atomic promotion;
  caught failure cleans a partial best-effort and never deletes an uncertain
  promoted final archive.
- Automatic pruning is irreversible. File deletion decisions are deterministic,
  generation-scoped, idempotently journaled, preserve the newest eligible
  archive and anomalies, and are disabled after upgrade/restore until explicit
  system-administrator confirmation. `backup.config_updated` stays classified
  irreversible and its audit summary names changed groups without values.
- Restore captures current operational settings, including encrypted credential,
  in memory only; after database rollback it re-applies them, rotates destination
  generations, and pauses pruning for review. No plaintext or durable temporary
  secret copy is permitted.
- Recovery status proves only current inventory visibility plus bounded read
  probing. It never claims key availability, authenticated decryption, archive
  integrity/completeness, migration viability, or successful destructive
  restore. Capacity is caller-available observation subject to TOCTOU, never a
  guarantee.

Ordinary household advisor chat does not receive backup configuration,
inventory, or recovery status. Its executor lacks authenticated box-global
system-administrator context. A future administrator advisor requires a separate
security design and ADR; tests must not assert that a tool is absent.

Synthetic fixtures and error strings contain no credentials or personal data.
PostgreSQL lock integration and SMB behavior use tool-owned/test credentials or
platform credential stores; no human or agent is asked to disclose a secret.

## Mobile Authentication

The iPhone app should use Face ID where available for local unlock. Server authorization remains token-based and revocable.

## Pairing

Initial pairing should use a short-lived QR code from the home server. Pairing creates a device record and scoped credentials.

## Logging

Logs must avoid sensitive raw financial data, credentials, document contents, and model prompts containing household details.

## Threat Model

See `docs/security/threat-model.md`.

## Repository hygiene: no personal identifiers (ADR 0030)

Tracked files carry no maintainer- or deployment-specific values — Apple team
ids, real private IPs/hostnames, personal home paths — and no secrets. These are
supplied at build/run time (env / gitignored `.deploy.env`) or shown as
placeholders. Enforced by `scripts/check-repo-hygiene.sh` (pattern-based; runs in
the Security workflow and as a pre-commit hook) alongside gitleaks for secrets.

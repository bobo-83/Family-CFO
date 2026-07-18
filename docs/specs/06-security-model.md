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
- Role-based users.
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

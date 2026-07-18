# Security Policy

Family CFO is a self-hosted, privacy-first application that handles a family's
financial data. We take security reports seriously.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Use GitHub's private vulnerability reporting instead: go to the repository's
**Security** tab → **Report a vulnerability**. That opens a private channel with
the maintainers.

Please include:

- what the issue is and the impact you see,
- steps to reproduce (or a proof of concept),
- affected version (the value at `/api/v1/health`, or the repo `VERSION`),
- any suggested remediation.

We aim to acknowledge a report within a few days and to keep you updated as we
investigate and fix.

## Scope

This project is deployed on a household's own hardware and is not exposed to the
internet by design (it runs behind the home LAN / a personal VPN). Reports are
most useful when they concern:

- authentication, pairing, or session handling,
- the deterministic financial engine producing wrong money figures,
- data exposure between households, or in backups, logs, or the audit trail,
- the AI runtime or tool-calling being coerced into unsafe actions,
- dependency vulnerabilities not already surfaced by the CI audit.

## What we already do

- Secrets are required via environment, never shipped as defaults (the stack
  refuses to start without `POSTGRES_PASSWORD`; backups need an encryption key).
- CI scans every change for committed secrets (gitleaks), audits third-party
  dependencies (`pip-audit`), and blocks personal identifiers from the repo
  (`scripts/check-repo-hygiene.sh`, ADR 0030).
- Backups are encrypted; the audit log records sensitive mutations and every one
  is undoable (ADR 0023).

Thank you for helping keep families' data safe.

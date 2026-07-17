# Angular Dashboard Spec

## Platform

Angular desktop web dashboard running inside Docker.

## Responsibilities

- Reports
- Transaction management
- Statement review
- Imports
- Administration
- Settings
- AI model configuration
- Backup management
- User management

## Information Architecture

Initial sections:

- Overview
- Cash Flow
- Transactions
- Accounts
- Goals
- Reports
- Imports
- AI Models
- Backups
- Settings
- Users

## UX Principles

- Work-focused dashboard.
- Dense but readable information.
- Clear review queues for imports and OCR results.
- Explanations remain attached to calculations.
- No hidden cloud dependencies.

## Generated API Client

Angular client code must be generated from `shared/openapi/family-cfo.v1.yaml`.

## Acceptance Criteria

- Dashboard can guide initial onboarding.
- Dashboard can manage local AI runtime configuration.
- Dashboard can review imports before they affect financial state.
- Dashboard can revoke paired devices.

## Debts & Loans page (M116)

The dashboard counterpart of the iOS loan editor (ADR 0025): lists loan-type
accounts (mortgage, auto, student, 401(k), other) with total-owed and
monthly-payment summaries (401(k) loans excluded — payroll-deducted, owed to
yourself); add/edit/delete; statement scan via `scanLoanStatement` from a file
picker or a pasted image/PDF (ADR 0028, `window:paste` while the form is open);
and the loan's end entered as a date OR "N payments remaining" (M115 — both
store `maturity_date`; the conversion helpers are exact inverses, test-guarded).
A liability's balance is recorded NEGATIVE (the amount owed), matching iOS.

## Goals page additions (M118/M119)

Goals carry an optional planned monthly contribution (create field + per-goal
inline edit) feeding the Overview's "Left to spend this month" savings term
(ADR 0027), and a Delete action (undoable, ADR 0023). Full goal management
exists on both clients (ADR 0025).

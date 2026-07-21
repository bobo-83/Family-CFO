# ADR 0057: Paste/scan an account statement to prefill the add-account form

## Status

Accepted. Extends ADR 0028 (every statement input accepts paste) and the
M77/M110 statement-scan family (W2, loan/lease, bill).

## Context

Adding a manual asset account (an HSA, a savings account, a brokerage) meant
retyping the name, picking the type, and copying the balance from a statement
by hand. The household asked to paste an HSA statement into the accounts page
and have the new-account form prefilled, then confirm and save.

The scan machinery already exists: the on-box vision model reads W2s, loan/
lease statements, and bills into *candidate* values that the user confirms —
a model never writes financial ground truth directly (M73's rule).

## Decision

A new `POST /accounts/scan` (`scanAccountStatement`, right:
`accounts.manage`) mirrors the loan-statement scan: images go straight to the
vision model; PDFs are rasterized page-by-page (`pdf_page_pngs`) and pages are
walked until one yields a name or balance. The prompt extracts
`{account_name, account_type, balance, statement_date}` with a **controlled
type vocabulary** mapped (with synonyms — "Health Savings Account" → `hsa`,
"Investment" → `brokerage`, "401k/IRA" → `retirement`, "money market" →
`savings`…) onto the app's asset `AccountType`s; anything unrecognized stays
null rather than guessed. Negative or zero balances are dropped (an asset
prefill never proposes a negative balance — a liability statement belongs in
Debts' loan scan).

Both clients wire it into the add-account form (ADR 0025 parity): iOS's
Add-account sheet gains paste-a-screenshot and photo-library scan; the web
accounts page accepts Ctrl/⌘+V paste and a file picker (the ADR 0028
pattern). The scan only PREFILLS the form; the user edits and saves.

## Invariant

> A statement scan produces candidates only — the add-account form is
> prefilled, never submitted, by a model. Unrecognized account types stay
> empty rather than guessed, and an asset prefill never carries a negative
> balance.

## Rejected

- **Auto-creating the account from the scan**: violates the confirm-first rule
  every other scan follows (M73/M76).
- **Extending `scanLoanStatement` to cover assets**: its result shape (payoff,
  APR, payments remaining, lease derivation) is loan-specific; a shared
  endpoint would fork on every field.
- **Free-text account_type from the model**: a controlled vocabulary + synonym
  map keeps the type either correct or empty — never an invented category.

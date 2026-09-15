# PRD

## Product

Family CFO is a self-hosted financial advisor for households that combines deterministic financial calculations with local AI explanations.

## Target Users

- Families who want private financial guidance.
- Self-hosting users who want control over their data and AI runtime.
- Households with multiple accounts, debts, goals, and long-term planning needs.

## Problem

Most personal finance apps focus on transaction categorization and monthly budgets. They do not reason across income, debts, savings goals, emergency funds, investments, retirement, and tradeoffs in a way that feels like a trusted advisor.

## Goals

- Provide plain-language guidance grounded in auditable calculations.
- Keep all sensitive financial data local to the user-controlled home server.
- Support iPhone capture and chat workflows.
- Support a desktop dashboard for review, administration, reports, and imports.
- Make every major component replaceable.

## Non-Goals

- Autonomous transactions or trades.
- Cloud-only AI.
- Selling or sharing user data.
- Mandatory bank credential aggregation.
- Tax, legal, or investment advice presented as professional advice.

## Primary User Journeys

### Purchase Advisor

The user photographs or describes a possible purchase. Family CFO estimates impact on discretionary cash flow, emergency fund, debt payoff, savings goals, and long-term plans. The response includes assumptions, tradeoffs, confidence, and alternatives.

### Weekly Report

Family CFO summarizes wins, risks, unusual spending, goal progress, and recommended actions.

### Monthly Financial Review

The desktop dashboard shows cash flow, net worth, debt, investments, and goal progress with explainable recommendations.

### Scenario Planning

The user asks "Can we retire at 55?" or "Should we refinance?" The financial engine calculates scenarios and the LLM explains the result.

## Functional Requirements

- Chat with financial context.
- Receipt and product capture from iPhone.
- CSV, PDF, OFX, and QFX import roadmap.
- Accounts, assets, liabilities, transactions, bills, goals, and household members.
- Weekly, monthly, and annual reports.
- Deterministic projections for cash flow, retirement, debt payoff, net worth, and savings goals.
- Local AI runtime through an abstraction.
- Onboarding wizard for self-hosted setup.

## Privacy Requirements

- No telemetry.
- No advertisements.
- No required cloud services for sensitive data.
- No third-party upload of financial documents.
- Local-first storage and reasoning.

## Explainability Requirements

Every recommendation includes:

- Direct answer
- Reasoning summary
- Assumptions
- Financial impacts
- Tradeoffs
- Alternatives
- Confidence
- Relevant warnings

## Acceptance Criteria

- The repository contains the full Spec Kit before product implementation.
- Initial API contract covers health, pairing, household context, purchase analysis, chat, imports, goals, and reports.
- Financial calculations are specified separately from LLM behavior.
- Security model covers pairing, authentication, local storage, backups, and secrets.

## Qualified Incomplete Monetary Aggregates (M124, issue #119)

When a selected sealed transaction or card-statement amount cannot be read, a
read-side aggregate must preserve useful readable facts without presenting an
unsafe answer as exact (ADR 0076).

Product requirements:

- Additive descriptive monetary leaves return their readable sum plus the count
  of relevant unreadable stored amount cells. Partial zero is visibly partial;
  it is never an empty-state or lower-bound claim.
- Any affordability, coverage, status, percentage, ranking, forecast, tax,
  savings, budget, cash-flow, or other decision that could change because of an
  omission is unavailable. Unaffected sibling sections remain usable.
- Raw/detail, export, equality/filter, indexing, write-comparison, and unstable
  candidate/ranking operations with no honest partial product retain the
  documented `sealed_amount_unreadable` refusal.
- The API owns every aggregate and derived decision. Web, iPhone/iPad, Watch,
  widgets, and the advisor present the same result and never reconstruct a null
  decision or sum qualified values/counts locally.
- Partial and unavailable states are explicit, localized, accessible, and free
  of positive/negative styling, progress, or navigation that implies a decision.
- The advisor may explain a partial descriptive value only with its limitation
  and must not recommend spending, affordability, coverage, tax, budget health,
  savings cuts, or runway from incomplete inputs.
- Omission disclosures reveal counts only. They never reveal ciphertext,
  plaintext, sealed names, guessed amounts, or source table/row/column identity.

Non-goals are repairing or guessing damaged values, changing the locked-household
meaning, adding a migration/cloud dependency/encryption format/Docker topology,
or supporting mixed old/new API and client artifacts. Acceptance requires the
normal count-0 path to match existing behavior, the synthetic corruption and
privacy matrices to pass, strict 409 and locked 423 boundaries to remain
distinct, and the coordinated contract/client release to pass compatibility and
platform tests.

## Tiered Backup Retention and Recovery Visibility (issue #116)

A system administrator must be able to preserve useful backup history without
making its length depend on backup cadence. Family CFO therefore keeps one
box-global backup stream and configures local and off-box destinations
independently.

Product requirements:

- The default policy keeps every eligible archive for 3 days, the newest archive
  in each UTC day through 14 days, and the newest archive in each ISO week
  through 90 days. Each destination may instead use validated operator-defined
  cumulative horizons or keep every backup.
- Local and off-box policies, logical maximum sizes, and caller-available
  free-space reserves are independent. A safe preflight may delete archives
  already disposable under an activated policy, but always preserves the newest
  eligible archive and every anomalous or unrecognized artifact.
- Active cadence, destination, and policy belong to the box, not to the acting
  administrator's household. Existing household values are bootstrap-only
  compatibility inputs and automatic pruning remains disabled after upgrade or
  restore until a system administrator explicitly confirms the policy.
- Configuration names the target history. Status separately reports the oldest
  readable backup currently visible, bounded probe completeness, capacity, and
  whether coverage is building, met, incomplete, shortened, or unknown.
- A visible/read-probed archive is a recovery candidate, not a guaranteed restore
  point. Status does not prove that the historical key is available, ciphertext
  authenticates, archive contents are complete, migrations can run, or a
  destructive restore succeeds.
- Backup, restore, retention maintenance, and archive deletion share one
  box-global mutation lock. Writes promote atomically, automatic decisions are
  deterministic and journaled, and inventory/capacity/retention failures never
  convert a valid local backup into a failed backup.
- Web and iOS expose the same global configuration, recovery states, warnings,
  optimistic-conflict behavior, accessibility semantics, and translated primary
  copy. Destructive policy edits require an explicit Save and activate action;
  a stale draft is never replayed after a conflict.

Only principals with the box right `backups.manage` may read or mutate this
state. Ordinary household advisor chat is an explicit non-goal: its executor is
household-scoped and has no authenticated system-administrator context, so it
must not receive box-wide backup inventory. A future system-administrator
advisor requires a separate executor and ADR. Other non-goals are per-household
archives, key recovery or archive-format changes, status-time decrypt/restore
tests, cloud backup providers, automatic deletion of anomalies, and a capacity
guarantee after preflight.

Acceptance requires the pure selector's interval/tie/cap/newest/anomaly matrix,
upgrade and restore review gates, strict local and SMB inventory/capacity states,
production lock and reconciliation tests, additive OpenAPI and generated-client
compatibility, localized accessible web/iOS parity, and API/worker-first rollout.

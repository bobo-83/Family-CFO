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

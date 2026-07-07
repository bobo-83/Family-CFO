# Domain Model

## Core Concepts

### Household

A household owns financial context, users, accounts, goals, reports, and settings.

### User

A person who can authenticate and access the household according to a role.

Roles:

- Owner
- Adult
- Viewer
- Child profile

### Account

An account represents a financial container.

Types:

- Checking
- Savings
- Credit card
- Brokerage
- Retirement
- HSA
- 529
- Mortgage
- Auto loan
- Student loan
- Real estate
- Other asset
- Other liability

### Transaction

A dated money movement with amount, currency, account, merchant, category, import source, and review state.

### Bill

A recurring obligation such as mortgage, utilities, insurance, subscriptions, phone, internet, or childcare.

### Income Source

Income includes salary, bonus, RSUs, stock options, side income, and other recurring or expected inflows.

### Goal

A target with a purpose, amount, date, priority, and funding source.

Examples:

- Emergency fund
- Vacation
- Retirement
- College
- Vehicle
- Renovation

### Scenario

A what-if calculation using current context plus user-provided changes.

Examples:

- Buy a laptop
- Take a vacation
- Refinance a mortgage
- Retire at 55
- Accelerate debt payoff

### Recommendation

An explainable answer grounded in financial engine outputs and optionally expanded by the reasoning model.

## Money Rules

- Store amounts as integer minor units plus currency.
- Do not use floating point for persisted money.
- Preserve original imported values.
- Track assumptions used for projections.

## Auditability

Financial engine outputs must include:

- Input references
- Calculation version
- Assumptions
- Warnings
- Output values

## Initial Aggregate Boundaries

- Household
- Financial Account
- Transaction Import
- Goal
- Scenario
- Report
- Conversation
- AI Runtime Configuration

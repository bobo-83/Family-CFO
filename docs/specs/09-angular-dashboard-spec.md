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

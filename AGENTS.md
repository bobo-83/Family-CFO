# Agent Instructions

This repository is optimized for AI coding agents and human maintainers.

## Operating Model

- Follow the Spec Kit order in `docs/specs/README.md`.
- Do not implement product behavior before the relevant spec exists.
- Prefer small, reviewable changes tied to a milestone.
- Treat documentation as code.
- Update tests and docs with implementation changes.

## Architecture Rules

- Financial calculations must be deterministic and auditable.
- LLMs may explain, summarize, and recommend, but must not be the sole calculator.
- Do not add cloud service dependencies for sensitive financial data.
- Keep AI runtimes, vector stores, OCR engines, authentication, and financial modules replaceable.
- OpenAPI is the source of truth for backend clients.

## Expected Change Shape

Each feature should include:

- Implementation
- Unit tests
- Integration tests where component boundaries are touched
- Documentation updates
- A clear commit message

## Sensitive Data

Never commit:

- Bank statements
- Receipts with personal details
- Credentials
- Tax documents
- Brokerage or payroll exports
- Production database dumps

Use synthetic fixtures only.

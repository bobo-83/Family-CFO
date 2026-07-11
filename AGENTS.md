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

## Platform Constraints

- Do not implement, scaffold, generate, or modify Swift/iOS app code from a Linux environment.
- On Linux, iOS work is limited to specifications, documentation, API contracts, CI planning, and review.
- Swift/iOS implementation requires a macOS environment with the Swift toolchain and Xcode available.

## Expected Change Shape

Each feature should include:

- Implementation
- Unit tests
- Integration tests where component boundaries are touched
- **Advisor tool access** — a feature that adds a data domain the family can
  see must also add or extend a read-only grounded tool in the M16 registry
  (`apps/api/src/family_cfo_api/ai_tools.py`, ADR 0009), reusing the same
  service code as the HTTP endpoint, so the chat advisor can answer questions
  about it. If chat access is genuinely out of scope, say so as an explicit
  non-goal in the spec gate.
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

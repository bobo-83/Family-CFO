# ADR 0003: Separate Deterministic Finance from LLM Reasoning

Status: Accepted

## Context

Financial advice requires auditable calculations. LLMs can produce useful explanations, but they are not reliable calculators or sources of truth.

## Decision

Financial calculations are performed by a deterministic financial engine. The LLM explains engine outputs and may ask for missing information, but it must not be the sole source of numeric calculations.

## Consequences

- Calculation outputs must include inputs, assumptions, version, warnings, and traceability.
- Recommendation responses must reference calculation outputs for numeric claims.
- Tests for financial calculations are required before user-facing recommendation behavior.

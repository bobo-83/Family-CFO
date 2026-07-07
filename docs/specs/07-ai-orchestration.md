# AI Orchestration

## Principle

The LLM explains. The financial engine calculates.

## Initial Runtime

Use vLLM first because it provides high-performance local inference and an OpenAI-compatible API.

The backend must not depend directly on vLLM-specific behavior. Use a runtime adapter interface.

Future adapters:

- Ollama
- llama.cpp
- Other OpenAI-compatible runtimes

## Specialized Systems

### Vision

Purpose:

- OCR
- Receipts
- Bills
- Product capture
- Bank statement extraction

Output: structured JSON with confidence.

Vision never provides financial advice.

### Financial Engine

Purpose:

- Budget calculations
- Cash flow
- Net worth
- Retirement
- Debt
- Savings goals
- Forecasts

Output: deterministic results with assumptions and warnings.

### Reasoning LLM

Purpose:

- Recommendations
- Coaching
- Explanations
- Tradeoffs
- Confidence summaries

Input:

- Financial engine output
- User goals
- Conversation history
- Structured vision output
- Relevant retrieved context

## Recommendation Contract

Every AI recommendation must expose:

- Direct answer
- Assumptions
- Deterministic calculation references
- Short-term impacts
- Long-term impacts
- Tradeoffs
- Alternatives
- Confidence
- Missing information

## Guardrails

- The LLM must not invent account balances, debt terms, or investment performance.
- The LLM must cite calculation outputs when making numeric claims.
- Financial advice must be framed as educational guidance unless a future legal review changes this policy.
- The system must not autonomously move money or make trades.

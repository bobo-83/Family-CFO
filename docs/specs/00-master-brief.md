# Master Project Brief

Family CFO is an open source, self-hosted, privacy-first AI financial advisor for families.

It is not a traditional budgeting app. It should understand a family's complete financial life and provide guidance while keeping sensitive information under the user's control.

## Product Philosophy

Family CFO should help answer questions such as:

- Can I afford this?
- Should we eat out tonight?
- Should I buy this laptop?
- Can we take this vacation?
- Can I retire at 55?
- Should I refinance my mortgage?
- Should I lease or buy?
- How will this purchase affect my long-term goals?

The product combines deterministic financial calculations with local AI explanations.

## Core Principles

- Privacy first
- Open source
- Local AI first
- Explainable AI
- Deterministic financial engine
- Replaceable major components
- **Never ask the user for a secret** — no tool or AI agent prompts for a
  password, passphrase, private key, or token. Authentication is delegated to
  the platform's own credential store, so the secret never reaches us
  ([`06-security-model.md`](./06-security-model.md), [`AGENTS.md`](../../AGENTS.md)).
- **One input, not two** — when the user tells the system something, don't make
  them repeat it for a related record the system could infer. Propagate the fact
  (apply to un-decided records, never overwrite explicit choices) and say what
  was done. Reducing user input is a first-class goal ([`AGENTS.md`](../../AGENTS.md)).

## High-Level Architecture

```text
SwiftUI iPhone App
  Chat
  Camera
  Apple Vision
  Face ID
  Notifications

HTTPS

Dockerized Family CFO Home Server
  Angular Desktop Dashboard
  FastAPI Backend
  Financial Engine
  AI Orchestrator
  OCR Pipeline
  PostgreSQL
  Qdrant
  vLLM
  Background Workers

Encrypted Local Storage
```

## Non-Goals

- No cloud-hosted LLM requirement
- No advertising
- No mandatory subscriptions
- No selling user data
- No autonomous financial transactions
- No autonomous investment decisions

## Guiding Principle

Every recommendation must answer:

- Why?
- What assumptions were made?
- What are the tradeoffs?
- How confident is the recommendation?
- How does this affect short-term and long-term financial health?

# ADR 0004: Use a Local AI Runtime Abstraction with vLLM First

Status: Accepted

## Context

Family CFO should run local reasoning models. vLLM is a strong first runtime because it supports high-performance serving and an OpenAI-compatible API.

The project should not become locked to one runtime.

## Decision

Use vLLM as the first supported runtime behind an AI runtime adapter interface.

## Alternatives Considered

- Ollama first: simpler local setup, but less aligned with high-throughput serving.
- llama.cpp first: portable and efficient, but different serving model.

## Consequences

- Runtime configuration is stored separately from recommendation logic.
- Future adapters can support Ollama, llama.cpp, and other OpenAI-compatible runtimes.
- Tests should validate adapter contracts independently of a specific model.

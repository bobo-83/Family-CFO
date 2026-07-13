# Family CFO

Privacy-first open source AI financial advisor for families.

Family CFO is a self-hosted home-server application that combines a deterministic financial engine with a local reasoning model. The financial engine calculates. The LLM explains.

## Vision

Family CFO should behave like a trusted family Chief Financial Officer available 24/7. It should answer practical questions such as:

- Can I afford this?
- Should we eat out tonight?
- Can we take this vacation?
- Can I retire at 55?
- Should I refinance my mortgage?
- How will this purchase affect our long-term goals?

Sensitive financial information remains under the user's control. No bank statements, bills, credentials, investment data, tax documents, spending history, or AI reasoning should require a third-party cloud service.

## Repository Status

This repository is intentionally starting with specifications before implementation.

The first development gate is the Spec Kit:

1. PRD
2. ADRs
3. Domain Model
4. OpenAPI
5. Database Schema
6. Security Model
7. AI Orchestration
8. Mobile Spec
9. Angular Dashboard Spec
10. Docker Spec
11. Milestone Roadmap

Implementation should begin only after these documents are reviewed and accepted.

Project tasks are tracked in [docs/specs/12-implementation-tasks.md](./docs/specs/12-implementation-tasks.md).

## Monorepo Layout

```text
apps/
  ios/                 SwiftUI iPhone app
  web/                 Angular desktop dashboard
  api/                 FastAPI backend

services/
  ai-orchestrator/     LLM runtime abstraction and tool orchestration
  financial-engine/    Deterministic financial calculations
  ocr-worker/          OCR and document processing workers
  scheduler/           Scheduled jobs and reports

docker/                Docker Compose and container assets
database/              Database schema and migrations
shared/                Shared schemas, OpenAPI, generated client sources
docs/                  Product, architecture, security, and workflow specs
```

## Architectural Principles

- Privacy first: no telemetry, no advertising, no mandatory cloud services.
- Local AI first: local reasoning by default through a replaceable runtime.
- Deterministic finance: calculations are auditable and never delegated solely to an LLM.
- Explainable AI: every recommendation must include assumptions, tradeoffs, alternatives, and confidence.
- Replaceable components: AI runtime, vector database, OCR, authentication, and financial modules use clean interfaces.
- API as source of truth: SwiftUI and Angular clients generate from the same OpenAPI contract.

## Planned Runtime Stack

- iPhone app: SwiftUI, Face ID, Vision Framework, Foundation Models where available.
- Desktop dashboard: Angular.
- Backend API: FastAPI.
- Financial engine: deterministic Python service/library.
- AI runtime: vLLM first, OpenAI-compatible runtime abstraction.
- Vector store: Qdrant first, replaceable behind an interface.
- Storage: PostgreSQL.
- Deployment: Docker Compose.

## Deploying

The whole stack — the Angular dashboard (served over HTTPS by nginx), the API,
the background worker, PostgreSQL, and the local vLLM AI runtime — runs with
Docker Compose on a machine you control.

### One-command deploy (local or remote)

```bash
scripts/deploy.sh          # interactive: choose local or remote (SSH), then it does the rest
```

For a remote host it prompts for SSH host/user/port/key, verifies Docker (and the
NVIDIA Container Toolkit), copies the project, generates a `.env` with random
secrets on first run, builds, and starts the stack — then prints the dashboard
URL. See the [Deployment guide](./docs/guides/deployment.md).

### Manual

```bash
cp .env.example .env       # then edit — at minimum set POSTGRES_PASSWORD
docker compose up -d
```

The dashboard is then at `https://localhost:8443` on the host, and at
`https://<host-lan-ip>:8443` from other machines on the network — published
ports bind all interfaces, and `scripts/deploy.sh` prints the LAN URL after it
finishes (self-signed cert by default — accept the browser warning). The API
applies database migrations on startup.
The local vLLM AI runtime is **on by default** and assumes a GPU-capable host;
to run without it, set `FAMILY_CFO_AI_ENABLED=false` and
`docker compose up -d --scale vllm=0`.

### Verify and test

```bash
scripts/doctor.sh          # read-only health report: containers, endpoints, disk, GPU
scripts/e2e-deploy-test.sh # real build + core-stack boot + login + chat smoke test
```

To update a running deployment quickly, patch only the app containers — the AI
model and database are left untouched, so the multi-GB model is never
re-downloaded:

```bash
scripts/patch.sh           # rebuild api + worker + web (or e.g. `scripts/patch.sh web`)
scripts/patch.sh ios       # build + install the iPhone app onto a paired device over WiFi
scripts/patch.sh api ios   # ship a server change and the phone that needs it, box first
```

See [docker/README.md](./docker/README.md) for the development override, volumes,
and secrets, and the [AI Advisor guide](./docs/guides/ai-advisor.md) for testing
the model end-to-end.

## System Requirements

Base stack (PostgreSQL + API + worker + nginx dashboard), **AI disabled**:

| Resource | Minimum | Recommended |
| --- | --- | --- |
| CPU | 2 cores | 4+ cores |
| RAM | 2 GB | 4 GB |
| Disk | 5 GB | 10 GB+ (grows with statements/backups) |
| GPU | none | none |

With the **local AI runtime (vLLM) on** you also need a CUDA GPU and the NVIDIA
Container Toolkit. Requirements are dominated by the model you choose — roughly
~2× the parameter count in GB of VRAM for `bf16`, or ~0.6× for a 4-bit quant,
plus headroom for the KV cache. Model weights are downloaded once into the
`model_cache` volume.

| Model | Tool parser | VRAM (bf16) | VRAM (4-bit) | Disk (weights) | Notes |
| --- | --- | --- | --- | --- | --- |
| Qwen2.5-7B-Instruct | `hermes` | ~16 GB | ~6 GB | ~15 GB | fast; good for smoke tests |
| Qwen2.5-14B-Instruct | `hermes` | ~30 GB | ~10 GB | ~28 GB | balanced |
| **Qwen2.5-32B-Instruct** (default) | `hermes` | ~65 GB | ~20 GB | ~62 GB | strong reasoning + tool use, ungated |
| Qwen2.5-32B-Instruct-AWQ | `hermes` | — | ~22 GB | ~19 GB | 4-bit; ~3–4× faster on bandwidth-limited hardware |
| Qwen2.5-72B-Instruct | `hermes` | ~145 GB | ~40 GB | ~140 GB | best; usually run 4-bit |
| Llama-3.3-70B-Instruct | `llama3_json` | ~140 GB | ~40 GB | ~132 GB | gated (needs `HUGGING_FACE_HUB_TOKEN`) |
| Qwen2.5-VL-7B-Instruct (vision describer) | n/a | ~16 GB | ~6 GB | ~16 GB | describes chat photo attachments (ADR 0011); runs alongside the main model |

VRAM figures are approximate and depend on context length / KV-cache settings;
size storage for **at least 1.5×** the weight size to allow for the download plus
extraction. Set the model with `VLLM_MODEL` and its parser with
`VLLM_TOOL_PARSER` in `.env` (defaults to Qwen2.5-32B-Instruct / `hermes`).

**Performance note (unified-memory systems like DGX Spark / GB10):** decode
speed is memory-bandwidth-bound — every generated token reads all the weights.
Measured on a GB10: 32B bf16 ran at ~3.2 tokens/s; the 4-bit
**Qwen2.5-32B-Instruct-AWQ** measured ~7.9 tokens/s (~2.5× faster; simple
questions ~6s end-to-end) with minimal quality loss — the recommended pick on
such hardware (Runtime page picker, or
`scripts/swap-model.sh Qwen/Qwen2.5-32B-Instruct-AWQ`). Want near-instant?
Qwen2.5-14B-Instruct-AWQ trades some reasoning depth for ~3× more speed.

Chat photo attachments run a second small **vision describer** (`vllm-vision`)
next to the main model; both share the GPU via `VLLM_GPU_FRACTION` (0.60) and
`VLLM_VISION_GPU_FRACTION` (0.20). Disable with `FAMILY_CFO_AI_VISION_ENABLED=false`
and `--scale vllm-vision=0` if the GPU lacks headroom.

## Development Workflow

Every feature starts with:

- Product requirement
- ADR when architecture changes
- API contract
- Tests
- Documentation update
- Commit message

See [AGENTS.md](./AGENTS.md) and [docs/development/ai-agent-workflow.md](./docs/development/ai-agent-workflow.md).

## License

MIT. See [LICENSE](./LICENSE).

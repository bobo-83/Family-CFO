# Spec Kit

Family CFO uses specification-driven development. Implementation begins only after the relevant spec exists and is accepted.

## Required Order

1. [PRD](./01-prd.md)
2. [ADRs](./02-adrs.md)
3. [Domain Model](./03-domain-model.md)
4. [OpenAPI](./04-openapi.md)
5. [Database Schema](./05-database-schema.md)
6. [Security Model](./06-security-model.md)
7. [AI Orchestration](./07-ai-orchestration.md)
8. [Mobile Spec](./08-mobile-spec.md)
9. [Angular Dashboard Spec](./09-angular-dashboard-spec.md)
10. [Docker Spec](./10-docker-spec.md)
11. [Milestone Roadmap](./11-milestone-roadmap.md)

## Task Tracking

Implementation tasks are tracked in [Implementation Tasks](./12-implementation-tasks.md).

## Acceptance State

**Release 0.2.0** — M1–M32 implemented and verified: the full agentic advisor
(local models, vision, live data, memory, attribution), model ops (picker,
one-click apply, hardware planning), bank sync with dedupe, redesigned mobile
dashboard, turnkey deployment, and layered security hardening. Deferrals are
documented in `docs/RELEASE-CHECKLIST.md`. See the [guides](../guides/README.md).

- M0 repository and specification baseline: accepted.
- M1 backend skeleton: accepted for implementation.
- M2 financial context and deterministic engine: implemented (cash flow and budget summary calculations are not yet exposed through an API endpoint; transaction/bill/income write APIs remain out of scope).
- M3 purchase advisor: implemented (deterministic explanation stub only; no real LLM call). Debt payoff/retirement projection is tracked as backlog, not owned by any milestone yet — see `docs/specs/12-implementation-tasks.md`.
- M4 local AI runtime: implemented (vLLM adapter only; no Ollama/llama.cpp adapters, no real vLLM deployment — tests mock the HTTP layer).
- M5 Angular dashboard: implemented (real pages for every M2–M4 API that exists; reports/transactions/imports/backups/users shipped as placeholder shells, now superseded by M11).
- M6 backend/API support: implemented for pairing sessions, paired-device revocation, and bounded deterministic chat, plus the dashboard-side pairing/device-management UI. Swift/iOS implementation remains pending a macOS Swift/Xcode environment per `AGENTS.md`.
- M7 imports and OCR: implemented (CSV import is real; PDF pipeline does real text extraction; the M7-era gaps — PDF line-item parsing, a real OCR engine, OFX/QFX parsing — were closed by M34).
- M8 reports and backups: implemented for weekly/monthly report generation (real wins/risks/unusual-spending heuristics, real goal progress, narrative explanation via the same guardrail-validated LLM/deterministic-stub pattern as the purchase advisor) and encrypted on-demand/scheduled backups (`BackupAdapter` protocol, real `PgDumpBackupAdapter` command/error-handling tested only — no PostgreSQL server in this environment — and a `SqliteFileBackupAdapter` exercising the same encrypt/retention/restore paths against a real file). No annual report (tracked as backlog); no Angular Reports/Backups page upgrade (deferred to M11); no backup-key recovery/rotation.
- M9 household setup, data management, and audit: spec accepted, implementation pending. Closes the M2-deferred write-API gap (account/transaction/bill/income CRUD, household bootstrap, membership management) and the unbuilt `audit_events` table.
- M10 conversation history: spec accepted, implementation pending. Closes the unbuilt `conversations`/`conversation_messages` gap that M4/M6 deferred to "a later milestone" that did not exist.
- M11 dashboard data entry and review UIs: implemented. The M5 Transactions/Imports/Reports/Backups placeholder shells are now real pages, the Accounts and Users pages gained create/edit/delete and member-management, and all write actions are role-gated (UI convenience; the API remains the authority). Vitest covers each page's happy path and role gating; an opt-in Playwright e2e covers login → create account → add transaction → generate report. A dashboard chat UI and a first-run setup wizard remain tracked backlog.

- M12 Docker deployment: implemented. `docker compose up -d` runs the core stack (PostgreSQL, API, worker, nginx-served dashboard); the API entrypoint waits for the DB and runs migrations; secrets come from a gitignored `.env`. vLLM and Qdrant are opt-in Compose profiles (off by default — vLLM needs a GPU, Qdrant has no consumer yet). Verified end-to-end against real PostgreSQL 17, including a backup/restore round trip (the first exercise of M8's `PgDumpBackupAdapter` on real Postgres). Reverse proxy/TLS, monitoring, and a backup sidecar remain Release-Readiness "Future Containers."

- M13 security hardening: implemented. HTTPS/TLS at the web tier (nginx on 443, HTTP→HTTPS redirect, security headers, self-signed cert bootstrap + bring-your-own), session logout and token rotation (`DELETE`/`POST /api/v1/auth/sessions[/refresh]`), configurable session TTL, a consolidated security test suite (authz matrix, redaction, no-telemetry), and CI hardening (gitleaks secret scanning, pip-audit dependency audit, and the service/web suites + client-drift check wired into CI). ADR 0008 resolves the four threat-model open questions (DB encryption, cert provisioning, backup-key recovery, external-AI opt-in). Verified end-to-end over TLS. Reverse proxy/monitoring/rate-limiting remain future Release-Readiness work.

- M14 debt payoff and retirement projections: implemented. Persists per-account debt terms (`accounts.annual_interest_rate`/`minimum_payment_minor`), makes the purchase advisor's `debt` impact real via `calculate_debt_payoff` (replacing the warning-only placeholder), adds `calculate_retirement_projection`, and a `POST /api/v1/advisor/retirement` scenario endpoint returning a grounded recommendation. Closes most of the M3-deferred debt/retirement backlog; an open-ended scenario-planning API remains backlog.

- M15 annual report: implemented. Adds `annual` as a third report type (prior calendar year, 12× monthly normalization) reusing the M8 report content and narrative, plus a scheduled annual generation job. Closes the "Backlog: Annual Report" item; the PRD's weekly/monthly/annual reports are now all delivered.

- M16 agentic tool-calling (conversational advisor): implemented (backed by ADR 0009). Exposes the deterministic engine calculations as callable tools the local model orchestrates to answer open-ended questions, with the guardrail trust boundary moving to tool-argument validation and every figure still tracing to a tool output. Read/compute-only tools; local-model-only; no per-question API.

- M17 turnkey deployment (AI on by default): implemented. Makes the **local** vLLM runtime on by default (via `FAMILY_CFO_AI_*` deployment settings a household inherits until it saves its own config; code default stays off for tests/non-Docker) and removes the `ai` Compose profile so `docker compose up -d` starts the whole stack. Adds `scripts/deploy.sh` for a one-command local/remote (SSH) full-stack deploy. Does not change ADR 0008's external/cloud-AI opt-in stance — only the on-box local model is defaulted on.

- M18 security hardening pass & deployment tooling: implemented (backed by ADR 0010). Closes a manual-review's findings — SSRF allowlist for the AI runtime `base_url`, in-memory brute-force limiter on login, upload size caps (API + nginx), a CSPRNG pairing secret, and production docs gating — and adds `scripts/doctor.sh` (health report) and `scripts/e2e-deploy-test.sh` (real build + core-stack boot + login + chat smoke). All controls have safe defaults; threat model unchanged.

- M19 dashboard AI chat & self sign-up: implemented. Adds an Angular **Ask the Advisor** chat page (send messages, render the grounded recommendation with a confidence chip, conversation history, and an AI-runtime status banner via the new `GET /ai/runtime/status` endpoint showing whether the model is loaded and which one) and a **sign-up/onboarding** page wiring the existing `POST /households` bootstrap. Also fixed the shared contract's missing `POST /households` request body.

- M20 dashboard redesign & mobile support: implemented. A design-token system (CSS custom properties + global element baselines) modernizes every page; the shell gains a mobile slide-in drawer behind a fixed top bar with safe-area/notch handling (`viewport-fit=cover`); tables scroll horizontally and the chat history collapses on narrow screens. Target: iPhone 15 Pro (393px). Web-only; no API changes.

- M21 chat photo attachments (vision routing): implemented (backed by ADR 0011). Chat accepts an attached/camera photo; it is always converted to a text description first (vision-capable main model, else the on-box `vllm-vision` describer, else a graceful warning), the description joins the tool-calling loop and its numbers join the grounded set, and the image itself is never persisted. On-device iPhone describing is recorded as native-app backlog (Safari cannot reach Apple's on-device models).

- M22 model selection, hardware planning & status clarity: implemented (backed by ADR 0012). The AI Runtime page gains a curated model picker (main + vision, vision hidden when the main model sees photos itself) with live replacement-semantics hardware-fit metrics (`GET /ai/models`, `GET /ai/hardware`), a save + `scripts/swap-model.sh` apply command, and a serving-mismatch notice; the chat banner shows separate main/vision states (`vision_enabled`); the camera button alignment is fixed. The API never controls Docker in this milestone.

- M23 Hugging Face model search & one-click apply: implemented (backed by ADR 0013, partially superseding ADR 0012). The AI Runtime page searches HF Hub live (API-proxied, estimated specs labeled as such), and **Apply** downloads/switches the served models via the narrow `model-manager` sidecar (the only Docker-socket holder; single validated swap operation; internal network; removable), with a live status panel polling until the selection is active. The API container remains socket-free; apply is owner-gated.

- M24 live-data chat tools: implemented (backed by ADR 0014). The advisor can fetch live public facts as grounded M16 tools — `get_exchange_rate` (keyless provider, on by default; only two ISO codes leave the box) and `web_search` via a self-hosted SearXNG (`--profile search`, registered only when `FAMILY_CFO_SEARXNG_URL` is set). Failures return structured `lookup_failed` payloads; fetched numbers are grounded via the existing tool-trace mechanism.

- M25 per-response model attribution: implemented. Every chat answer is tagged with what produced it — `answered_by` (the chat model id, persisted as `recommendations.model_version`; null = deterministic path) and `photo_described_by` (the vision model that read an attached photo, also recorded in the persisted assumptions). The chat UI shows a per-bubble caption ("🤖 Answered by X · 📷 photo read by Y" / "🧮 Deterministic calculation").

- M26 chat usability pass: implemented. iOS zoom hardening (`touch-action: manipulation` on interactive elements + explicit 16px chat input), conversation deletion surfaced in the chat UI (confirmation dialog, owner/adult role gate matching the existing M10 `DELETE /conversations/{id}`, clears the open thread), and a legible history list (card items with title + last-updated date).

- M27 institution connections & transaction dedupe: implemented (backed by ADR 0015). Pull statements via SimpleFIN behind a pluggable `BankConnector` seam — the setup token is exchanged once, the resulting access URL is Fernet-encrypted at rest and never exposed, and bank credentials never touch this server. Two-tier dedupe: provider ids give hard idempotency (`(account_id, external_id)` unique); a content hash covers CSV rows (re-uploading a CSV now imports 0 instead of duplicating everything). Manual sync-now + daily scheduled sync; counts (imported vs duplicates skipped) always reported. OFX DirectConnect (no third party at all) remains the preferred next connector on the backlog.

- M28 live price search on by default: implemented (amends ADR 0014). The bundled SearXNG now ships un-profiled with a JSON-enabled config and per-deploy secret, and `FAMILY_CFO_SEARXNG_URL` defaults to it — so price/web lookups work out of the box alongside exchange rates; opt-out documented. Also fixed nginx's 60s proxy timeout cutting off long agentic requests (now 300s). Verified with a real web-searched price question through the live model.

- M29 inference performance: implemented. Diagnosed slow responses (GPU active; memory-bandwidth-bound at 3.2 tok/s for 32B bf16 on unified memory) and moved the recommendation to AWQ 4-bit — measured 7.9 tok/s (~2.5×) live; AWQ options added to the curated catalog.

- M30 conversational memory: implemented. The agentic loop now receives the active conversation's prior turns (bounded window; history numbers grounded), fixing follow-ups that previously lost all context.

- M31 advisor personality: implemented. A tone-setting persona layer (playful default, professional opt-out via `FAMILY_CFO_AI_TONE`) sits above unchanged grounding rules; plus a guardrail fix grounding rounded variants of tool floats (honest rounding of 9.647→"9.6" no longer falls back).

- M32 single-household lockout, full audit coverage & v0.2.0: implemented. `POST /households` refuses once a household exists (single-tenant by default; env opt-out); audit_events now cover login, pairing, device revoke, AI config/model apply, import apply/discard, report generation, and backup create/restore; versions bumped and **v0.2.0** tagged after full cross-package verification.

- M33 asset spendability & accounts organization: implemented. The net-worth tool returns an asset spendability breakdown (liquid/investments/retirement/education/property) with an explicit not-spendable note, and the grounding rules forbid treating net worth as purchasable funds — fixing affordability answers that spent retirement money. `GET /accounts` gains nullable institution/last_synced_at (additive), and the Accounts page groups by category showing the linked institution and last sync.

- M34 real document pipeline: implemented. OFX/QFX imports parse STMTTRN blocks (tolerant SGML/XML regex parser, no new deps) into pending transactions with FITID feeding the M27 external_id hard-dedupe (re-import idempotent); PDF imports additionally run a heuristic statement line-item parser (content-hash deduped pending transactions, unparseable lines counted as skipped); ocr-worker gains a TesseractOcrAdapter selected automatically when the binary is present (baked into the api/worker image; deterministic adapter remains the hermetic test fallback).

- M35 connected account typing: implemented. Bank sync infers the account type from the provider account name at first creation (401k/IRA → retirement, HSA, 529, brokerage, savings, credit/loan/mortgage; SimpleFIN carries no type field, so everything used to land as "checking" — which M33 spendability wrongly counted as liquid). Existing accounts are never retyped by a sync; the Accounts page gains an inline type select (owners/adults) backed by the existing `updateAccount` PATCH so mislabeled accounts can be corrected in one tap.

- M36 emergency fund designation: implemented. Accounts can reserve either a percent of their balance or a fixed amount for emergencies (mutually exclusive, migration `0034`); the reservation is derived at read time and capped at the balance. The net-worth tool reports `emergency_fund_reserved` and the grounding rules subtract it from spendable liquid funds; emergency-fund coverage measures the designated fund when one exists. Accounts page: per-row designation editor, page-level reserved total, and per-category balance rollups.

- M37 Bills page: implemented. The `bills` API and its `ApiService` wrapper have existed since M9, but no page or nav entry was ever built — the M11 page sweep covered every other placeholder shell except this one. Practical impact: with no way to enter recurring expenses, the Overview page's emergency-fund coverage always read "Not enough data" (`emergency_fund_months` needs a monthly-bills denominator). New `/bills` page (list, create, delete; role-gated) closes the gap; no API or contract changes were needed.

- M47 AI runtime page redesign: implemented (user-reported UX fixes; frontend-only). A "Now serving" card and pinned "Your selection" summary always render first, with **stub entries synthesized** for any active/selected model id not in the loaded lists (fixing the bug where an applied off-catalog HF model vanished after reload, taking the fit/apply section with it). The two model grids became one compact, capped, filterable list: quick-filter chips (Recommended-for-this-server = fitting main models strongest-first, Best-for-finances = tool-calling mains, Biggest/Smallest vision, All), role/sort/only-fits facets, per-model fit badges, and role-aware Select. The HF search input is full-width at 16px (stops iOS focus-zoom). The Advanced section now shows the serving main+vision combination and gains a vision-model mini-form that applies through the swap endpoint (vLLM only) with a container-restart warning.

- M45 category management: implemented (prerequisite for budgets). Adds `GET/POST/DELETE /categories` (create/delete owner/adult, audited; a unique `(household_id, name)` index — migration `0037` — gives duplicate-name 409; delete un-categorizes referencing transactions rather than failing). Transaction create/update accept a validated `category_id` (404 if it isn't the household's) and the `Transaction` response exposes `category_id`. UI: a `Categories` page (list/create/delete) and a category `<select>` on the Transactions create form + inline per row. Categories are flat and household-scoped; new households start empty.

- M44 savings-rate metric: implemented. `HouseholdContext` gains additive `savings_rate` = `(monthly_income − average_monthly_spending) / monthly_income`, where income is the recurring monthly figure and average spending is actual outflow (M42 `sum_spending`) over the last 3 complete calendar months ÷ 3 (the current partial month is excluded for stability; the rate can be negative, and is null when income is 0). The Overview cash-flow card shows the percentage (green positive, red negative) with the trailing-3-month average spend.

- M43 configurable emergency-fund target: implemented. `households.emergency_fund_target_months` (migration `0036`, null = default 6) lets each household set its own target; a new role-gated, audited `PATCH /household` sets it (1–60, or reset). The emergency-fund summary computes `target_months_recommended`, the gap, and status against the configured target, with the "getting started" floor clamped to `min(3, target)` so sub-3-month targets still make sense. The Overview emergency-fund card gains an inline target editor for owners/adults.

- M42 spending insights on the Overview: implemented. Two generic base-currency aggregates over `transactions` — `sum_spending` (total outflow; income excluded) and `top_spending_merchants` (grouped, `NULL`→"Other"). `HouseholdContext` gains additive `spending_insights` comparing **month-to-date** against the **same day range last month** (clamped to the prior month's length, so early-month figures aren't misleading), with a `change_percent` (null when last month was zero) and the top 5 merchants. The Overview shows a spending card (change red when up, green when down) with the merchant list. Contract addition only.

- M41 goal progress on the Overview: implemented. `HouseholdContext` gains additive `top_goal` — the highest-priority goal (`list_goals` is priority-ordered) as a `GoalProgress` with `percent_complete` (`min(100, round(current/target*100))`, zero-target guarded). The Overview shows a card with the goal name/type, a progress bar, current-of-target, percent, and optional target date; a "no goals yet" empty state links to the Goals page. Contract addition only.

- M40 net-worth history: implemented. A `net_worth_snapshots` table (migration `0035`, unique `(household_id, as_of)`) stores one snapshot per household per day; `net_worth_history.record_snapshot_once` upserts today's value (idempotent) and runs as a daily worker job plus once at worker startup so the trend begins immediately. `HouseholdContext` gains additive `net_worth_history` (last 30 snapshots, oldest-first); the Overview net-worth card renders an inline SVG sparkline and the change over the shown window.

- M39 upcoming bills: implemented. Fixed a latent drop — bills stored a `next_due_date` and the schema declared it, but the bills `_to_schema` (and `create_bill`'s return) never populated it, so the due date was invisible everywhere. A pure `next_bill_occurrence` helper rolls a stored due date forward to its next occurrence (day-based for weekly/biweekly/semimonthly, calendar-month arithmetic with end-of-month clamping for monthly/quarterly/annual) so stale dates never read as overdue. `HouseholdContext` gains additive `upcoming_bills` (next 14 days, soonest first, with `days_until`); the Overview shows an upcoming-bills card and the Bills page gained a due-date input and shows each bill's next due date. No migration.

- M38 overview dashboard enrichment: implemented. `HouseholdContext` gains an `emergency_fund` summary (months of coverage vs the standard 3/6-month guidance, the fund balance and its provenance, the dollar gap to the recommended target, and a status enum), `monthly_cash_flow` (recurring income − bills), an ordered `asset_breakdown` (M33 spendability categories, map now shared between ai_tools and the API via `finance_service`), and `total_debt` — all additive. The Overview page becomes a card grid with a detailed emergency-fund card (status chip, reserved amount, gap to target, actionable empty states linking to Bills/Accounts), cash flow, assets, and debt. A dashboard-feature-ideas backlog was recorded in the task list.

A post-M8 spec-kit audit surfaced M9–M11 (write APIs, audit log, conversation history, dashboard shell upgrades) as promised-but-unowned work, plus the deferred follow-ups and vector-store/retrieval work now tracked in `docs/specs/12-implementation-tasks.md`. All are documented before implementation, per the spec-driven rule above.

Before coding a milestone, update the relevant documents with:

- Scope
- Non-goals
- API behavior
- Data model changes
- Security impact
- Test expectations
- Documentation impact

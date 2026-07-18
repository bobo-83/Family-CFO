# Angular Dashboard

The desktop web experience for Family CFO. Not the mobile application.

Responsibilities: the full household experience at parity with the iPhone app
([ADR 0025](../../docs/adr/0025-cross-client-feature-parity.md)) — Overview,
Bills, Debts & Loans, Budgets, Goals, Accounts, Transactions, Income & Tax,
Categories, the AI advisor, plus administration (users, devices, backups, AI
runtime, imports, reports, memory).

## Current pages and status

The app ships **19 routed pages** (`src/app/pages/`), grouped in the sidebar
(`src/app/shell/shell.ts`):

- **Money** — Overview, Ask the Advisor, Accounts, Transactions, **Bills**,
  **Debts & Loans** (`/loans`), Income & Tax, **Budgets**, Categories, **Goals**
- **Advisor** — Advisor Memory, AI Runtime
- **Admin** — Imports, Reports, Backups, Users, Devices
- plus **Login** and **Sign-up** (first-run "Create your household").

Highlights not covered by the milestone log below (which is historical and stops
at M20):

- **Bills** is a **payment timeline** — overdue / due-soon / paid groups with
  credit cards and loans as first-class payables ([ADR 0024](../../docs/adr/0024-bills-payment-timeline.md)).
- **Overview** leads with a **Cash outlook** card ([ADR 0026](../../docs/adr/0026-overview-cash-outlook.md))
  and a **"Left to spend this month"** spending plan ([ADR 0027](../../docs/adr/0027-month-spending-plan.md));
  the old safe-to-spend figure is reframed as the **Stress test**.
- **Debts & Loans**, **Budgets**, and **Goals** are full CRUD pages; the loan
  editor scans a statement (file or paste) and takes "payments left" entry.
- Statement/photo inputs accept **paste** (Ctrl/⌘+V) on the loans, income-tax,
  imports, and chat pages ([ADR 0028](../../docs/adr/0028-statement-inputs-accept-paste.md)).
- The sidebar footer shows the running server version (from `/health`,
  [ADR 0029](../../docs/adr/0029-monorepo-version.md)).

## M5 Scope

Implemented, backed by real M2–M4 APIs:

- Login (`POST /api/v1/auth/sessions`) — there is no public sign-up; a household owner creates users. Token storage, a client request interceptor attaching the bearer token, and an auth guard protecting every route except `/login`.
- Overview (`GET /household`), Accounts (`GET /accounts`), Goals (`GET`/`POST /goals`, create form limited to `owner`/`adult` roles), AI Runtime settings (`GET`/`PUT /api/v1/ai/runtime`, editing limited to `owner`).
- Reports, Transactions, Imports, Backups, and Users are explicit placeholder shells — each states which future milestone (M6/M7/M8) will make it real, rather than simulating functionality.

Not implemented in M5: purchase advisor UI, chat, dark mode/i18n, and the production Docker image (that's Release Readiness).

## M6 Scope (Dashboard Integration)

M6 is primarily the iPhone app, which cannot be built from Linux (see `AGENTS.md`'s platform constraint). But M6's own spec says "Dashboard creates a pairing session" — that half is Angular work and is implemented here on the Users & Devices page:

- **Pair a device** (`owner`/`adult`): `POST /api/v1/pairing/sessions` creates a short-lived, single-use session; the returned `qr_payload` is rendered client-side as a scannable QR code (via the `qrcode` package, pure JS, no native deps) plus its raw text and expiration. The payload is non-secret — it contains the API base URL, session id, and household id/name, not a token.
- **Paired devices list and revocation** (`owner` only for revoke): `GET /api/v1/pairing/devices` and `DELETE /api/v1/pairing/devices/{device_id}`.
- There is no QR *scanner* here — the dashboard only displays the code; scanning it is the iPhone app's job (M6, macOS-only, not built in this environment).
- Verified end-to-end against a real backend: session creation renders a real QR image, and a device paired via `POST /api/v1/pairing/confirm` (simulating what the iPhone app will call) appears in the list and can be revoked.

## M11 Scope (Dashboard Data Entry and Review)

Turns the M5 placeholder shells into real pages, now that their backends (M7 imports, M8 reports/backups) and the M9 write APIs exist. All write actions are gated in the UI to the same roles the API enforces — the UI gating is convenience, not the security boundary.

- **Accounts** (`owner`/`adult` to write): create/delete accounts and record balances, alongside the existing list.
- **Transactions** (`owner`/`adult`): manual add/delete plus the list, with money entered in major units and converted to integer minor units before sending.
- **Imports** (`owner`/`adult`): register a CSV/PDF import, upload the file, watch status, and apply/discard a `needs_review` import. Processing happens in the background worker, so status moves from `pending` to `needs_review` out of band.
- **Reports** (`owner`/`adult` to generate): generate weekly/monthly reports and render each report's wins/risks/unusual-spending/recommended-actions and narrative.
- **Backups** (`owner` only): create a backup, list backup jobs, and restore — with a confirmation dialog stating that restore replaces all current data.
- **Users & Devices** (`owner` for members): the M6 pairing/device half now sits alongside real member management (list, add, change role, remove).

Not implemented in M11 (tracked backlog): a dashboard **chat** page (M10 persists conversations at the API layer but ships no UI) and a first-run **household setup wizard** around `POST /api/v1/households` (the API exists after M9; M5 onboarding remains login-only). Both are delivered in M19 below.

## M19 Scope (AI Chat & Self Sign-up)

Closes the two M11 backlog UI gaps.

- **Ask the Advisor** (`/chat`, any role): a conversational page over `POST /api/v1/chat/messages` for open-ended questions ("Can I afford a $1,000 phone?"). Renders the grounded `Recommendation` (answer + expandable impacts/assumptions/tradeoffs/alternatives/warnings) with a **confidence** chip (High/Medium/Low + %), a **conversation history** sidebar (`GET /conversations`, `GET /conversations/{id}`) with "New conversation", and an **AI status banner** driven by the new `GET /api/v1/ai/runtime/status` — showing whether the model is loaded/active and which model is serving. When no model is loaded the API's deterministic snapshot answers (unchanged).
- **Sign-up** (`/signup`, public): a first-run onboarding form over `POST /api/v1/households` (household name, base currency, owner name/email/password) that stores the session like login and enters the app; linked from the login page.

## M20 Scope (Redesign & Mobile)

- **Design tokens**: `src/styles.scss` defines CSS custom properties (palette, radius, shadows, type) plus global element baselines (buttons, inputs, tables, headings) that every page inherits. New styles should consume `var(--color-*)`/`var(--radius-*)` rather than hard-coded values.
- **Responsive shell**: desktop keeps the dark sidebar; below 820px it becomes a slide-in drawer behind a fixed top app bar (hamburger + scrim, closes on navigation). Safe-area insets (`viewport-fit=cover` + `env(safe-area-inset-*)`) handle notched iPhones; target width is iPhone 15 Pro (393px).
- **Responsive pages**: data tables scroll horizontally on narrow screens (global rule); the chat history sidebar collapses to a horizontal chip strip; auth cards fit small widths. Inputs are ≥16px font-size so iOS Safari doesn't zoom on focus.

## Stack

Standalone components (no `NgModule`s), Angular signals and the built-in `resource()` API for async data, zoneless change detection, plain SCSS. No server-side rendering — this is a single-page app served behind the FastAPI backend.

## Setup

```bash
cd apps/web
npm install
npx playwright install chromium
```

This sandbox (and possibly yours) has no system Chrome; Playwright's headless Chromium is required for the e2e tests (`npm run e2e`) and works without `sudo`. `ng test` needs no browser at all — it uses Vitest with jsdom.

## Run

```bash
npm start
```

Serves on `http://localhost:4200` with a dev-server proxy (`proxy.conf.json`) forwarding `/api/*` to `http://localhost:8000`, so the generated client's default `/api/v1` base URL works unchanged against a locally running `apps/api` backend. Start the backend first (see `apps/api/README.md`), including seeding fixtures if you want to log in as the demo user.

## Test and Lint

```bash
npm test
```

Runs the Vitest unit suite (jsdom, no browser). Format with:

```bash
npx prettier --write .
```

## End-to-End Tests

```bash
npm run e2e
```

Runs `e2e/onboarding.spec.ts` and `e2e/data-entry.spec.ts` (M11: login → create account → add transaction → generate report) against a running dev server (`npm start`) and a running, fixture-seeded API backend on `http://localhost:8000` — they are not part of `npm test` and do not start either server themselves. Override the target with `E2E_BASE_URL`.

## Generated API Client

```bash
npm run generate:client
```

Regenerates `src/app/api-client/` from `shared/openapi/family-cfo.v1.yaml` using `@hey-api/openapi-ts` (TypeScript-native — no JVM required, unlike the classic Java-based `openapi-generator-cli`). The generated directory is committed, never hand-edited; components never import it directly — they depend on `src/app/core/api.service.ts`, a thin injectable wrapper, so tests can substitute it via Angular's `TestBed` DI instead of module mocking (Angular's Vitest integration does not support `vi.mock()` on relative imports).

## Build

```bash
npm run build
```

Output goes to `dist/web/` (gitignored).

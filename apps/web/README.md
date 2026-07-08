# Angular Dashboard

The desktop web experience for Family CFO. Not the mobile application.

Responsibilities:

- Reports
- Transaction management
- Statement review
- Imports
- Administration
- Settings
- AI model configuration
- Backup management
- User management

## M5 Scope

Implemented, backed by real M2–M4 APIs:

- Login (`POST /api/v1/auth/sessions`) — there is no public sign-up; a household owner creates users. Token storage, a client request interceptor attaching the bearer token, and an auth guard protecting every route except `/login`.
- Overview (`GET /household`), Accounts (`GET /accounts`), Goals (`GET`/`POST /goals`, create form limited to `owner`/`adult` roles), AI Runtime settings (`GET`/`PUT /api/v1/ai/runtime`, editing limited to `owner`).
- Reports, Transactions, Imports, Backups, and Users are explicit placeholder shells — each states which future milestone (M6/M7/M8) will make it real, rather than simulating functionality.

Not implemented in M5: purchase advisor UI, chat, dark mode/i18n, and the production Docker image (that's Release Readiness).

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

Runs `e2e/onboarding.spec.ts` against a running dev server (`npm start`) and a running, fixture-seeded API backend on `http://localhost:8000` — it is not part of `npm test` and does not start either server itself. Override the target with `E2E_BASE_URL`.

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

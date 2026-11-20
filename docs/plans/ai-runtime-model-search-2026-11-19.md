# AI runtime model search: labeled results view with exact-match first (#168): Plan

> **Status:** Implemented 2026-11-19, with the critique
> (`docs/reviews/ai-runtime-model-search-plan-critique-2026-11-19.md`, findings 1–8)
> applied. Spec section: `docs/specs/09-angular-dashboard-spec.md`, "AI runtime
> \"Find a model\" search results (issue #168)". 23 regression tests appended to
> `ai-runtime.spec.ts` (479 web tests + production build pass); lt/vi locale
> catalogs updated for the new copy.

## Goal

Fix issue #168: on the AI runtime "Find a model" dashboard (`apps/web/src/app/pages/ai-runtime/`), a submitted query must show matching models in a clearly labeled results view — a full `owner/model` match first when it meets the active filters, with the full repo ID visible — with consistent behavior whether cluster mode ("Use both boxes for larger models") is on or off, distinguished loading / no-match / Hugging Face failure states, no stale-response races, and dashboard regression tests covering the exact-ID, unrelated-cluster-recommendation, and no-match cases.

## Reconciliation (4-lane Oracle export)

The `context_builder` export (4 lanes, all completed) is the preservation baseline. All lanes agree on the core design: a distinct labeled search mode separate from browse, draft-vs-submitted query, a request-generation guard, one ordered search list that includes cluster rows (so cluster grouping cannot displace an exact single-box match), matching restricted to id/label substring, and zero backend/contract changes. Material divergences and dispositions:

| # | Divergence | Disposition |
|---|---|---|
| D1 | HF failure with matching local candidates: render them with an explicit notice (lanes 2, 4) vs. show error and no rows (lanes 1, 3) | **Accepted: labeled local fallback.** More useful during an outage; the error alert keeps failure distinct from no-match. Rejected alternative (error + no rows) is simpler but hides models the family can still run. |
| D2 | Quick-filter toolbar during search: hidden (lanes 3, 4) vs. visible relabeled "Browse collections — leaves search" (lane 2) | **Accepted: hidden.** Search's active filters are role/fit/sort; Clear search is the way back. Rejected alternative adds a second competing navigation. |
| D3 | Browse fetch total failure: clear stale browse-live results so the "curated only" fallback is honest (lanes 2, 4) vs. retain previous results (current behavior, lane 1) | **Accepted: clear stale browse-live results.** Current `fetchLive` leaves prior results in place on total failure (`ai-runtime.ts:626–662`), which mislabels them as curated fallback. |
| D4 | `fetchLive` shape: single request per call (lane 4) vs. variadic aggregation (lanes 1, 3) | **Accepted: single request with an explicit destination.** Every current call site issues exactly one request; the backend owns pipeline/deep fan-out (`ai_runtime.py:546–567`). A "partial success" warning is moot and dropped. |
| D5 | Exact-match definition: case-insensitive full-ID equality (lanes 1–3) vs. additionally requiring an `owner/model`-shaped query (lane 4) | **Accepted: case-insensitive equality with the full repo ID.** Only a query containing `owner/model`-shaped text can equal a full ID, so lane 4's guard is implied; label equality never pins. |
| D6 | State representation: tagged union (lanes 1, 2) vs. concrete signals (lanes 3, 4) | **Accepted: concrete signal set** (least churn on existing signals). The tagged union is an equivalent alternative; the observable contract is identical. |
| D7 | Spec location: new standalone spec file (all lanes) | **Corrected:** the Spec Kit is numbered docs (`00-master-brief` plus `01–12`, `docs/specs/README.md:5–17`); dashboard behavior is specified in `docs/specs/09-angular-dashboard-spec.md`, which already carries per-feature sections (e.g. "Debts & Loans page (M116)"). No new spec file. |
| D8 | Full repo ID in collapsed rows: search rows only (lanes 1, 4) vs. shared row markup in both modes (lane 3) | **Accepted: shared row summary shows the full ID in both modes** — one fragment, and the acceptance criterion is satisfied where it is required. |
| D9 | Request generations: one shared generation across search + browse (lanes 2, 4) vs. independent generations (lane 1) | **Accepted: one shared generation.** Every new intent (submit, quick filter incl. `all`, clear, destroy) increments it; a single guard then protects every commit, including lookup-universe writes. |

## Background

### Current frontend behavior (verified)

- `PAGE_SIZE = 6` (`ai-runtime.ts:20`), `LIVE_LIMIT = 20` (`ai-runtime.ts:23`).
- `catalog` is an Angular `resource` calling `api.listAiModels()`; its loader throws with `apiErrorMessage` on error (`ai-runtime.ts:116–128`), but **there is no catalog-error UI** — the template never reads `catalog.error()`. In Angular 22 an errored resource's `value()` throws during render (`_resource-chunk.mjs:233`), so a failed `listAiModels`/`getAiRuntimeConfig` currently makes `knownModels` (`ai-runtime.ts:213–238`) throw and breaks the card — a latent bug this plan must not inherit (see State model).
- `stubModel(id, roleHint)` synthesizes an `AiModelInfo` from an id alone (parameter/memory guesses, vision heuristics, "(not in catalog)" label; `ai-runtime.ts:191–215`).
- `knownModels` (computed, `ai-runtime.ts:212–239`) merges curated catalog + non-duplicate `searchResults` + stubs for configured/served main and vision IDs (`config.model`, `status.served_model`, `status.vision_model`). `byId()` (`ai-runtime.ts:241–247`) resolves from it. **This shared universe is the defect:** a query response enlarges the browse universe instead of defining a query-restricted result set.
- Search signals: `searchQuery` (:614), `searchResults` (:615), `searching` (:616), `searchError` (:617), `quickFilter` (:619, default `'recommended'`), plus `roleFilter`/`onlyFits`/`sortBy`/`visibleCount` (:620–624).
- `fetchLive(...requests)` (`ai-runtime.ts:626–662`): `Promise.all` over the requests, merges + dedupes by id, sets `searchError` ("Hugging Face is unreachable; showing curated models only.") only when **every** request failed, and otherwise replaces `searchResults`. No stale-response guard; on total failure prior results are retained.
- `runSearch()` (`ai-runtime.ts:664–672`): trims, no-ops when empty or `searching()`, sets `quickFilter = 'all'`, resets `visibleCount`, calls `fetchLive({ q, pipeline: 'any' })`. It never restricts displayed rows to the query.
- `setQuickFilter()` (`ai-runtime.ts:674–696`): sets the filter, resets pagination; `recommended` → `{pipeline:'text-generation', deep:true}`; `all-in-one` → `{pipeline:'any', deep:true}`; `finance` → `{q:'finance', pipeline:'text-generation', deep:true}`; `vision-big`/`vision-small` → `{pipeline:'image-text-to-text', deep:true}`; `all` fetches nothing.
- Cluster: `clusterActive` (`ai-runtime.ts:302–311`) = saved toggle AND peer reachable; `needsCluster` (`ai-runtime.ts:313–328`) = `min_nodes >= 2` or headroom-adjusted memory above the single-box budget. `clusterModels` (`ai-runtime.ts:698–701`) filters `knownModels`; `filteredModels` (`ai-runtime.ts:704–790`) excludes cluster-needed models (when inactive, only `min_nodes >= 2`), applies quick-filter predicates (with `LEGACY_FAMILIES` exclusions), role, fit-only, and sorts (generation-aware desc, unknown sizes last).
- Pagination: `visibleModels`/`hiddenCount` (`ai-runtime.ts:793–798`), `showMore()` (:800–802), `pickPhotoModelNext()` (:804–808) routes through `setQuickFilter('vision-big')`.
- Template (`ai-runtime.html`): search input writes `searchQuery` directly with `(input)` and Enter calls `runSearch()`; the Search button is `[disabled]="searching()"` (disabled while searching) (:201–221); `searchError` alert (:223–225); quick-filter row (:227–291); browse loading line (:293–295); facets with sort `default` labeled "Recommended" (:297–325); cluster section rendered as a separate non-paginated list before the regular rows (:327–387); empty state checks only `visibleModels().length` (:389–399); regular expandable list (:401+); "Show N more" (:598–607). Full repo IDs are visible only in the expanded detail.

### Backend seam (verified; unchanged by this plan)

- `GET /api/v1/ai/models` → `list_ai_models` (`apps/api/src/family_cfo_api/api/ai_runtime.py:278–291`) returns the curated `MODEL_CATALOG`.
- `GET /api/v1/ai/models/search` → `search_ai_models` (`ai_runtime.py:521–579`): `q`, `pipeline` (`any`/`text-generation`/`image-text-to-text`; 422 otherwise), `limit` clamped 1–30, `deep`. `any` fans out over both pipelines and `deep` over `_DEEP_HINTS` (user's `q` first, deduped); results dedupe by id and cap at 120.
- `_fetch_hf_page` (`ai_runtime.py:508–516`): `{hf_hub_url}/api/models` with `pipeline_tag`, `sort=downloads`, `limit`, `search=q` when nonempty.
- **HF failures raise HTTP 503** ("Hugging Face is unreachable; showing curated models only", `ai_runtime.py:568–575`) — upstream failure is never serialized as a successful empty response, so the existing `{data, error}` envelope is sufficient to distinguish failure from no-match.
- Response model `AiModelCatalog`; entry schema (`apps/api/src/family_cfo_api/schemas.py:1571–1604`): `id` (HF repo id), `label`, `role` (main/vision/both), `parameters_b`, `est_memory_gb`, `est_disk_gb`, `tool_parser`, `supports_vision`, `gated`, `notes`, `created_at`, `min_nodes`. Author is implicit in the `org/name` id; there is no pipeline field.
- Web client: `ApiService.searchAiModels(options)` wraps the generated `searchAiModels` (`apps/web/src/app/core/api.service.ts:894–901`, import at :136; generated methods `apps/web/src/app/api-client/sdk.gen.ts:1703–1745`). `apiErrorMessage(error, fallback, response?)` (`apps/web/src/app/shared/api-error.ts:19–22`) handles structured errors and 429 `Retry-After`.

### Tests, spec gate, and prior art (verified)

- `apps/web/src/app/pages/ai-runtime/ai-runtime.spec.ts` is the only dashboard spec: Angular TestBed + Vitest, `ApiService` provided as `useValue` with `vi.fn()` mocks and a local `response(data, error?)` helper (representative stub at `ai-runtime.spec.ts:99`; TestBed config :125–130). Existing coverage: hardware fit, apply + status polling, quick filters, recommendation/vision ranking, catalog fallback when HF unreachable, cluster gating/application. **No test covers exact-match ordering.**
- Spec gate: `docs/specs/README.md` requires specs before implementation; the governing doc for dashboard behavior is `docs/specs/09-angular-dashboard-spec.md` (per-feature sections precedent: "## Debts & Loans page (M116)"). Implemented work is recorded as a per-issue acceptance section in `docs/specs/README.md` (pattern: "## Issue #162 …" at `README.md:240`).
- Localization: `npm run extract-i18n` (`apps/web/package.json`) writes `apps/web/src/locale/messages.xlf`; translations live in `messages.lt.xlf` and `messages.vi.xlf`.
- Prior art: no plan/review covers this area (related #167 has none). Relevant history: `21103cff` (m23 HF search + one-click apply), `e02dc189` (m48 live filtered catalog), `23cb2303` (m53 deep search), `64987a35` (#162 cluster toggle).
- Issue #168 acceptance criteria: (1) labeled results view, exact `owner/model` first when it meets active filters, full repo ID visible; (2) query consistent with cluster mode on/off, cluster fit + Apply kept for matching cluster models, no unrelated curated recommendations; (3) loading / no-match / HF failure distinguished, stale responses prevented, clearing restores browsing; (4) regression tests with stubbed HF results for exact ID, unrelated cluster recommendations, and no matches.

## Design

### Scope and non-goals

Dashboard-only (`ai-runtime.ts` / `.html` / `.scss` / `.spec.ts` + spec/docs/locale). Explicit non-goals:

- #167 (apply flow) — unchanged except that Apply must keep working from search rows.
- No backend, OpenAPI, generated-client, database, persistence, URL-state, iOS, or auth changes. No new service or child component.
- No exact-ID lookup endpoint; exact-first ranks candidates the existing endpoint returns (bounded recall) plus locally known entries. Do not fabricate a model from arbitrary query text.
- No auto-retry, timeout policy, or local cache.
- **Advisor tool access is an explicit non-goal** (AGENTS.md gate): this changes an existing operator-facing model picker, not a household data domain.

### State model — `ai-runtime.ts`

Keep `searchQuery` as the editable **draft**. Add:

| State | Shape / default | Notes |
|---|---|---|
| `submittedQuery` | `string \| null` signal, `null` | Trimmed submitted text (case preserved for the heading). `null` = browse mode. |
| `searchResults` | existing signal, now only the current query's accepted response | Cleared on new submission, clear, and exit. |
| `browseLiveResults` | new `AiModelInfo[]` signal, `[]` | Last accepted quick-filter response; search responses never write it. |
| `liveLoadState` | `'idle' \| 'loading' \| 'success' \| 'error'` | Describes the current live operation (browse or search). |
| `searching` | changes to computed | `liveLoadState === 'loading'`. |
| `searchError` | existing nullable signal | Current operation's formatted failure. |
| `liveGeneration` | private number, 0 | Monotonically increasing; captured per request. |
| `destroyed` | private boolean | Set in the existing destruction cleanup alongside `stopPolling()`. |

Derived: `searchActive = submittedQuery !== null`. The state, not the draft, drives search mode: editing a nonempty draft never relabels or replaces the displayed results.

Repurpose `knownModels` as the **lookup universe only** (feeds `byId()` and serving-plan lookups): curated + current query results + browse-live results + configured/served stubs. Introduce `browseModels` (curated + browse-live + stubs) as the source for **all browse display**, including the browse cluster section — so a cleared search cannot leak into browse (`clusterModels` and `filteredModels` switch their source accordingly). Search display builds its own candidate list (below) and never reads `knownModels`. Dedupe everywhere by case-insensitive repo id while preserving each winning record's original casing for display and Apply. `byId()` resolves via the same canonical comparison so a configured ID differing only in case stays resolvable.

**Guarded resource reads:** every computed feeding display or candidates (`browseModels`, `knownModels`, the search pipeline, D1's local fallback) reads throwing resources (`catalog`, `config`) through a guarded accessor — `resource.hasValue()` is safe in the error state, `value()` is not — so a failed catalog or runtime-config load degrades to HF results + stubs instead of breaking the card, and a catalog failure is never mislabeled as an HF failure.

### Interaction contract

| Trigger | Behavior |
|---|---|
| Nonempty draft edit | Update draft only; submitted query, results, and heading unchanged. Route through a new `onSearchInput(value)` handler. |
| Draft becomes empty/whitespace (incl. native search-input clear) | Immediately clear search (below); no request. |
| Submit nonempty draft (Enter or Search button) | Allowed even while a request is pending — newest wins. Capture trimmed query, increment generation, enter loading, reset pagination, collapse expanded row, fetch. |
| Submit the identical normalized query already loading | No-op (dedupe); any other query supersedes immediately. |
| Submit empty draft | Same as clear. |
| Clear search button | Empty draft, invalidate pending work, `submittedQuery = null`, `liveLoadState = 'idle'`, reset pagination + expansion; restore browsing **without fetching**. Return focus to the input. |
| Quick-filter selection (incl. `pickPhotoModelNext()` → `setQuickFilter('vision-big')`) | Exit search, clear draft/query results, invalidate pending work, reset pagination/expansion, then existing browse behavior (fetch unless `'all'`). Accepted `browseLiveResults` are **retained** across switches and while a replacement fetch loads — today's behavior, which the M54 preservation test depends on (`ai-runtime.spec.ts:349–365`); success replaces them, failure clears them (D3). `'all'` keeps accepted results and only invalidates pending work. |
| Role, fit-only, or sort change | Recompute locally, reset pagination, collapse expansion; never invalidates a pending request for the submitted query. |
| Cluster toggle / peer reachability / hardware change | Recompute eligibility and fit in place; keep the submitted query and fetched candidates; reset pagination/expansion (effect on `clusterActive()`). |
| Component destruction | Set `destroyed`, invalidate generation; existing polling cleanup unchanged. |

Search **never rewrites** `quickFilter` (unlike today's `runSearch()`), so clearing restores the browse collection the user had. While search is active, hide the quick-filter toolbar (D2); role/fit/sort facets stay visible and apply to search results; the sort `default` option's label reads "Search order" in search mode (stored value stays `'default'`). Search button is enabled while loading (supersede) and disabled only for a whitespace-only draft or an identical in-flight query.

### Request lifecycle — latest-intent-wins

Replace `fetchLive(...requests)` with `fetchLive(target: 'browse' | 'search', options: LiveSearchOptions): Promise<LiveFetchOutcome>` (D4), where `LiveSearchOptions` is the module-local type of `ApiService.searchAiModels`'s argument minus `limit` (always supplied as `LIVE_LIMIT`) and `LiveFetchOutcome = { ok: true, models } | { ok: false, message }`. Consolidate the repeated "reset `visibleCount` to `PAGE_SIZE` + collapse `expandedId`" pair into one private `resetModelWindow()` used by submissions, clears, quick-filter exits, and facet changes:

1. Increment and capture `liveGeneration` before awaiting anything; set loading and clear the current error.
2. Call `this.api.searchAiModels({ ...options, limit: LIVE_LIMIT })` (unchanged seam) inside `try/catch`.
3. Classify: response `error`, missing data, or malformed/missing `models` array, or a rejected promise = failure; a valid empty array = success. **Search mode owns its failure copy client-side:** the endpoint's only non-auth failure is the 503, whose structured detail ("…showing curated models only", `ai_runtime.py:568–575`) is false in search mode — render the client fallback ("Could not search Hugging Face. Search again to retry.") for it, and keep `apiErrorMessage`'s structured pass-through for unexpected statuses (e.g. 401). The server message remains correct for the browse failure branch.
4. Before **every** completion write (success, failure, and the loading-clear), require: not `destroyed` AND captured generation === current generation. **No unconditional `finally`** — a stale request must not clear a newer request's loading indicator.
5. Commit only to the captured destination: `searchResults` (search) or `browseLiveResults` (browse).

One shared generation covers search and browse (D9): submit, quick filter (including `'all'`), clear, and destruction each invalidate outstanding work even when they start no replacement request. This is logical cancellation — no `AbortSignal`, no service-contract change; superseded requests may finish in transport and are simply discarded.

Browse failure (D3): clear `browseLiveResults`, keep curated + stubs, and show a browse-specific fallback line (replacing today's retained-stale-results behavior). Search failure: keep `searchResults` empty (never an older query's rows) and show the failure state below.

### Search candidates, matching, and ordering

Computed search pipeline for the submitted query:

1. **Build candidates in stable order:** current HF response in server order, then curated catalog entries in catalog order, then matching configured/served IDs (via `stubModel`) not already present. When an id exists in the curated catalog, curated metadata wins at that position (hand-vetted role/size/`min_nodes` govern fit); real metadata wins over stubs; dedupe by case-insensitive id without moving the first occurrence.
2. **Query matching (applied to all sources):** normalize with `trim().toLowerCase()`; a candidate matches when its lowercase full `id` **or** `label` contains the normalized query. Literal contiguous substring — no regex, token expansion, fuzzy matching, and no matching on `notes`. Stubs appear only when their **ID** matches (never via the generated "(not in catalog)" label alone); no model is ever synthesized from the query text.
3. **Deployment eligibility:** cluster inactive → exclude `min_nodes >= 2`; oversized `min_nodes == 1` models remain visible with their honest single-box "no fit" verdict (browse parity). Cluster active → both tiers retained.
4. **Shared facets + sort:** extract the role/fit/sort block from `filteredModels` (`ai-runtime.ts:712–790`) into a private pure helper (e.g. `filterAndSortModels(models, quickFilter)`, always on a copy) reused by browse and search; search calls it with the neutral `'all'` quick filter but the user's role/fit/sort choices. Role `main` = `role === 'main' || 'both'`; `vision` = `supports_vision`; fit-only = `fitOf()` in `{fits, tight}` (cluster-aware via `fitOf`). Explicit sorts reuse the existing comparators (generation-aware desc, unknown sizes last); default sort preserves candidate order (HF order, then curated, then stubs).
5. **Exact-first pin:** stable-partition the filtered/sorted list so the single case-insensitive **full-ID equality** match (D5) is index 0 — only if it survived eligibility and facets (never reinserted past a filter). The pin overrides explicit sorting; sorting governs the remainder. At most one exact row exists after dedupe; label-only equality does not pin.
6. **Paginate the single ordered list** (cluster and single-box rows together) with the existing `PAGE_SIZE`/`showMore()` machinery — an eligible exact ID is visible without "Show more."

Paging reuses the existing `visibleModels`/`hiddenCount` computeds, made mode-aware (select the search or browse array by `searchActive`) — no new pagination names, no "Show more" template churn. Complexity: linear merge/filter plus at most O(n log n) sort; no new network calls per candidate.

### Presentation — `ai-runtime.html` / `.scss`

Browse mode (idle) is unchanged, except: the browse empty-state condition now requires **both** the cluster section and the regular list to be empty (today it checks only regular rows, `ai-runtime.html:389–399`).

Search mode (submitted query active) replaces the browse lists inside the same card:

- Accessible results heading: **Search results for "{submittedQuery}"** (`aria-labelledby`, `.search-results` wrapper; query text via interpolation, never HTML).
- A **Clear search** button beside the input/results heading, usable while loading.
- Quick-filter toolbar hidden (D2); role/sort/fit facets visible.
- Loading: "Searching Hugging Face…" status (`role="status"`, `aria-busy`); no old rows, no no-match message. The shared browse loading line (`ai-runtime.html:293–295`) and `searchError` alert (:223–225) are scoped to `!searchActive()` so neither double-renders nor lands in the wrong section; the search section renders its own status and alert. The Search button label becomes a static "Search" (dropping the `{searching(), select, …}` ICU at :211–221) — progress lives in the results status, since the button now stays enabled for supersede.
- Success with rows: result count for the complete filtered sequence, then one paginated list reusing the existing row markup. Extract the cluster `<li>` (`ai-runtime.html:327–387`) and the expandable regular `<li>` (:401+) into two local `ng-template` fragments rendered via `NgTemplateOutlet` (added to the standalone component's imports); browse keeps its separate sections, search iterates one ordered list choosing the cluster fragment when `servesOnCluster(model)` is true. Cluster search rows keep the combined-memory figures, fit verdict, and existing Apply button (`applyModel(model)` already derives the `cluster: true` payload; `ai-runtime.html:371–384`); regular rows keep expansion, hub detail, serving plans, permissions (`canManageRuntime()`), and disabled states. Show the full repo ID in every collapsed row summary (shared fragments mean browse rows show it too, D8), wrapped for long ids.
- Success, zero displayable rows: "No models match '{query}' with the current filters." — no error alert, no browse recommendations underneath.
- Failure: `role="alert"` with the HF message; when matching **local** candidates (curated/active entries) exist, additionally show "Showing matching catalog and currently configured models only." and render those filtered, ranked local matches (D1); without local matches, no rows and **no** no-match message (remote absence was not established). A filter-excluded exact match gets **no** distinct state: it stays excluded (the issue requires filters to bind) and the generic no-match copy, which already names "the current filters", covers it.

Reuse `.browse__loading`, `.page-error`, `.page-empty`, `.model-row`, `.model-detail` styles; add only compact styles for the heading, always-visible repo id (wrapping), and the cluster annotation. Mark all new copy with the existing `i18n`/`$localize` conventions.

## File-by-file impact

| File | Change | Order |
|---|---|---|
| `docs/specs/09-angular-dashboard-spec.md` | Add a per-feature section (pattern: "## Debts & Loans page (M116)") recording the results-view contract: matching/ranking rules, state machine, failure semantics, cluster behavior, and the non-goals above. **First — the spec gate.** | 1 |
| `docs/specs/README.md` | Add an `## Issue #168` acceptance section on the #162/#164 pattern (`README.md:240`, `:269`) when implementation lands — not a milestone-log line (the README marks that log historical). | 5 |
| `docs/plans/ai-runtime-model-search-2026-11-19.md` | This plan; update its status when implementation lands. | 1 |
| `apps/web/src/app/pages/ai-runtime/ai-runtime.ts` | State model, generation guard, `onSearchInput`/`clearSearch`, single-request `fetchLive(target, options)`, lookup-vs-display split (`browseModels`), shared merge + facet/sort helpers, search pipeline with exact-first pin, mode-aware pagination, destruction cleanup, `NgTemplateOutlet` import. | 2–3 |
| `apps/web/src/app/pages/ai-runtime/ai-runtime.html` | Search-mode section, clear control, distinct status/empty/error branches, row fragments via `NgTemplateOutlet`, full-ID summaries, browse empty-state correction. **Atomic with the component changes.** | 3 |
| `apps/web/src/app/pages/ai-runtime/ai-runtime.scss` | Minimal heading / repo-id / cluster-annotation styles; reuse existing classes; respect the stylesheet budget. | 3 |
| `apps/web/src/app/pages/ai-runtime/ai-runtime.spec.ts` | Regressions below; extend the existing `response()`/mock harness (no new test framework). | 3–4 |
| `apps/web/src/locale/messages.lt.xlf`, `messages.vi.xlf` | Run `npm run extract-i18n`, then fill the new strings' translations. | 5 |

**Unchanged:** `api.service.ts`, `api-error.ts`, generated clients (`sdk.gen.ts`, `types.gen.ts`), `schemas.py`, backend routes, database, iOS targets — no client regeneration (`generate:client` not run).

## Implementation order

1. **Spec gate:** add the `09-angular-dashboard-spec.md` section; update this plan's status links. No product behavior before the spec exists (AGENTS.md / Spec Kit).
2. **Behavior-preserving extraction:** split `browseModels` from `knownModels`, extract the shared merge and facet/sort helpers, correct the browse empty-state condition. Existing dashboard tests must still pass unchanged.
3. **Atomic functional landing:** search state + generation guard + interactions + template/SCSS + the three issue-mandated regressions in one reviewable change.
4. **Adversarial coverage:** races, failures, clear/restore, cluster toggles, pagination, metadata precedence, destruction.
5. **Verification + docs/locale:** full suites, extraction, translations, and the `## Issue #168` acceptance section in `docs/specs/README.md`.

## Verification

All tests use the existing TestBed/`ApiService`/`response()` seam with synthetic fixtures and manually controlled deferred promises — no live HF calls, hardware, credentials, sleeps, or host-capability skips (AGENTS.md test policy). Assert rendered DOM, not only computed arrays; resolve/reject outstanding deferred promises before awaiting fixture stability; stop status polling after Apply assertions (existing test convention).

### Issue-mandated regressions (acceptance criterion 4)

1. **Exact ID ranks first.** Stub HF to return a suffix variant and an unrelated model before the exact mixed-case `owner/model`; submit the full ID padded with whitespace and/or different casing. Assert: labeled results heading uses the trimmed submitted query; the exact row renders first with its full repo ID visible while collapsed; unrelated rows absent; duplicates render once. Repeat with an explicit size sort (pin survives). Verify an exact match excluded by role/fit/cluster eligibility is not reinserted.
2. **Unrelated cluster recommendations stay out.** Stub HF with an unrelated curated cluster recommendation plus matching live models. Test cluster on, off, and peer-unreachable: only query-matching eligible rows render inside `.search-results`; exact-first holds across cluster and single-box rows (both directions); a matching cluster row shows combined-budget fit text and its Apply control posts the existing `cluster: true` payload; toggling off hides `min_nodes >= 2` rows while oversized single-node rows keep their no-fit verdict.
3. **Successful no matches.** Stub HF with an empty array for a query matching no curated or active ID: query-specific no-match message, no result rows, no unrelated browse rows, no failure alert.

### Race and failure matrix

- Search A then B; resolve B then A: only B owns rows, heading, and loading state.
- Resolve or reject A while B pending: B remains loading; A commits nothing.
- Search → clear (button or emptying the input) → late success/failure: browsing stays restored; nothing repopulates.
- Search ↔ quick-filter crossovers in both directions; a pending fetch followed by `All` commits nothing and old errors are cleared (accepted results persist — M54 parity).
- Identical in-flight submission is deduplicated (identity = trim-only comparison of submitted queries; a case-differing resubmit is accepted); a different query supersedes immediately.
- Pending request → component destruction: no later state mutation, no unhandled rejection.
- Structured API error envelope, rejected promise, missing data, and valid empty array produce distinct outcomes (failure alert vs. no-match vs. rows).
- Search failure with matching local entries renders the labeled fallback; without them, no no-match message. The rendered alert text is the client-owned search copy, not the backend's browse-phrased 503 detail. Retry (re-submit) succeeds.
- Catalog/runtime-config resource rejected: the query still works — HF rows render via the guarded reads, and nothing mislabels the catalog failure as an HF failure.
- Browse failure still shows curated fallback (now with stale live results cleared, D3).

### Preservation checks

- Nonempty draft edits do not relabel or replace the submitted query's results until submission.
- Clear restores the previous quick filter and accepted browse results without issuing a request; no query-result leakage into browse (including the browse cluster section).
- Dedupe keeps curated metadata over live duplicates; stubs never replace real metadata.
- Search pagination counts mixed cluster/single rows and resets on submission and facet/cluster changes.
- Applying from a search row does not clear or reset the search state; the resulting `status` reload may add a matching stub mid-search (desired recompute).
- Existing fit, authorization, detail-expansion, Apply, and polling tests continue passing unchanged.

### Commands and manual checks

- `cd apps/web && npm test` (Vitest via `ng test`), `npm run build` (production incl. template/type checks and budgets).
- `npm run extract-i18n` then update `messages.lt.xlf` / `messages.vi.xlf`; no `generate:client` (no contract change).
- Repo formatting checks per existing tooling (prettier).
- Manual: keyboard submit and clear, long-ID wrapping, narrow (393px) layouts, and screen-reader status/alert announcements. Also confirm once against the live box that querying `nvidia/Qwen3.8-Flash-Next-NVFP4` surfaces the exact repo first (the backend queries both pipelines under `pipeline=any`, and the issue confirms HF lists it under `image-text-to-text`).

## Risks and tradeoffs

- **Row-fragment extraction is the main regression risk** (lanes 2–4 agree): preserve the existing markup conditions and test both `NgTemplateOutlet` branches; do not fix unrelated Apply behavior.
- **Browse ranking regressions** while extracting the shared facet/sort helper: retain existing browse assertions (step 2 runs them before any behavior change).
- **Failure vs. no-match conflation** is prevented by the tagged outcome and separate DOM branches; the backend's 503-on-unreachable (`ai_runtime.py:568–575`) keeps the envelope honest. The fan-out is wrapped in one try (`ai_runtime.py:546–567`): a failure in **any** pipeline page yields 503 even if the other succeeded — partial outages are the common case, and D1's labeled local fallback covers them.
- **Local matching is deliberately narrower than HF relevance** (no tag/fuzzy matching) — documented dashboard contract, deterministic and auditable.
- **Logical vs. transport cancellation:** superseded requests may still consume backend work; accepted for correctness without widening `ApiService`.
- **Exact-first is a ranking guarantee over returned candidates:** a genuinely unknown repo that HF's bounded search omits cannot be conjured; the plan adds no second lookup path.

## References

- Issue: https://github.com/bobo-83/Family-CFO/issues/168 (related: #167)
- Upstream example: https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4
- Key files: `apps/web/src/app/pages/ai-runtime/ai-runtime.ts`, `ai-runtime.html`, `ai-runtime.scss`, `ai-runtime.spec.ts`; `apps/web/src/app/core/api.service.ts`; `apps/web/src/app/shared/api-error.ts`; `apps/api/src/family_cfo_api/api/ai_runtime.py`; `apps/api/src/family_cfo_api/schemas.py`
- Spec gate: `docs/specs/README.md`, `docs/specs/09-angular-dashboard-spec.md`

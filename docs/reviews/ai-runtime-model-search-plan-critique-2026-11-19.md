# AI runtime model search plan (#168) — focused critique

**Date:** 2026-11-19
**Subject:** `docs/plans/ai-runtime-model-search-2026-11-19.md` against the four-lane Oracle export `prompt-exports/oracle-plan-2026-11-19-192717-plan-fix-issue-168-2-2e8e.md` (lanes only; the composed prompt and file dump are context).

## Scope and method

Coverage is limited to the five requested categories: (1) export content missing/weakened in the plan, (2) under-specified seams, contradictions, incorrect references, missing dependencies, (3) details the code disproves or a simpler design replaces, (4) requirements absent from both documents, (5) material questions. The Reconciliation table's D1–D9 resolutions are not reopened except where code contradicts one (none do).

Claims were checked against: `apps/web/src/app/pages/ai-runtime/ai-runtime.ts` (909 lines), `ai-runtime.html`, `ai-runtime.spec.ts`, `apps/web/src/app/core/api.service.ts`, `apps/web/src/app/shared/api-error.ts`, `apps/api/src/family_cfo_api/api/ai_runtime.py`, `apps/api/src/family_cfo_api/schemas.py`, `apps/api/src/family_cfo_api/main.py`, `docs/specs/README.md`, `docs/specs/09-angular-dashboard-spec.md`, `apps/web/package.json`, `apps/web/src/locale/`, issue #168, and the Angular 22 resource implementation in `apps/web/node_modules/@angular/core/fesm2022/_resource-chunk.mjs`.

**Verified and held (no findings):** all backend claims — 503-on-failure (`ai_runtime.py:568–575`), `limit` clamp 1–30, `any` fan-out, `deep` over `(q, *_DEEP_HINTS)` with q first and deduped, 120-result cap, `_fetch_hf_page` sends `search=q` only when nonempty (`:508–516`); `list_ai_models` (`:278–291`); `AiModelInfo` fields incl. `min_nodes` (`schemas.py:1571–1604`); `api.service.ts` refs (`:894–901`, import `:136` — the lanes' `:733–744` were stale; the plan is right); `apiErrorMessage(error, fallback, response?)` (`api-error.ts:19–22`); every template line ref in the plan's Background; the test harness (`response()` `:6–14`, TestBed `:125–136`, `stopPolling()` after Apply assertions `:226`, `:243`, `:576`, `:698`, `:737`); locale files exist (`messages.xlf`, `messages.lt.xlf`, `messages.vi.xlf`); package scripts `test`/`extract-i18n`/`build`; `09-angular-dashboard-spec.md` per-feature-section precedent (`:59` — D7's resolution is correct); the plan's quoted acceptance criteria match issue #168 verbatim.

## Findings

### 1. Errored resources throw during render — the plan's Background claim is disproven and the D1 fallback is unimplementable as written (categories 1, 3, 4)

Lane 2 states a guarantee the plan dropped: *"If catalog data is unavailable, successful HF search remains usable. Query fallback can use configured/served stubs independently; a catalog failure must not be mislabeled as an HF failure."* The plan not only drops it — it builds on a false premise.

- The plan's Background says catalog errors *"surface as a catalog-load error (`ai-runtime.ts:116–128`)"*. Disproven: `ai-runtime.html` contains **zero** references to `catalog` (no `catalog.error()`/`catalog.status()` anywhere). There is no catalog-error UI.
- What actually happens: the `catalog` and `config` loaders throw on error (`ai-runtime.ts:116–128`, `:89–101`); in Angular 22 a resource's `value()` **throws** `ResourceValueError` in the error state (`_resource-chunk.mjs:233`, with `isResolved = state.error === undefined` at `:511–513`). `knownModels` reads `this.catalog.value() ?? []` and `this.config.value()?.model` (`ai-runtime.ts:213–238`), so a failed `listAiModels` or `getAiRuntimeConfig` makes `knownModels` → `filteredModels`/`clusterModels` throw during template evaluation — the card breaks instead of degrading. This is a latent bug today; the plan inherits it into `browseModels`, the search-candidate pipeline, and D1's local fallback, all of which read the same resources unguarded.

**Correction:** read `catalog`/`config` through a guarded accessor in the merged-universe and candidate computeds (`resource.hasValue()` is safe in the error state; `value()` is not), so a failed catalog/config degrades to HF results + stubs. Add one regression: stub `listAiModels` (and/or `getAiRuntimeConfig`) to reject, submit a query, assert HF rows render and nothing mislabels it as an HF failure. Fix the Background sentence at the same time. **Justification:** D1's fallback needs readable curated/config data; without the guard, the most useful failure path (exact local match during an outage) is unreachable whenever the catalog load failed, and the new search view can crash the card.

### 2. `browseLiveResults` lifecycle across quick-filter switches is unspecified, and a verification bullet contradicts the behavior the plan preserves (category 2)

Today, live results persist across quick-filter switches: the M54 test (`ai-runtime.spec.ts:349–365`) runs `setQuickFilter('recommended')` (fetching a live entry), then `setQuickFilter('all')` — which fetches nothing — and expects the live entry **still visible** under `all`. The plan's interaction contract says quick-filter selection performs "existing browse behavior (fetch unless `'all'`)" — i.e. `browseLiveResults` is retained until replaced — but its own verification matrix says "pending fetch followed by `All` (no replacement fetch) — **old results/errors cannot return**", which reads as clear-on-switch (a browse behavior change beyond D3's failure case, and one that would break the M54 preservation test).

**Correction:** state explicitly that `browseLiveResults` is retained across quick-filter switches and while a replacement fetch loads (today's behavior; D3 changes only the *failure* branch), and reword the verification bullet to "the **pending** response commits nothing and old errors are cleared." **Justification:** step 2 of the implementation order ("existing dashboard tests must still pass unchanged") is only satisfiable with the retain interpretation; as written, two implementers can read this plan opposite ways and both claim fidelity.

### 3. The search-failure alert will quote the backend's browse-phrased 503 message (category 2)

The 503 detail "Hugging Face is unreachable; **showing curated models only**" (`ai_runtime.py:568–575`) is reshaped by the `HTTPException` handler (`main.py:159–171`) into `ErrorResponse{error:{message}}` (`schemas.py:50–57`), and `apiErrorMessage` prefers the structured message (`api-error.ts:19–22`). The plan formats search failures with `apiErrorMessage(error, fallback, response)` — so the search-failure alert renders a clause that is **false in search mode**: under D1 the view shows matching local candidates only, or nothing ("curated models only" is not what happens, and "only" is wrong even for browse under D3's cleanup). The plan's generic fallback ("Could not search Hugging Face…") only appears when the server message is absent — i.e. never for this endpoint's real failure.

**Correction:** in search mode, own the copy client-side — the endpoint has exactly one non-auth failure shape (503), so use the search fallback for it and keep the structured pass-through for unexpected statuses (401). One sentence in the spec section + a test asserting the rendered alert text. The backend detail message is a declared non-goal, so this must be handled in the dashboard. The same server message remains correct for the browse failure branch (D3), so the scoping is per-mode.

### 4. Contradiction on pagination computeds: new names vs mode-aware reuse (category 2)

The Design says "Expose `filteredSearchModels`, `visibleSearchModels`, `hiddenSearchCount`" and then, in the same sentence, "(mode-aware `visibleModels`/`hiddenCount` selects the search or browse arrays)". These are different designs. The template binds `visibleModels()` and `hiddenCount()` (`ai-runtime.html:402`, `:598–607`); lane 4's mode-aware reuse needs no template churn for "Show N more", while the new names force the template to switch bindings per mode.

**Correction:** pick one (lane 4's mode-aware reuse of `visibleModels`/`hiddenCount` is the smaller diff) and delete the other phrasing.

### 5. Shared card elements need search-mode scoping; the Search button label contradicts supersede (category 4)

Absent from both documents; all inside the "atomic template landing":

- The shared `searchError` alert (`ai-runtime.html:223–225`) and the browse loading line (`:293–295`) key off the shared `searching()`/`searchError()` signals. With D9's single `liveLoadState`, a **search** in flight renders the browse line "Fetching the live list from Hugging Face…" alongside the search status, and a **browse** failure renders the alert above the (hidden) quick-filter row instead of in the search section — double or misplaced rendering unless both elements are scoped to `!searchActive()` (or moved).
- The Search button's ICU label `{searching(), select, true {…} other {Search}}` (`:211–221`) shows "…" exactly when the plan makes the button **enabled** for supersede. Lane 4 resolved this ("keep the button label 'Search'; put progress in the result status") but the plan's presentation section doesn't carry it — the label must stop keying off `searching()`.

**Correction:** add both to the presentation section; they are one-line template facts but are exactly the kind of shared-binding seam the row-fragment extraction is likely to miss.

### 6. The identical-in-flight dedupe has no regression test, and "identical normalized query" is undefined (categories 1 and 2)

The interaction contract keeps "Submit the identical normalized query already loading → No-op" and disables Search for "an identical in-flight query", but the plan's race matrix has no test for it; lane 4 explicitly required *"Identical in-flight submission is deduplicated; a different query is accepted immediately."* It is the likeliest silent regression precisely because the button is now enabled while loading. Separately, "normalized" is undefined for identity: trim-only, or trim + lowercase (the matching normalization)? The two choices differ for `nvidia/Qwen…` vs `NVIDIA/qwen…` while loading.

**Correction:** restore the dedupe regression to the race matrix and define identity in the contract (trim-only is sufficient; case-differing resubmits are cheap and harmless).

### 7. Docs step misreferences the spec-README convention (categories 2 and 3)

The plan's final step adds "a milestone log line in `docs/specs/README.md`", but the README itself marks the per-milestone log as historical — *"starts at M0 and does not run to the current milestone"* (`docs/specs/README.md:19–20`) — and records recent work as per-issue acceptance sections ("## Issue #162 account-only sign-in gate" `:240`, "## Issue #164 session expiry and reauthentication gate" `:269`). **Correction:** add an `## Issue #168` acceptance section on the #162/#164 pattern (or record why not), not a milestone log line. Nit: the plan calls the Spec Kit docs "numbered `00–12`"; the README numbers them 01–12 (`:5–17`). Neither affects D7.

### 8. Dropped lane details worth re-attaching (categories 1 and 4)

- Lane 4's `resetModelWindow()` helper (reset `visibleCount` to `PAGE_SIZE` + collapse `expandedId`) — the reset pair appears in six-plus rows of the interaction contract; one named helper prevents divergent reset paths. Same for lane 4's `LiveSearchOptions` type alias (fetch options minus `limit`, which `fetchLive` always supplies).
- No contract row covers **apply-driven status changes**: applying from a search row reloads `status`, changing `served_model` and therefore the stub set — a matching stub can appear mid-search. That recompute is desirable, but the plan should state that Apply from a search row must not clear/reset the search state (the contract covers cluster/hardware changes only), with one preservation assertion.

### 9. "HF failure" includes partial failure — one clarifying sentence (category 4)

`search_ai_models` wraps the entire fan-out in one try (`ai_runtime.py:546–567`): a failure in **any** page — either pipeline under `any` — yields 503 even if the other pipeline succeeded. D1 already handles this correctly, but the plan's risks section can say so in one line; it also means D1's "more useful during an outage" rationale applies to partial outages, which are the common case.

## Material questions before execution

1. **`browseLiveResults` on quick-filter switch (→ Finding 2):** retain-until-replaced (today's behavior; the M54 test depends on it) or clear-on-switch? Changes step 2's test expectations and the D3 wording.
2. **Pagination naming (→ Finding 4):** mode-aware reuse of `visibleModels`/`hiddenCount`, or new search computeds with template binding switches?
3. **Search-failure copy ownership (→ Finding 3):** client-owned copy for the 503, or accept the server's browse-phrased message in search mode?
4. **Filter-excluded exact match UX:** the contract (correctly, per the issue) never reinserts an exact match that fails role/fit/cluster eligibility, and the no-match copy says "No models match '{query}' with the current filters." For a user who typed a full repo ID and sees "no models match", should the state distinguish "matched but excluded by your filters" (one extra DOM branch + count), or is the generic copy accepted? The issue text supports exclusion; the answer changes one line of copy and one branch — worth deciding in the spec section rather than during review of the diff.

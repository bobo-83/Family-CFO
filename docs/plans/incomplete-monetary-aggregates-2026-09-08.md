# Qualified Incomplete Monetary Aggregates: Implementation Plan

**Issue:** [#119 — Surface incomplete totals rather than refusing the whole endpoint](https://github.com/bobo-83/Family-CFO/issues/119)
**Status:** Documentation gate accepted; backend qualification and orchestration Items 2–3 complete; client Items 4–5 remain pending.
**Expected contract release:** `0.159` from the current `0.158`, or the next unused minor if `VERSION` advances before implementation.

## Goal

Replace the current all-or-nothing response to an unreadable sealed monetary cell with deterministic, auditable aggregate results that preserve readable leaf totals and disclose each omission through a reusable qualified-money contract. A safety- or decision-critical derived figure must be unavailable rather than calculated from incomplete inputs, while unaffected response sections remain usable across the API, web, Apple clients, advisor, and background work.

## Done when

- Every read-side aggregate reachable from sealed transaction or card-statement amount decoding is explicitly qualification-aware or explicitly documented and tested as strict.
- A qualifiable endpoint returns HTTP 200 with `{ value: Money, incomplete_count }` leaf totals; one unreadable source is counted once per affected aggregate, never replaced with zero, and never exposed by identity.
- Any decision whose answer could change because of an omitted amount is `null`/unavailable, and no API, client, advisor tool, persisted calculation, widget, or background narrative reconstructs it from partial components.
- Transaction list/detail, the card-statement list and card-statement mutation/undo pre-reads, export, equality/filter, indexing, and unstable candidate/ranking operations retain documented HTTP 409 `sealed_amount_unreadable` behavior.
- The committed OpenAPI contract, FastAPI schemas, compatibility fixture/version, generated web and Swift clients, all affected UI surfaces, localization/accessibility, and the corruption matrix pass together.

## Scope and non-goals

### In scope

- The complete read-side aggregate graph rooted in the 21 current `_dec_amount` invocation sites (22 textual matches including the function definition), including HTTP, advisor, calculation persistence, study/review, web, iPhone/iPad, Watch, and widgets.
- Per-storage-cell omission identity inside the process; per-field counts on the wire.
- Partial descriptive leaf totals, unavailable dependent decisions, strict refusal boundaries, repair/retry behavior, compatibility/versioning, and focused tests.
- Existing account-balance, bill, goal, budget-limit, compensation-profile, and debt-term values only where they compose with affected transaction or statement inputs.

### Non-goals

- Repairing, deleting, re-encrypting, or guessing unreadable values.
- Making `_dec_amount` globally tolerant or changing the 423 `household_locked` meaning.
- Treating partial values as lower bounds; an omitted refund or inflow can move the exact result either way.
- Adding a database migration, cloud dependency, new encryption format, or Docker/runtime topology.
- Qualifying raw records that have no honest partial representation.
- Supporting mixed `0.158`/`0.159` API and client artifacts. This is one coordinated contract release.
- Adding a new advisor data domain. Existing tools must reuse the same qualified services; no parallel advisor arithmetic is allowed.

## Background and current state

### Failure path

- `_dec_amount` is intentionally strict: a sealed token that decrypts to a non-integer raises `SealedAmountUnreadableError` (`apps/api/src/family_cfo_api/repository.py:35-59`). The exception class and the global FastAPI handler expose HTTP 409 with code `sealed_amount_unreadable` (`apps/api/src/family_cfo_api/household_crypto.py:118-130`, `apps/api/src/family_cfo_api/main.py:122-136`).
- That rule fixed the dangerous prior behavior of silently substituting zero, but aggregate callers currently abort before preserving readable siblings. The four examples in issue #119—overview, spending, cash flow, and safe-to-spend—are only part of the graph.
- Direct repository aggregate roots include category spending, tax, income, total spending, merchant ranking, materialized transaction scans later aggregated by services, recurring-income/categorized-income/outflow scans, and card-statement reads (`apps/api/src/family_cfo_api/repository.py:710-749`, `1930-1989`, `2280-2450`, `6869-6988`).
- The service/API graph includes current and historical household context, spending, budgets and their mutation responses, yearly overview/review, payment timeline, cash outlook, spending plan, and income analysis (`apps/api/src/family_cfo_api/api/household.py:538-1005`, `apps/api/src/family_cfo_api/api/budgets.py:35-197`, `apps/api/src/family_cfo_api/api/bills.py:115-136`, `apps/api/src/family_cfo_api/api/income_analysis.py:273-439`, `apps/api/src/family_cfo_api/yearly_review.py:63-98`). Advisor and background paths include safe-to-spend, savings recommendations, study digests, and yearly narratives (`apps/api/src/family_cfo_api/ai_tools.py:522-546`, `1342-1349`; `apps/api/src/family_cfo_api/ai_study.py:103-120`; `apps/api/src/family_cfo_api/yearly_review.py:180-190`).

### Existing ownership and constraints

- Repository code owns decryption and non-amount SQL eligibility. Services own deterministic composition and must decide whether an output is a safe partial description or an unsafe decision. Routes serialize; clients present but do not invent server financial decisions.
- `Money` is currently a direct `{ amount_minor, currency }` value throughout response schemas (`apps/api/src/family_cfo_api/schemas.py:57-59`, `188-201`, `326-391`, `417-517`, `757-834`, `1295-1302`, `1442-1471`). OpenAPI is authoritative for both clients.
- ADR 0072 requires decrypt-then-compute and deterministic refusal rather than plausible zero (`docs/adr/0072-per-household-encryption-design.md:71-90`, `168-191`, `387-400`). ADR 0075 supplies the closest precedent: expose only a safe subset, preserve null-versus-empty, and disclose exclusions without leaking sealed details (`docs/adr/0075-foreign-currency-balances-excluded-and-disclosed.md:42-94`, `112-150`). Existing strict corruption coverage lives in `apps/api/tests/test_unreadable_amounts.py:28-89`.
- The current OpenAPI parity checker validates component presence and required fields but is not sufficient to catch a property changing from `Money` to `QualifiedMoney`; it must compare nested references, nullability, array items, and enums (`apps/api/src/family_cfo_api/tools/openapi.py:13-24`, `96-119`, `156-188`).
- Web types are generated into `apps/web/src/app/api-client/`; CI regenerates and diffs them (`apps/web/package.json:9-43`, `apps/web/openapi-ts.config.ts:1-9`). Swift types come from the same contract and are also checked for drift (`scripts/generate-swift-client.sh:1-30`, `apps/ios/openapi-generator/openapi-generator-config.yaml:1-5`, `.github/workflows/ios.yml:47-53`).
- Apple overview/Watch/widget snapshots are custom primitive `Codable` models, not serialized generated API DTOs (`apps/ios/FamilyCFO/FamilyCFOWidgetShared/OverviewSnapshot.swift:10-24`, `apps/ios/FamilyCFO/FamilyCFO/Overview/OverviewSnapshot+Context.swift:6-13`). The implementation changes their mapping and may add optional completeness/unavailable primitives, but must not invent a generated-model cache migration.
- Spec Kit order is mandatory: PRD → ADRs → domain → OpenAPI → database/security → advisor → mobile/web → Docker → roadmap/tasks (`docs/specs/README.md:1-20`, `239-255`).

## Settled decisions

1. **Wire shape:** affected monetary aggregates use `QualifiedMoney { value: Money, incomplete_count }`, not a response-level metadata map or scattered companion counts.
2. **Availability:** descriptive leaf totals return the sum of readable contributors with a count; safety- or decision-critical derivations are unavailable if any dependency that could change them is unreadable.
3. **Coverage:** audit all aggregate reads, not just headline endpoints.
4. **Strict decoder:** `_dec_amount` remains unchanged for raw/detail and other unqualifiable operations. Tolerance is an explicit aggregate-only path.
5. **Counting unit:** count distinct unreadable stored amount cells relevant to that field. Source sets are unioned internally; counts are never added.
6. **Rankings:** merchant/category rankings are unavailable as a whole if an eligible unreadable row could change membership or ordering. When available, their existing item amounts remain ordinary `Money`.
7. **Card statement specificity:** current aggregates use `statement_balance_minor`, not `minimum_due_minor`. Aggregate queries decode only the balance; corruption in an unused minimum must not invalidate them. The card-statement list and mutation/undo pre-reads remain strict for both fields.
8. **Singleton timeline amounts:** an unreadable statement-backed item amount is `null`, never a qualified zero presented as the amount. Its item status is `unknown`, while `due_total` carries the qualified partial sum and count.
9. **Client arithmetic:** add server totals where clients currently recombine changed fields—specifically `SpendingByCategory.total` and a `BudgetListResponse.summary`—so web/Watch/iOS do not sum partial values or omission counts locally.

## Design

### 1. Internal qualification model

Add `apps/api/src/family_cfo_api/qualified_amounts.py` with three immutable, request-local types:

- `UnreadableAmountSource`: frozen/hashable identity `(household_id, table, row_id, column)`. Tables initially include `transactions` and `card_statements`; columns include their sealed amount cells. Identity contains no ciphertext, plaintext, merchant, account, or guessed amount.
- `Qualified[T]`: `value: T` plus `incomplete_sources: frozenset[UnreadableAmountSource]`; `incomplete_count` is derived from set length. Helpers may map a value, create a complete value, or union sources, but must not overload arithmetic.
- `QualifiedRows[T]`: readable `rows` plus the source set for omitted rows. Use it only when downstream behavior is a pure additive fold or whole-result invalidation; never create placeholder records with zero amounts.
- `AmountCandidate[TMetadata]`: safe non-amount metadata plus `amount: int | None` and `incomplete_source: UnreadableAmountSource | None`. Use it when downstream code must scope an unreadable candidate by account, date/window, merchant/category key, override, or other metadata. A candidate with `amount=None` is not a zero-valued domain record and never crosses the wire.

The types stay internal. Only counts cross the HTTP/persistence boundary; source identities and candidate metadata exist long enough to attribute, deduplicate, and diagnose the current request. Do not reduce a candidate to a bare source set before payment matching, recurring detection, transfer exclusion, or source attribution has finished.

#### Count and propagation invariants

1. Apply household, date, currency, account, category, and other non-amount predicates before decoding when possible.
2. Count a cell only when it passes those predicates and its unreadable value could affect the particular aggregate.
3. If eligibility depends on the unreadable sign, treat the row conservatively as potentially relevant.
4. A leaf value contains readable contributors only.
5. A composite partial value unions dependency source sets. Never add child counts because the same cell may reach several branches.
6. A decision is computed only when the union of every dependency actually used is empty.
7. The same source may count once in several independent wire fields; each field describes its own omissions.
8. An out-of-window, wrong-currency, wrong-account, or otherwise SQL-proven irrelevant row contributes neither value nor count.
9. Count two unreadable fields on one row only when the output depends on both. A balance-only card aggregate does not count a corrupt minimum.
10. Exact zero with count 0 means no contributors; zero with a positive count means every readable contributor summed to zero and must be labeled partial.

### 2. Decoder and repository boundaries

Keep `_dec_amount(engine, household_id, value) -> int` strict. Add a private aggregate decoder accepting `table`, `row_id`, `column`, and token, returning a readable integer or `None` plus its source identity.

- Catch only `SealedAmountUnreadableError` at this cell boundary.
- Rethrow `HouseholdLockedError`; preserve HTTP 423.
- Propagate database, crypto, cancellation, and unexpected failures.
- Continue caching readable decryptions; never cache unreadable as zero.
- Log only household, table, row, and column identifiers needed for repair. Never log token/plaintext or neighboring sealed names.

Change aggregate interfaces so provenance cannot be discarded accidentally:

| Repository seam | New result/policy |
|---|---|
| `sum_spending`, `sum_income`, `sum_taxes` | `Qualified[int]` |
| `sum_spending_by_category` | replace with one `CategorySpendingTotals` scan |
| `top_spending_merchants` | `Qualified[list[MerchantSpend]]`; service exposes list only when complete |
| aggregate transaction materialization | return `AmountCandidate[TransactionMetadata]` rows so unreadable candidates retain safe matching/grouping metadata |
| single transaction used inside an aggregate | new aggregate candidate reader; raw `get_transaction` stays strict |
| income-detection, categorized-income, household-outflow scans | candidate rows with optional amount and safe attribution metadata; a bare union is insufficient |
| shared bill-detection scan | keep the existing strict reader for suggestions and add a separate aggregate-candidate reader for subscription forecasting |
| card statements used by aggregates | a balance-only candidate reader selecting the authoritative row before decoding `statement_balance_minor`; existing list and mutation/undo pre-reads stay strict |

`CategorySpendingTotals` contains `by_category: dict[id, Qualified[int]]`, `categorized_total`, `uncategorized`, and `overall`. Build them in one scan: assign sources directly to category or uncategorized buckets and never derive uncategorized as `overall - categorized`. Spending, budgets, yearly rollups, and advisor tools reuse this result.

Repository queries must select row IDs before decode. Pure sums may omit unreadable rows and return their source set; matching/detection readers must retain each unreadable row's safe non-amount candidate metadata until attribution is complete. Existing visible placeholder behavior for unreadable text remains unchanged.

For card aggregates, select the newest eligible unpaid/in-horizon statement per account using non-amount metadata first, then decode that chosen balance. If it is unreadable, preserve the chosen statement with `amount=None`; never fall through to an older readable statement or a running-balance fallback.

#### Reads that deliberately remain strict

- Transaction list/detail routes, the card-statement list route, and strict card-statement mutation/undo pre-reads.
- Raw exports and indexing inputs.
- Duplicate/import equality, dedupe, dispute zero checks, amount/range/sign filters, and write-side comparisons.
- Bill suggestions and other cadence/median/candidate ranking whose whole product can change when a row is missing.
- Yearly review generation and any narrative/recommendation boundary requiring complete facts.
- Any endpoint whose only product is an unstable candidate/ranking set with no independent useful section.

Document HTTP 409 on these operations. A background strict reader catches the error at household/job scope, marks the unit retryable/skipped, and proceeds to other households; it must never index or persist an invented zero.

### 3. Public contract

Add the shared schema:

```text
QualifiedMoney
  value: Money                 required
  incomplete_count: integer    required, minimum 0
```

Semantics:

- Count 0 means no selected stored monetary cell was omitted for that field.
- Positive count means `value` is the deterministic sum of readable contributors only.
- The count does not include ADR 0075 foreign-currency exclusions; both disclosures remain independent.
- `value` never contains a synthetic substitute.

Use one server conversion helper from internal qualification to `QualifiedMoney`.

A decision-shaped amount uses `QualifiedMoney | null`: non-null values must be complete (count 0), while `null` means an omitted dependency could change the answer. Keep the qualified component leaves beside it; do not emit a best guess. Non-money decisions use nullable fields or explicit `unavailable`/`unknown` enum cases.

Add `ComputationAvailability { status: "complete" | "unavailable", incomplete_count: integer >= 0 }` for non-additive detection/projection algorithms. `complete` requires count 0; `unavailable` requires a positive count. This is colocated typed status, not a response-level metadata map, and it distinguishes ordinary absence from corruption-driven unavailability without putting `null` inside `QualifiedMoney.value`.

Add `SavingsContributionSet { contributions: list[SavingsContribution], detection: ComputationAvailability }`. When detection is complete, the list retains current declared and detected rows. When an unreadable source could change grouping/cadence/median, retain declared rows only, suppress inferred/detected rows, and disclose the source count through `detection`. This preserves known user-entered facts without presenting an unstable detected collection as empty.

### 4. Field-by-field contract

Legend: **Q** = `QualifiedMoney`; **Q?** = nullable qualified decision (only complete values are non-null); **M** = existing `Money`; **U** = non-money decision becomes unavailable; **Strict** = HTTP 409 remains.

#### Household, emergency fund, cash flow, and spending

| Field | Shape | Rule |
|---|---|---|
| `HouseholdContext.net_worth` | Q | Current account-balance result has count 0; historical transaction reconstruction propagates omissions. |
| `net_worth_history[].net_worth` | M | Existing persisted snapshots are not retroactively qualified by current damage. |
| `asset_breakdown[].total`, `total_debt`, upcoming bill amounts, goals | M | Account/bill/goal inputs are unaffected. |
| `emergency_fund_months` | `number|null` | Null when the expense basis is incomplete. |
| `EmergencyFundSummary.reserved`, `goal_target` | M | Stored balance/designation/goal facts. |
| `monthly_expenses` | Q | Bills/debt terms plus qualified trailing spending. |
| `months` | U | Null when monthly expenses are incomplete. |
| `gap_to_recommended` | Q? | Null when its expense/month basis is incomplete. |
| `status` | enum adds `unavailable` | Do not claim on-track/funded when the verdict can change. |
| `MonthlyCashFlow.income`, `spending` | Q | Qualified month transaction leaves. |
| `MonthlyCashFlow.net` | Q? | Null if either leaf is incomplete. |
| `income_baseline` | M | Compensation profile. |
| `taxes` | `QualifiedMoney|null` | Null still means no tax candidates; categorized unreadable candidates produce partial zero/count. |
| `SpendingInsights.this_month`, `last_month` | Q | Each period owns its count. |
| `change_percent` | `integer|null` | Null if either period is incomplete or exact prior value is zero. |
| `top_merchants` | `list[MerchantSpend]|null` | Null when an unreadable candidate can change ranking; item amounts remain M when complete. |
| `CategorySpend.amount` | Q | Count only sources attributable to that category. |
| `SpendingByCategory.categorized_total`, `uncategorized`, new `total` | Q | Direct one-pass buckets; `total` prevents client recombination. |

#### Savings and budgets

| Field | Shape | Rule |
|---|---|---|
| `SavingsRate.monthly_income`, `average_monthly_spending` | Q | Descriptive transaction totals. |
| `transfers` | `QualifiedMoney|null` | Non-null only when automatic transfer detection is stable; null for ordinary absence or unavailable detection, distinguished by `transfer_detection`. Never label a rerun over readable candidates as partial. |
| new `transfer_detection` | `ComputationAvailability` | Positive count means grouping/cadence/median could change and `transfers` is unavailable; declared contribution facts remain elsewhere. |
| `payroll_deductions` | M | Declared profile. |
| `gross_income`, `residual`, `total_saved` | Q? | Null whenever the take-home/spending/transfer dependencies used are incomplete or transfer detection is unavailable. |
| `percent` | `integer|null` | Null for incomplete dependencies, unavailable transfer detection, or complete nonpositive gross. |
| `savings_contributions` | `SavingsContributionSet` | Preserve declared facts; suppress unstable detection and report its count. |
| `Budget.limit` | M | Stored limit. |
| `Budget.spent` | Q | Category partial total. |
| `remaining` | Q? | Null if `spent` is incomplete. |
| `percent_used`, `status` | nullable | No progress/status claim from partial spent. |
| `BudgetSummary.total_budgeted` | M | Sum of stored limits. |
| `total_spent` | Q | Union sources across envelope categories. |
| `over_count`, `warning_count` | nullable integers | Null if any envelope status is unavailable. |
| `envelope_count` | integer | Stored budget count. |
| `BudgetListResponse.summary` | required `BudgetSummary` | Same server result used by Overview; eliminates web/phone/Watch local aggregation. |

Budget list/create/update responses all return the qualified shape. A successful mutation must not appear to fail with 409 because an unrelated transaction is damaged.

#### Payment timeline

| Field | Shape | Rule |
|---|---|---|
| Stored bill/account-term item `amount` | `Money|null` | Normally M; null only when that item depends on an unreadable aggregate input. |
| Statement-backed item `amount` | `Money|null` | Null for unreadable balance; never substitute zero. |
| `paid_with` | existing object or null | Omit when the linked/matched transaction amount is unreadable. |
| `status` | enum adds `unknown` | Use when amount/payment matching prevents a paid/due verdict. |
| `due_total` | Q | Sum readable due items and union all sources that can change due membership/amount. |
| `liquid_balance` | M | Account balance. |
| `covered` | `boolean|null` | Null if due amount or relevant matching status is incomplete. |

A readable explicit payment link remains authoritative. An unreadable linked payment must not be treated as absent and silently rematched.

#### Cash outlook

| Field | Shape | Rule |
|---|---|---|
| `starting_cash` | M | Account balance. |
| `events[].amount` | M | Emit only readable events. |
| `expected_income` | Q? | Non-null only when recurring-income detection is stable and every used input is complete; do not present a forecast from readable-only candidates as partial. |
| new `income_projection` | `ComputationAvailability` | Reports candidate sources that make cadence/median/membership unstable and distinguishes unavailable from an exact zero forecast. |
| `obligations`, `due_soon` | Q | Sum only readable, definitively placed/due items; union sources for unknown membership or amount. |
| `ending_cash`, `lowest_balance`, `shortfall` | Q? | Null if any projected event dependency or placement is incomplete. |
| dates, runway action, sell units/ticker | nullable/unavailable | Null with the projection. |
| `due_soon_covered` | `boolean|null` | Null if due-soon dependencies are incomplete. |

Emit an outlook event only when its amount, membership, and date/placement are all known. Omit an unreadable or membership-uncertain event and attach the responsible sources to its parent income/obligation qualification or availability status. Do not run the running-balance, shortfall, or runway algorithm over a partial event stream.

#### Spending plan and safe-to-spend

| Field | Shape | Rule |
|---|---|---|
| `income_received`, `spent`, `bills_remaining` | Q | Additive leaves; `bills_remaining` includes only definitively due items and qualifies unknown membership. |
| `income_projected`, `expected_income` | Q? | Null when recurring-income detection is unstable; when available, `expected_income` combines complete projection with complete received income. |
| new `income_projection` | `ComputationAvailability` | Carries the source count for unavailable cadence/median/membership. |
| `account_obligations`, `planned_savings` | M | Stored account/goal inputs. |
| `left_to_spend`, `per_day` | Q? | Null if any added/subtracted dependency is incomplete. |
| `SafeToSpend.liquid_balance`, `emergency_fund_reserved`, `bills_due`, `minimum_debt_payments`, `total_debt` | M | Current sources are unaffected; minimum debt payments are account terms, not statement minimums. |
| `credit_card_payments` | `QualifiedMoney|null` | Additive statement-balance leaf; preserve ordinary absence. |
| `subscription_forecast`, `committed_savings` | `QualifiedMoney|null` | Non-null only when their detection/pairing result is stable; null for absence or unavailability, distinguished by the statuses below. |
| new `subscription_detection`, `savings_detection` | `ComputationAvailability` | Positive count makes the corresponding automatic forecast unavailable. A declared-only computation may remain complete if it does not consult automatic candidates. |
| `committed_total`, `safe_to_spend` | Q? | Null if a required dependency is incomplete or a required automatic forecast is unavailable. |
| drill-down lists | existing readable items | Omit an unreadable statement/transaction-derived item; aggregate wrapper carries the count. |
| `warnings` | existing list | Add one generic partial/unavailable explanation; typed counts remain authoritative. |

If committed savings is not reserved, its incompleteness is informational and does not invalidate safe-to-spend. Never substitute a running card balance for an unreadable exact statement balance.

#### Income analysis and tax

| Field | Shape | Rule |
|---|---|---|
| Readable transaction row amount | M | Return readable evidence rows only. |
| `IncomeSourceAnalysis.total_amount` | Q | Count a relevant unreadable member only while safe metadata still attributes it to this source. |
| `typical_amount` | Q? | Null if omitted candidates could change median. |
| `frequency` | nullable string | Null if cadence is unstable. |
| new response `detection` | `ComputationAvailability` | Captures unattributable/unstable candidate sources and distinguishes incomplete detection from an exact empty result. |
| `IncomeRollup.annual_income`, `monthly_average` | Q | Partial descriptive rollups. |
| `transaction_count` | nullable integer | Null when candidate membership is incomplete. |
| profile and declared expected amounts | M | User-declared authority. |
| `tax` | `TaxEstimate|null` | Profile-based tax remains available; transaction-derived tax is null if detected income is incomplete. |

Return readable source/other-inflow evidence, but do not classify a row whose unreadable sign leaves its role unknown. Coverage warnings additionally explain incomplete stored amounts in presentation; they do not replace typed counts.

#### Yearly overview and review

| Field | Shape | Rule |
|---|---|---|
| monthly `income`, `spending` | Q | Per-month qualified leaves. |
| monthly `net` | Q? | Null if either monthly leaf is incomplete. |
| `net_worth_eom` | `QualifiedMoney|null` | Null for no reconstruction; partial is allowed because it is descriptive. |
| `total_income`, `total_spending` | Q | Union source identities across months. |
| `total_net` | Q? | Null if annual inputs are incomplete. |
| `top_categories` | `list[NamedAmount]|null` | Null if ranking membership/order could change; item amounts remain M. |
| cached `review` | existing object or null | Suppress while the current dependency graph is incomplete. |
| review generation | Strict | POST returns documented 409 and does not overwrite cache. |

The current yearly month income is `max(income_received_between, categorized sum_income)`. Build both as qualified inputs and union both source sets before selecting the readable maximum; an omitted source in the branch that did not win can still change which branch should win. Annual income unions those already-qualified monthly identities rather than adding monthly counts.

### 5. Deterministic service composition

For each additive leaf, query with non-amount predicates, decode with identity, apply existing sign/category rules only to readable values, and return the readable result plus the relevant source set. For matching or detection, preserve safe candidate metadata until attribution is finished. Complexity remains linear in selected rows; memory is proportional to selected candidates plus distinct failures.

A cadence, clustering, median, pairing, transfer-exclusion, or forecast output is non-null only if all candidates that could alter it are readable, or non-amount predicates prove the unreadable candidates irrelevant before the algorithm runs. Re-running the detector on readable rows is not a stability proof. Otherwise return its `ComputationAvailability(status="unavailable", incomplete_count=N)` and keep only independent declared facts.

For each derived result, collect the source sets of the dependencies actually used. If the union is non-empty, do not invoke the existing financial-engine function; return component leaves and an unavailable decision. If empty, invoke the existing deterministic calculation unchanged. This gate applies to monthly net, budget progress/status, spending change, savings rate, emergency-fund coverage/status, payment coverage, outlook/runway, plan allowance, safe-to-spend, transaction-based tax, annual net, and review/narrative inputs.

#### Payment matching

- Narrow potential impact with readable account, bill, date/window, merchant/placeholder, and category metadata before attaching an unreadable source.
- If amount sign/tolerance could determine a match, mark only the affected item `unknown`; keep unrelated items stable.
- An unreadable potential payment makes the affected bill/card remain uncertain. Exclude a status-unknown item from the numeric `due_total`, `bills_remaining`, and outlook event subtotal even when its stored bill amount is readable; union the source that makes membership uncertain into each parent leaf.
- An unreadable statement balance affects only that card and aggregates containing it.
- Current aggregate algorithms never read statement minimum due. Add a regression proving a corrupt minimum does not affect timeline, outlook, or safe-to-spend; the statement list and mutation/undo pre-reads remain strict.
- Select the newest authoritative statement before balance decode. A newest unreadable statement must not fall through to an older readable statement or to the account running balance.

#### Recurring income, subscriptions, and savings detection

- Pass metadata-preserving amount candidates through grouping/override/transfer-exclusion code; expose readable domain rows only after attribution and stability have been decided.
- Do not create placeholder candidates.
- Source totals may be partial where safe metadata attributes a row conservatively; cadence, median, automatic contribution rows, detection-derived monthly equivalents, forecasted events, and ranked recommendations are unavailable if omitted candidates could alter them.
- Preserve user-declared overrides and contributions. Never overwrite or hide an explicit user choice merely because automatic inference is unavailable.

### 6. Persistence, privacy, and lifecycle

For services that already create a calculation reference, introduce an application-owned `CalculationAttempt[T]` envelope rather than pretending the financial engine ran. A complete attempt wraps the existing engine `CalculationResult`; an incomplete attempt carries the response, calculation type, inputs, assumptions, outputs, warnings, and an application schema version such as `qualified-attempt/1`, with `engine_invoked=false`.

Persist exactly one audit row for an incomplete attempt that reaches the existing persistence boundary, and return its calculation reference just as the complete service does. Its inputs include the union-derived `incomplete_amount_count`; outputs contain qualified component leaves and `null` decisions; its warning is generic. Non-calculation aggregate endpoints do not gain audit rows. Keep `CALCULATION_ENGINE_VERSION` unchanged unless engine behavior itself changes—the application attempt version owns the new incomplete shape.

Route complete and incomplete attempts through one `_persist_attempt` boundary so a request cannot write both. Cancellation before the persistence commit may leave no row; cancellation after commit leaves the valid row. A retry creates a new self-contained count and reference—there is no server-side count accumulation.

Never persist or return source row IDs, table/column names, ciphertext, sealed names, or guessed amounts. ADR 0075 foreign-currency warnings remain separate. `financial_calculations` JSON needs no SQL migration.

For yearly reviews:

- Do not generate, overwrite, or feed incomplete facts to deterministic fallback or an LLM.
- Keep an older cached row for recovery, but suppress it from GET while current dependencies are incomplete.
- After repair, normal generation/retrieval resumes.

For iOS/watch/widget caches:

- Map generated qualified fields into existing custom primitive snapshot structs.
- Add optional completeness/unavailable primitives only where a widget must distinguish exact, partial, and unavailable.
- Decode old snapshots with their existing primitive shape; new optional fields default absent. If a future incompatible primitive change fails decode, treat it as cache miss.
- A successful 200 response with a null safety decision must clear the stale cached decision; never coerce it to zero or leave the prior value visible.

Concurrency/lifecycle rules:

- Field-level conservative consistency is sufficient; this change does not move ownership of existing per-query connections or require a response-wide database snapshot. A concurrent repair may affect a later query, but every field must reflect exactly the sources used for that field and never substitute zero.
- Source sets are request-local and immutable, so parallel service branches can union deterministically. Retries recompute from storage and do not accumulate counts.
- Angular resource ownership and an explicit Apple load owner must reject stale results.
- Apple ownership is `(household/session identity, requested month, monotonic generation)`, not month alone. Guard every mutation and side effect after each suspension point; cancel-and-replace old optional tasks where possible.
- Only current-context success triggers notification/widget refresh; optional outlook/plan failure does not discard a valid context.
- Background runtime/session resources retain `finally: close()` behavior on complete, incomplete, cancelled, and failed paths.
- Repair requires no migration: the next read naturally returns count 0 and resumes decisions.

### 7. Advisor and background consumers

Update advisor serialization to include `value`, `incomplete_count`, and a display string derived only from `value`; mark a positive count as partial. Exclude `incomplete_count` from `grounded_money`/number-claim extraction.

Add grounding rules:

- Quote a partial value only with its limitation.
- Never reconstruct a null decision from components.
- Do not advise spending, affordability, coverage, tax liability, budget health, savings cuts, or runway from incomplete inputs.
- Explain that stored data needs repair; do not suggest signing in again, which is reserved for 423/locked state.

Tool behavior:

| Tool/consumer | Required result |
|---|---|
| net worth, month/year descriptive tools | Return qualified totals; no derived net/ranking claim when unavailable. |
| emergency fund | Return components; omit coverage/status decision. |
| safe-to-spend | Return readable components, `safe_to_spend: null`, and structured `error: "incomplete_data"` so old quote instructions cannot fire. |
| spending insights | Return period totals; null change and merchant ranking. |
| income/tax | Reuse the endpoint builder; profile tax survives, detected-income tax may be null. |
| budgets | Return qualified spent and null affected statuses. |
| `find_savings` | Return `incomplete_data` and no ranked cuts if ranking inputs are incomplete. |
| purchase impact | Return `incomplete_data` if affected cash-flow inputs are incomplete. |

`ai_study.build_month_digest` returns a qualified digest. For an incomplete month it must not call the runtime, write memories, or update the digest hash; log household/month/count only. `_next_month_to_study` scans onward for another complete stale month so one damaged old month cannot starve later work. The damaged month remains pending for automatic retry.

Strict indexing/report workers catch the known exception at household/job scope, emit a non-sensitive retryable-skip log/metric, and continue; no durable retry record is added. In vector indexing, distinguish lock from confirmed corruption before any whole-household wipe: a locked household retains every prior vector, while a corrupt transaction read deletes only that household's transaction-kind points so stale `amount_display` payloads cannot be retrieved. Preserve its memory-kind points and every other household, then retry naturally on the next scheduled run. Yearly narrative generation requires an empty dependency source set even for its deterministic fallback.

### 8. Web behavior

Create shared qualified-money presentation helpers rather than page-local arithmetic. They unwrap for currency formatting, expose partial state/count copy, and expose unavailable copy. Use localized singular/plural wording such as “Partial total—1 stored amount could not be read and was left out.” A partial disclosure is a note/status; transport failures remain alerts.

Update every production consumer, not just Overview:

- Overview current/month/year, spending insights, category totals, savings, safe-to-spend, outlook, and plan.
- Budgets list/summary and mutation refreshes.
- Bills payment timeline.
- Income/Tax analysis.
- Shared API fixtures/helpers and locale catalogs.

Presentation rules:

- Render partial leaves, including partial zero, with adjacent visible and accessible disclosure.
- Render unavailable decisions as an em dash/“Unavailable”; remove positive/negative/success styling, progress indicators, checkmarks, and navigation that imply a decision exists.
- Do not compute totals, percentages, remaining money, running outlook rows, or rankings locally from qualified/null values.
- Cash outlook may show starting cash, readable events, and qualified components, but no running-balance/runway view when projection outputs are null.
- Budgets show partial spent but no remaining/percent/status; use the server `BudgetListResponse.summary` rather than the current client reduction.
- Continue rendering unaffected sibling cards. Aggregate incompleteness in a 200 response is not a resource error.
- Preserve household-session/request ownership guards.

Tests cover count 0, partial nonzero, partial zero, pluralization, unavailable decisions, sibling survival, absence of misleading classes/labels, and each page’s generated fixture. Run the repository i18n checker; do not add untranslated literals.

### 9. Apple client behavior

Regenerate shared Swift OpenAPI types on macOS; never hand-edit generated files. Map documented strict-operation 409 responses to an explicit incomplete-data error while retaining 423’s sign-in/locked handling.

Update all generated-type consumers:

- iPhone/iPad Overview current/month/year and its spending, savings, safe-to-spend, cash outlook, and plan details.
- Budgets list/summary and Watch budgets; replace local budget arithmetic with the server summary.
- Bills/payment timeline.
- Income/Tax analysis.
- Watch glance/year detail and Watch/widget/home-widget snapshots.
- Test fixtures and `Localizable.xcstrings`.

Add a shared/testable presentation extension outside generated code for formatting, partial warning text, availability, and VoiceOver. Mirror web rules: show partial leaves; never color, speak, navigate, or display progress for unavailable decisions; do not rebuild running outlook rows from a partial event stream.

Refactor `OverviewViewModel.load()` so authoritative context commits independently of optional current-only outlook/plan requests:

1. Increment a monotonic generation and capture it with household/session identity and requested month as the load owner; cancel retained tasks from the prior generation.
2. Start current-only outlook/plan tasks under that owner.
3. Await context independently and commit only if the full owner still matches. A newer same-month load must beat an older one.
4. Convert optional failures to section-specific state, not a page-level context failure.
5. Recheck ownership after every suspension point and before state mutation, deferred loading cleanup, goal-name resolution commit, notification refresh, snapshot write, and widget reload.
6. On cancellation, month change, or household/session replacement, discard results and suppress side effects even if transport completion arrives late.
7. Refresh notifications/snapshots only after an owner-valid current context load.
8. Clear unavailable safety values in primitive snapshots; never map null to zero or retain a stale exact value.

This work requires Xcode and the iOS/watchOS platforms; do not implement or validate Swift from Linux.

## Error and edge-case matrix

| Case | Required behavior |
|---|---|
| All candidate cells unreadable | Leaf is qualified zero with count N; never show an empty-state conclusion or lower-bound wording. |
| No selected rows | Exact zero with count 0. |
| Unreadable row outside period/currency/scope | No value impact and no count. |
| Unknown sign | Count conservatively if other predicates make it a candidate. |
| Unreadable refund/inflow | Partial value may be above or below exact value; copy says partial, not “at least.” |
| Same cell reaches multiple branches | Union identity; count once in the composed field. |
| Two cells on one row | Count only fields the output uses. |
| Corrupt card minimum only | Aggregate balance consumers remain exact; statement list and mutation/undo pre-reads are 409. |
| Unreadable linked payment | Do not auto-rematch; affected status unknown and due total qualified. |
| Foreign-currency exclusion plus unreadable amount | Preserve distinct typed disclosures; do not combine counts. |
| Locked household | HTTP 423; no partial response. |
| Unexpected crypto/database error | Propagate normally; no qualification catch. |
| Detail after aggregate page loaded | Detail may still return documented 409. |
| Concurrent repair | Current response remains internally honest; later refresh converges to count 0. |
| Repeated/late client response | Ownership/cancellation guard prevents stale commit. |

## File-by-file impact

### Documentation and contract

- `docs/adr/0076-qualified-incomplete-monetary-aggregates.md` (new): record qualified leaves, unavailable decisions, storage-cell identity/deduplication, strict boundary, privacy, versioning, and rejected alternatives.
- `docs/specs/01-prd.md` through `docs/specs/12-implementation-tasks.md`: update in required order. Explicitly record no SQL migration, no security boundary change, existing advisor tool reuse, client parity/accessibility, no Docker change, roadmap milestone, and task ownership.
- `shared/openapi/family-cfo.v1.yaml`: add `QualifiedMoney`, `ComputationAvailability`, `SavingsContributionSet`, field changes, nullable decisions, `unknown`/`unavailable` enums, strict 409 responses, and precise null/zero/empty semantics.
- `VERSION`, every component `BUILD`, and `shared/openapi/compatibility/0.159.yaml`: bump the contract minor, reset builds to 0, and commit the immutable new fixture atomically. If `0.159` is occupied, use the next unused minor everywhere.

### Backend

- `apps/api/src/family_cfo_api/qualified_amounts.py` (new): internal identities, qualified values/rows, metadata-preserving amount candidates, mapping and union helpers.
- `apps/api/src/family_cfo_api/repository.py`: aggregate decoder, IDs in queries, one-pass category result, qualified sum/transaction/income/outflow/statement readers, and an explicit comment/inventory for every remaining strict `_dec_amount` use.
- `apps/api/src/family_cfo_api/schemas.py`: wire types and exact field matrix; shared conversion helper; unaffected `Money` remains unchanged.
- `apps/api/src/family_cfo_api/finance_service.py`: thread provenance through monthly income/spending, savings, emergency expenses, reconstruction, timeline, outlook, plan, subscriptions, and safe-to-spend; gate calculations; add the application-owned complete/incomplete `CalculationAttempt` and one-write persistence boundary with generic count-only warnings.
- `apps/api/src/family_cfo_api/api/household.py`: current/history/update context, spending total, budget summary, yearly/outlook/plan assembly; preserve unaffected siblings and do not broadly catch corruption.
- `apps/api/src/family_cfo_api/api/budgets.py`: shared category result, qualified progress, server summary, consistent list/create/update behavior.
- `apps/api/src/family_cfo_api/api/bills.py`: timeline nullable amount/status/qualified due mapping.
- `apps/api/src/family_cfo_api/api/income_analysis.py`: qualified source/rollup results and tax authority gate reused by endpoint/advisor/mutation responses.
- `apps/api/src/family_cfo_api/yearly_review.py`: provenance-carrying overview result, complete-only rankings/narratives, cached-review suppression without deletion.
- `apps/api/src/family_cfo_api/ai_tools.py`: safe serialization, grounding exclusions, structured incomplete outcomes.
- `apps/api/src/family_cfo_api/ai_study.py`: qualified digest, no incomplete persistence, starvation-free month selection.
- `apps/api/src/family_cfo_api/income_detection.py`, `bill_detection.py`, `savings_detection.py`, `savings.py`, report/month/reconstruction/subscription/index/dedupe callers discovered by the signature inventory: preserve candidate metadata, prove detection stability or return unavailable, or deliberately remain strict. Keep a strict and aggregate-candidate split for the bill-detection reader. No caller may read `.value` while discarding sources.
- `apps/api/src/family_cfo_api/vector_indexing.py`: catch unreadable amounts per household before wipe; preserve every vector when locked, remove only the corrupt household's transaction-kind points when corruption is confirmed, preserve memory/other-household points, continue, and cover worker/API split ownership.
- `apps/api/src/family_cfo_api/main.py`: retain handler meanings; clarify aggregate-local qualification versus strict operations.
- `apps/api/src/family_cfo_api/tools/openapi.py`: compare `$ref`, requiredness, nullability, array items, and enum members recursively for response components.

### Tests

- Preserve and extend `apps/api/tests/test_unreadable_amounts.py` for strict decoder, transaction detail, card-statement list and mutation/undo pre-read, and locked distinctions.
- Add `apps/api/tests/test_incomplete_aggregates.py` for source identity, deduplication, predicate relevance, partial zero, unknown sign, privacy, and independent foreign-currency disclosure.
- Extend existing focused suites for household overview/history, budgets and mutations, spending categories, payment timeline, outlook, plan, income/tax, yearly overview/review, advisor grounding/tools, AI study, strict workers, and OpenAPI parity.
- Strengthen `apps/api/tests/test_openapi_contract.py` so a `$ref`, nullable shape, array item, or enum drift fails even when property names are unchanged.
- Never assert a host tool’s absence. All corruption uses synthetic DB fixtures and explicit seam patching; run everywhere without secrets.

### Web

- Regenerate `apps/web/src/app/api-client/**`; never hand-edit it.
- Add shared qualified presentation logic under `apps/web/src/app/shared/` or the narrowest existing money-formatting module.
- Update `pages/overview/`, `pages/budgets/`, `pages/bills/`, and `pages/income-tax/` TypeScript/templates/specs, plus shared API fixtures.
- Replace budget, spending, and projection client arithmetic with server fields.
- Update all catalogs under `apps/web/src/locale/` and accessibility assertions.

### Apple platforms

- Regenerate `apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated/**` on macOS.
- Update `Overview/HouseholdAPI.swift`, `OverviewViewModel.swift`, `OverviewView.swift`, `SafeToSpendDetailView.swift`, `CashOutlookDetailView.swift`, and year detail/presentation helpers.
- Change `Budgets/BudgetsAPI.swift` to preserve the full `BudgetListResponse` (or a typed budgets+summary result); update `Budgets/BudgetsViewModel.swift` and `BudgetsView.swift` to consume the server summary.
- Update Bills timeline, Income, Watch glance/year views, and all changed-field fixtures.
- Update `FamilyCFOWatch/WatchBudgetsView.swift` to retain the server summary, and map it into `FamilyCFOWatchShared/WatchFaceSnapshot.swift`; widgets/complications must not recompute the aggregate fraction from primitive slices.
- Update `Overview/SpendingCard.swift` to use `SpendingByCategory.total` and its completeness for empty state rather than recombining categorized/uncategorized values.
- Update `FamilyCFOWidgetShared/OverviewSnapshot.swift`, `Overview/OverviewSnapshot+Context.swift`, snapshot stores, `FamilyCFOWatch*`, `FamilyCFOWidget*`, and home-widget consumers using primitive compatibility rather than generated DTO decoding.
- Update `FamilyCFOTests` and `Localizable.xcstrings`, including VoiceOver assertions.

## Orchestration progress

- [x] Item 1 — accept ADR 0076 and update Spec Kit artifacts in order.
- [x] Item 2 — implement backend qualification, API contract/version, and backend verification.
- [x] Item 3 — update advisor/background consumers and worker isolation.
- [x] Item 4 — regenerate and update the web client and all affected web surfaces.
  - Evidence: generated client is stable; 325 web tests, production/all-locale builds, compatibility, i18n, fixture, and scoped diff checks passed. Reverse-completion ownership, unavailable savings copy, partial chart disclosure, and authoritative budget fixtures are covered.
- [x] Item 5 — regenerate and update Apple phone/Watch/widget clients on macOS.
  - Evidence: Swift generation/check, iOS 0.159 compatibility, the full FamilyCFO simulator suite, and signing-disabled app/Watch builds passed. Phone, Watch, widgets, notifications, and primitive caches are session/month/generation owned and clear unavailable decisions without mapping null to zero.

### Item 3 completion evidence (2026-09-08)

- Advisor tools now preserve qualified monetary leaves, exclude omission counts from grounding, and fail closed with structured `incomplete_data` for unsafe safe-to-spend, savings-ranking, retirement-default, and purchase-impact paths.
- Yearly review remains complete-only; scheduled reports build strict facts before runtime/narrative work. AI study scans past incomplete months and writes no runtime-derived memory or digest row for them. Report and vector jobs isolate known unreadable data by household, and vector collection remains before any wipe.
- Verification: the focused Item 3 suite passed (`156 passed`); the expanded corruption and affected-service matrix passed (`212 passed`); `make lint` and `make check-openapi` passed; `make coverage` passed all `1077` API tests with the protected module at `100%`.

### Item 2 correction evidence (2026-09-08)

- `monthly_average` now carries the same base-currency rollup source set as `annual_income`; foreign detection omissions cannot taint either base rollup.
- Safe-to-spend unions subscription amount/detection sources, reports `subscription_detection` unavailable when either is incomplete, and suppresses the automatic `subscription_forecast` until detection is stable.
- `compute_purchase_impact` now qualifies and consumes transaction-derived monthly essential expenses before any decision calculation. The direct route returns the concrete strict `409 sealed_amount_unreadable` response without scenario/calculation/recommendation writes, and authoritative OpenAPI is identical to the frozen `0.159` fixture without a version change.
- Verification: the focused income/safe-to-spend/purchase/OpenAPI service and endpoint matrix passed (`116 passed`); `make lint`, `make check-openapi`, `scripts/check-versions.sh`, and `scripts/check-compatibility-fixtures.sh` passed.

### Backend remediation clarification (2026-09-09)

- The accepted vector-retention rule distinguishes unavailable keys from confirmed corrupt data. Locked households preserve all prior vectors because corruption is unproven; a corrupt transaction read removes only that household's transaction-kind vectors because an old payload can retain an amount storage can no longer decode.
- Memory-kind vectors for the corrupt household and all vectors for other households remain intact. This clarification changes neither contract `0.159` nor generated clients.

### Final review completion evidence (2026-09-09)

- Current-month cash-flow income now unions categorized and detection-or-categorization source sets, including uncategorized unreadable payroll, without double-counting readable paychecks; net remains unavailable whenever that unioned leaf is incomplete.
- Timeline/outlook now retain undated payment uncertainty. Safe-to-spend serializes statement, subscription, and committed-savings details from the same household-date computation that produced their totals and availability, rather than re-querying after calculation.
- Historical advisor net worth distinguishes a real zero snapshot from no snapshot and uses qualified reconstruction for the latter. Strict card-statement, bill-suggestion, report, purchase-impact, and yearly-review operations expose typed 409 responses in contract 0.159 and both generated clients.
- Final verification: the full API suite passed (`1,096 passed`) with the protected crypto module at `100%`; web passed `325` tests plus production/i18n/compatibility gates; Swift generation stability, iOS compatibility, full simulator tests, and dedicated Watch builds passed; version and immutable fixture checks passed.

## Execution index

| Work item | Goal | Done when | Key files | Dependencies | Size |
|---|---|---|---|---|---|
| 1. Accept design/spec gate | Establish product and contract decisions before behavior changes. | ADR 0076 and Spec Kit 01–12 are reviewed in order with scope, non-goals, security, advisor, clients, and tests explicit. | `docs/adr/0076-*`, `docs/specs/01-*`…`12-*` | None | M |
| 2. Add qualification core | Make omission identity impossible to lose accidentally. | Internal types/decoder exist; strict decoder is unchanged; unit tests prove set union and filtering. | `qualified_amounts.py`, `repository.py`, core tests | 1 | M |
| 3. Convert repository roots | Return qualified sums/rows from every aggregate root. | Static `_dec_amount` inventory classifies every call; one-pass categories and balance-only statements pass corruption tests. | `repository.py`, detection modules | 2 | L |
| 4. Gate service decisions | Preserve leaves and suppress unsafe derivations. | Timeline/outlook/plan/savings/emergency/safe-to-spend/year services carry sets and never call calculators on incomplete dependencies. | `finance_service.py`, `yearly_review.py` | 3 | XL |
| 5. Revise contract/version | Publish one authoritative breaking shape. | Schemas/OpenAPI/parity check agree; `0.159` fixture/version/builds and generated-model diffs are intentional. | `schemas.py`, OpenAPI, parity tool/tests, `VERSION`, `BUILD` | 1, 4 | L |
| 6. Update API/persistence | Keep qualifiable endpoints 200 and strict endpoints documented 409. | Endpoint corruption matrix passes; persisted warnings contain counts only; budget mutation responses remain successful. | household/budgets/bills/income APIs, persistence | 4, 5 | L |
| 7. Ground advisor/workers | Prevent partial facts from becoming advice or memories. | Tools expose limitations/structured errors; study and review skip safely; strict jobs continue by household. | `ai_tools.py`, `ai_study.py`, review/index workers | 4–6 | L |
| 8. Update web | Present partial/unavailable states everywhere without local recomputation. | Generated client, four page areas, locales, accessibility, and UI tests pass. | web API client, overview/budgets/bills/income-tax/shared | 5, 6 | L |
| 9. Update Apple clients | Match contract across phone, Watch, widgets, and primitive caches. | Generated client, screens, snapshots, locales, and Xcode tests pass; no null-to-zero/stale decision remains. | shared generated client, app/Watch/widget/tests | 5, 6 | XL |
| 10. Release verification | Prove normal, corruption, privacy, compatibility, and rollback behavior together. | Full matrix and commands below pass at one version; API/web/iOS artifacts are releasable atomically. | all above | 1–9 | L |

## Ordered implementation and verification

1. **Documentation gate.** Write/accept ADR 0076, then update Spec Kit files in order. Include rejected alternatives: global tolerant decoder (would weaken raw-record integrity), response-level metadata map (easy to detach from fields), partial safety decisions (misleading), and dual old/new wire shapes (parallel contract ambiguity).
2. **Qualification core.** Add internal types, decoder, source-set helpers, and narrow unit tests. Preserve 423 and strict 409 behavior.
3. **Repository conversion.** Convert direct sums, one-pass categories, aggregate row readers, income/outflow scans, and balance-only statement reads. Update direct callers atomically; the type system should reject integer assumptions.
4. **Service conversion.** Thread provenance through all deterministic services and add explicit empty-union gates. Test services before public schema changes.
5. **Contract release.** Update Pydantic and OpenAPI, strengthen parity validation, set contract/version/build metadata, and create the immutable fixture.
6. **Endpoint assembly/persistence.** Serialize exact field shapes, isolate unrelated sections, document strict responses, and ensure mutations returning aggregates do not fail after a successful write.
7. **Advisor/background.** Update serialization, grounding, structured incomplete results, study selection, review gating, and strict worker isolation.
8. **Web.** Regenerate, replace local arithmetic, update every production consumer, localization, accessibility, and tests.
9. **Apple platforms on macOS.** Regenerate, update all phone/Watch/widget consumers and primitive snapshot mapping, localize, and run Xcode tests.
10. **Full release gate.** Run normal plus synthetic-corruption matrices, client compatibility, generated diffs, version checks, and privacy assertions before coordinated deployment.

## Verification matrix

### Required corruption cases

- One unreadable transaction: household stays 200; cash-flow/spending/category leaves are partial; net and dependent decisions are null; assets/debt/goals/bills survive.
- Category assignment: source appears in only its category and relevant totals; uncategorized is direct, and client-visible total matches the server total without double counting.
- Budgets: spent is partial, remaining/percent/status are unavailable, server summary is authoritative, and create/update still return success.
- Savings/emergency: descriptive inputs remain; rate/coverage/status are unavailable.
- Outlook/plan: component leaves remain; running projection, runway, left-to-spend, and per-day are unavailable.
- Income: rollups are partial; cadence/median/count are unavailable as applicable; profile-based tax survives while transaction-derived tax is null.
- Year: monthly/annual leaves are partial, net/rankings/review are unavailable; review POST returns 409 without overwriting cache.
- Advisor: partial count is not extracted as money; no tool reconstructs or recommends from a null decision.
- Study/background: incomplete month writes no hash/memory and does not starve a later complete month; strict job skips only the affected household.
- Corrupt statement balance: affected card amount is null/status unknown; due total partial; coverage/outlook/safe-to-spend decisions unavailable; unrelated cards/bills survive.
- Corrupt statement minimum only: current aggregates remain exact; the card-statement list and mutation/undo pre-reads return 409.
- Shared source versus two sources: a shared cell counts once after union; two cells count twice.
- Candidate attribution: an unreadable row retains enough safe metadata to affect only matchable bill/card items and attributable income/source totals.
- Unknown membership: a readable bill amount with an unreadable possible payment is omitted from due/remaining/outlook numeric subtotals while the parent is qualified.
- Detection stability: unreadable cadence/cluster/median/pairing input never yields a readable-only forecast labeled partial.
- Authoritative statement: newest unreadable plus older readable never falls through to the older statement or running balance.
- Calculation audit: an incomplete attempt gets exactly one application-versioned row/reference; cancellation before commit may leave none, after commit preserves it.
- Apple ordering: controlled same-month reverse completion, cancellation, session replacement, optional failure, and post-await side effects cannot commit stale state.
- Adapter/cache ownership: phone, Watch, widgets, and SpendingCard consume server totals and preserve completeness/unavailability.
- Vector indexing: locked failure preserves all existing household vectors; corrupt transaction failure removes only that household's transaction-kind vectors while memory and foreign-household points survive; later households continue and stale amounts are not retrievable.
- Privacy: response, calculation JSON, warnings, reviews, logs under test capture, and advisor payload expose counts but never source IDs/tables/columns/ciphertext/sealed names.
- Normal count-0 path: values and decisions exactly match pre-change behavior.

### Commands

Backend, from `apps/api` using the repository environment:

```bash
.venv/bin/python -m pytest tests/test_unreadable_amounts.py tests/test_incomplete_aggregates.py
.venv/bin/python -m pytest \
  tests/test_household_overview.py \
  tests/test_budgets.py \
  tests/test_payment_timeline.py \
  tests/test_cash_outlook.py \
  tests/test_spending_plan.py \
  tests/test_yearly_overview_api.py
make check-openapi
make lint
make coverage
```

Run the focused corruption matrix in the ordinary no-master-key test configuration and in CI’s synthetic valid-master-key configuration. Tests create/patch their own key seam; neither the implementer nor CI should ask a user for a secret.

Web, from the repository root:

```bash
cd apps/web
npm run generate:client
CI=true npm test
npm run build
cd ../..
scripts/check-client-compatibility.sh web
scripts/check-web-i18n.sh
git diff --exit-code -- apps/web/src/app/api-client
```

Apple platforms, on macOS with the newest supported Xcode and iOS/watchOS platforms:

```bash
scripts/generate-swift-client.sh --check
scripts/check-client-compatibility.sh ios
cd apps/ios/FamilyCFO
sim="$(xcrun simctl list devices available | awk -F '[()]' '/iPhone/ { udid = $2 } END { print udid }')"
xcodebuild test \
  -project FamilyCFO.xcodeproj \
  -scheme FamilyCFO \
  -destination "id=$sim" \
  CODE_SIGNING_ALLOWED=NO
```

Repository/release gates from the root:

```bash
scripts/check-compatibility-fixtures.sh
scripts/check-versions.sh
git diff --exit-code -- \
  apps/web/src/app/api-client \
  apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated
```

Final review gate: enumerate every `_dec_amount` call and every generated-model reference to changed fields. Each must be demonstrably qualification-aware or intentionally strict; no adapter may take `.value` without handling availability/source propagation.

## Risks and mitigations

1. **Provenance discarded at a caller:** use breaking internal result types, no arithmetic overloads, and the static caller inventory.
2. **Double counting:** union storage-cell identities, never counts; test shared multi-branch sources.
3. **Over-invalidation:** apply non-amount predicates and statement-field selection first; test unrelated periods/accounts/minimums.
4. **Under-invalidation:** count unknown-sign candidates conservatively and propagate matching/detection uncertainty.
5. **Misleading clients:** assert absence of “covered,” “on track,” progress colors, percentages, per-day, runway, safe-to-spend, and rankings when unavailable.
6. **Client recomputation:** add server spending/budget totals and prohibit local arithmetic over wrappers.
7. **Advisor count mistaken for money:** exclude metadata from grounding extraction and test exact payload claims.
8. **Sensitive leakage:** allow identities only request-locally and diagnostic identifiers only in controlled server logs; persist generic count-only warnings.
9. **Parity checker false confidence:** strengthen recursive property validation before accepting generated diffs.
10. **Worker starvation:** scan past incomplete study months and scope strict failure to one household/job.
11. **Stale Apple safety data:** explicitly clear primitive cached decisions on a successful incomplete response; do not rely only on decode failure.
12. **Breaking rollout/rollback:** ship API/web/iOS/version fixture together; rollback them together. Schemaless calculation history may contain new wrappers, so old-code mixed deployment is unsupported.

## Compatibility and rollout

This is an intentional breaking contract change. Bump the repository contract minor (`0.158` → expected `0.159`), reset all component builds to 0, and add the matching immutable compatibility fixture as ADR 0074 requires. OpenAPI, FastAPI schemas, API implementation, generated web/Swift clients, fixture, `VERSION`, and every `BUILD` land in one reviewable release change.

Deploy API and clients as one contract release. Existing version gates should require older clients to update rather than attempt to decode the new shapes. Rollback means rolling back API, web, and Apple artifacts together. No SQL rollback is needed; calculation JSON is versioned, cached yearly reviews remain stored, and primitive client caches are either backward-compatible through optional fields or safely rebuilt.

## Open questions

None. The three product choices were settled up front. The critique also resolves that unstable detection outputs are wholly unavailable unless non-amount predicates prove stability; existing calculation-producing endpoints persist one application-versioned incomplete attempt/reference; worker retry is log/metric plus scheduled rerun rather than durable state; response-wide database snapshotting is out of scope; and Apple load ownership combines household/session, month, and generation. If implementation discovers a genuinely new aggregate root, classify it under the same leaf-partial/decision-unavailable/strict rules and amend ADR 0076 and the caller inventory before merging.

## References

- [GitHub issue #119](https://github.com/bobo-83/Family-CFO/issues/119)
- [ADR 0072 — Per-household encryption design](../adr/0072-per-household-encryption-design.md)
- [ADR 0075 — Foreign-currency balances excluded and disclosed](../adr/0075-foreign-currency-balances-excluded-and-disclosed.md)
- [Spec Kit order and feature gate](../specs/README.md)
- `apps/api/tests/test_unreadable_amounts.py`
- `shared/openapi/family-cfo.v1.yaml`

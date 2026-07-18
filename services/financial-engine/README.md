# Financial Engine

The financial engine is deterministic and independent of the LLM.

Responsibilities:

- Cash flow
- Budgets
- Net worth
- Debt payoff
- Retirement projections
- Savings goals
- Scenario planning

The engine returns calculations, assumptions, warnings, and traceable inputs. The LLM explains those results but does not replace them.

## M2 Scope

Implemented as the `family_cfo_financial_engine` package:

- `Money`: an exact value type backed by integer minor units plus an ISO 4217 currency code. Arithmetic (`+`, `-`, unary `-`, scalar `*`) requires matching currencies and raises `CurrencyMismatchError` on mismatch. `Money.scale(numerator, denominator)` normalizes recurring amounts (e.g. weekly to monthly) using `Decimal` rounding (round-half-up) so no float ever touches a persisted amount.
- `CalculationResult`: the shared audit contract (ADR 0003) every calculation returns — `calculation_type`, `version`, `inputs`, `assumptions`, `outputs`, `warnings`, `computed_at`.
- `calculate_net_worth`: sums signed account balances (positive for assets, negative for liabilities) into `net_worth`, `asset_total`, and `liability_total`. Unrecognized account types are still included in the total but flagged with a warning.
- `calculate_cash_flow`: normalizes recurring income and bills to a monthly amount and nets them against caller-supplied discretionary spending.
- `calculate_budget_summary`: nets monthly income and bills against category-level spending and returns a per-category breakdown.
- `calculate_emergency_fund_months`: divides liquid balance by monthly essential expenses; returns `None` with a warning when expenses are zero or negative.
- `calculate_goal_progress`: computes remaining amount, percent complete, and (given a monthly contribution) months to completion for a single goal.

`calculate_retirement_projection` (`RetirementInput`) is implemented and exported.
Beyond the M1–M4 primitives below, the engine also provides
`calculate_safe_to_spend` (`SafeToSpendInputs`, including the subscription-forecast
term) and the tax estimator (`estimate_annual_tax`, `gross_up_from_net`,
`FILING_STATUSES`, `TAX_YEAR`).

## M3 Scope

- `calculate_purchase_impact`: models a one-time cash purchase against `PurchaseImpactInputs` (price, net worth, liquid balance, monthly essential expenses, discretionary cash flow, liability total, and an optional top-priority `GoalInput`). It composes `calculate_emergency_fund_months` and `calculate_goal_progress` rather than duplicating their logic, and returns before/after net worth, before/after emergency fund months, discretionary-cash-flow months consumed, and — only when a top goal is supplied — that goal's opportunity-cost percentage.
- `accounts` now persists `annual_interest_rate` and `minimum_payment_minor` (migration `0029`), so debt terms are available; the debt-payoff outlook is computed from them (`finance_service.compute_debt_outlook`). A liability with no recorded terms is surfaced as "unmodeled" rather than guessed.
- A purchase price greater than the supplied liquid balance produces a warning instead of a hard failure; the caller decides how to surface that.
- `calculate_debt_payoff`: simulates monthly amortization for a single debt given `DebtInput` (balance, annual interest rate, minimum payment, optional extra payment), returning months to payoff and total interest paid. It has no database dependency — inputs are supplied directly by the caller, so it is fully unit tested with synthetic `DebtInput` values (accounts now persist rate and payment via migration `0029`). When the payment doesn't cover accruing interest, or payoff would take more than 100 years, it returns `None` for both outputs with a warning instead of an incorrect number.
- `calculate_future_value`: grows a lump sum (`FutureValueInput`: present value, annual return rate, whole years) at a constant rate compounded annually, returning `future_value` and `growth`. Used for opportunity-cost questions — what an amount could become if invested rather than spent. Added for the M16 agentic advisor.

## Assumptions and Limitations

- All inputs to a calculation must share one currency; the engine does not perform currency conversion.
- Recurring frequency normalization assumes a fixed 12-month year (`weekly` = 52/12, `biweekly` = 26/12, `semimonthly` = 24/12, `quarterly` = 1/3, `annual` = 1/12).
- `calculate_net_worth` assumes account balances are already signed correctly by the caller (asset accounts positive, liability accounts negative); the engine does not infer sign from account type.
- `calculate_purchase_impact` assumes the purchase is paid in cash from liquid balances in a single lump sum; it does not model financing, recurring costs, or multi-item purchases.
- `calculate_debt_payoff` assumes interest compounds monthly on the remaining balance and the full payment is applied every month; it does not model variable rates, promotional periods, or payment holidays.
- The engine has no database or HTTP dependency — callers (see `apps/api/src/family_cfo_api/finance_service.py`) are responsible for loading inputs and persisting the returned `CalculationResult` for audit.

## Tests

```bash
cd services/financial-engine
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests
```

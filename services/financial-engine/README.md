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

Debt payoff, retirement projections, and multi-variable scenario planning are not implemented yet; they are planned for later milestones (M3 scenario planning builds on the `scenarios` table added in M2).

## Assumptions and Limitations

- All inputs to a calculation must share one currency; the engine does not perform currency conversion.
- Recurring frequency normalization assumes a fixed 12-month year (`weekly` = 52/12, `biweekly` = 26/12, `semimonthly` = 24/12, `quarterly` = 1/3, `annual` = 1/12).
- `calculate_net_worth` assumes account balances are already signed correctly by the caller (asset accounts positive, liability accounts negative); the engine does not infer sign from account type.
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

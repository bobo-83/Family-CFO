"""App-side tool library for the agentic advisor (M16, ADR 0009).

The model orchestrates the deterministic financial engine by calling these
tools. The trust boundary is here: every tool validates its arguments before
touching the engine, read tools are scoped to the caller's household (the model
never supplies an entity id), and every returned figure comes from an engine
calculation -- never from the model. Bad arguments and missing facts are
returned as structured error payloads (never raised) so the loop can feed them
back for the model to correct itself or ask the user.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

from family_cfo_ai_orchestrator import ToolCallingResult, ToolSpec, extract_numbers
from family_cfo_financial_engine import (
    CalculationResult,
    DebtInput,
    FutureValueInput,
    Money,
    RetirementInput,
)
from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, repository
from family_cfo_api.config import Settings, get_settings
from family_cfo_api.explanation import format_money
from family_cfo_ai_orchestrator.tool_calling import ToolExecutor

# Personality layer (M31): tone only. The grounding rules below are appended
# verbatim in every tone — fun never loosens the numbers discipline.
PERSONAS = {
    "playful": (
        "Personality: you are the family's upbeat, warm, slightly cheeky CFO — "
        "think favorite-money-nerd-friend, not bank clerk. Be conversational and "
        "encouraging, celebrate wins ('your emergency fund is flexing 💪'), use at "
        "most one fitting emoji per answer, and drop an occasional light money pun "
        "when the news is good. When the news is bad, drop the jokes: be kind, "
        "plain, and honest — never joke away a real risk, and never let charm "
        "replace clarity about what the numbers say."
    ),
    "professional": (
        "Personality: you are a concise, professional family CFO. Warm but "
        "businesslike; no emoji, no jokes."
    ),
}


def build_system_prompt(settings: Settings | None = None, *, today: date | None = None) -> str:
    """The chat system prompt: persona (by tone setting) + today's date + the
    invariant grounding rules. The date matters because the model's training
    predates the app's real 'now', so without it the model calls the current
    year 'the future' and refuses to look up months that have already happened."""
    settings = settings or get_settings()
    persona = PERSONAS.get(settings.ai_tone, PERSONAS["playful"])
    today = today or date.today()
    date_line = (
        f"Today's date is {today.isoformat()} — the current month is {today:%B %Y}. "
        "Any month on or before this has ALREADY happened: its data exists, so look it up "
        "with the month tools (they take a YYYY-MM). Only dates strictly AFTER today are the "
        "future. Never tell the user that the current month or a past month 'hasn't happened "
        "yet' or 'is in the future'."
    )
    return f"{persona}\n\n{date_line}\n\n{GROUNDING_RULES}"


def build_household_context(
    *,
    currency: str,
    first_name: str | None = None,
    member_count: int = 1,
    earliest_month: str | None = None,
    latest_month: str | None = None,
) -> str:
    """A system message of stable household facts the model can't derive on its
    own: who it's speaking with, the currency, the family size, and which months
    hold data — so it never asks for these or claims a real month has no data."""
    lines = ["Household context (rely on these without a tool call):"]
    if first_name:
        lines.append(f"- You are speaking with {first_name}.")
    plural = "s" if member_count != 1 else ""
    lines.append(f"- This is one family with {member_count} member{plural} in the app.")
    lines.append(f"- All amounts are in {currency} unless a tool result says otherwise.")
    if earliest_month and latest_month:
        lines.append(
            f"- Transaction data exists from {earliest_month} through {latest_month}. Look up "
            "any month in that range with the month tools; there is simply no data outside it "
            "(never claim an in-range month is missing or hasn't happened)."
        )
    return "\n".join(lines)


# The model must ground every number in a tool result and must never invent a
# figure. When a required input is missing it asks the user rather than guess.
GROUNDING_RULES = (
    "You are a household financial assistant for a single family. Answer the "
    "user's question using ONLY the provided tools for any financial figure. "
    "Never state a number, amount, or projection you did not obtain from a tool "
    "result. Amounts are in minor currency units (e.g. cents); pass them as "
    "integers and quote the human-readable 'display' string from tool results "
    "back to the user. If a tool reports \"error\": \"missing_input\", ask the "
    "user to supply that fact instead of guessing. If a tool reports "
    "\"error\": \"invalid_arguments\", correct the arguments and try again. Keep "
    "the final answer to a few plain-language sentences. "
    "NEVER derive a spendable amount yourself by subtracting one tool's number "
    "from another's — arithmetic you perform is not grounded, and cash that is "
    "merely sitting in an account is not the same as cash that is free to spend. "
    "For ANY question about what the family can spend or afford ('how much money "
    "do I have to spend', 'can I afford this', 'is there room for X'), ALWAYS "
    "call get_safe_to_spend and quote its safe_to_spend display value: it already "
    "subtracts the designated emergency fund, the bills falling due, AND the "
    "minimum debt payments owed. Report its warnings — they say when the figure "
    "is overstated (e.g. debts with no recorded minimum payment). When its "
    "total_debt is above zero you MUST state that debt in the same answer: "
    "reporting spendable cash while saying nothing about what the family owes is "
    "true and still misleading. Never present "
    "liquid balances, or liquid minus the emergency fund, as though it were "
    "spendable money: that is the family's bill and debt money too. For a "
    "specific purchase also call project_purchase_impact for the cash-flow view, "
    "and never treat net worth as spendable — see get_net_worth's asset_breakdown, "
    "where retirement and education funds are NOT available (taxable investments "
    "only with a tax caveat). For currency "
    "conversion use the get_exchange_rate tool; for live item prices or other "
    "public facts use web_search when available — search only for the item or "
    "fact, never include names, account details, or other household information "
    "in a search query. For income, salary, RSU vest, bonus, or tax "
    "questions ALWAYS call get_income_and_tax first — it carries the household's "
    "declared compensation profile including upcoming vest dates (quote its "
    "assumptions when giving a tax figure); for "
    "bills or upcoming payments use get_bills; for budgets use get_budgets; for "
    "recent spending habits use get_spending_insights. For 'where can I cut', "
    "'how do I save money', or reducing spending, call find_savings and follow "
    "its note: suggest cutting WASTE first (duplicate/forgotten subscriptions, "
    "fees, categories that crept up), then the largest RECURRING discretionary "
    "habits — never a one_off_purchase (already spent) and never an activity in "
    "valued_activities (the family enjoys it) — and tie every trim to one of "
    "their goals (e.g. 'this frees $X/mo toward your emergency fund'). Offer "
    "options; never moralize. "
    "For debts, read get_debt_outlook: each debt's `payoff_now` is the ONE-TIME "
    "amount that clears it today (its balance plus about a month's interest). "
    "NEVER tell the user to send more than a debt's balance — you cannot pay off "
    "a $3,000 loan by sending $10,000. To model paying it down faster, use "
    "debt_payoff, whose extra_monthly_payment is a RECURRING monthly amount, not a "
    "one-time lump sum; the one-time amount to clear a debt is always its "
    "payoff_now. Do not invent an 'extra payment' the user didn't ask for, and "
    "quote debt_payoff's own months_to_payoff and interest — never your own. "
    "When recommending WHICH debt to pay down first, prioritize by INTEREST RATE, "
    "not by balance: the highest-rate debt (usually a credit card) costs the most "
    "and comes first. A debt at or below roughly 4-5% APR — at or below inflation, "
    "as many student loans are — is LOW priority: do NOT tell the user to rush to "
    "pay it off ahead of higher-rate debt or ahead of saving and investing, and say "
    "why. Each debt in get_debt_outlook carries a `strategy_note` when it needs "
    "special handling — read and honor it. A 401(k) loan is the clearest case: its "
    "interest is paid to the borrower's OWN retirement account and it is repaid by "
    "payroll deduction, so its rate is not a true cost — never recommend paying one "
    "off early just because its rate looks high. Before giving any debt plan, look "
    "at the WHOLE picture: every debt get_debt_outlook returns (credit cards and "
    "leases included, not just the small loans), plus get_bills and "
    "get_spending_insights for where the money actually goes."
)

# Backwards-compatible alias (professional baseline without persona).
TOOL_SYSTEM_PROMPT = GROUNDING_RULES


def _money_out(money: Money) -> dict[str, Any]:
    return {
        "amount_minor": money.amount_minor,
        "currency": money.currency,
        "display": format_money(money),
    }


def _serialize(value: Any) -> Any:
    if isinstance(value, Money):
        return _money_out(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


def _result_payload(result: CalculationResult, calculation_id: str) -> dict[str, Any]:
    return {
        "outputs": {key: _serialize(value) for key, value in result.outputs.items()},
        "assumptions": list(result.assumptions),
        "warnings": list(result.warnings),
        "calculation_ref": f"financial_calculations:{calculation_id}",
    }


# --- argument validation helpers -------------------------------------------
# Each returns (value, error_dict). On failure value is None and the caller
# should return the error payload immediately.


def _missing(field: str) -> dict[str, Any]:
    return {"error": "missing_input", "missing": field}


def _invalid(detail: str) -> dict[str, Any]:
    return {"error": "invalid_arguments", "detail": detail}


def _int_arg(
    args: dict[str, Any], field: str, *, minimum: int | None = None
) -> tuple[int | None, dict[str, Any] | None]:
    if field not in args or args[field] is None:
        return None, _missing(field)
    value = args[field]
    if isinstance(value, bool) or not isinstance(value, int):
        return None, _invalid(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        return None, _invalid(f"{field} must be >= {minimum}")
    return value, None


def _rate_arg(
    args: dict[str, Any], field: str
) -> tuple[float | None, dict[str, Any] | None]:
    if field not in args or args[field] is None:
        return None, _missing(field)
    value = args[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, _invalid(f"{field} must be a number")
    if value < 0 or value > 1:
        return None, _invalid(f"{field} must be a decimal fraction between 0 and 1")
    return float(value), None


def _currency_arg(
    args: dict[str, Any], household_currency: str
) -> tuple[str | None, dict[str, Any] | None]:
    # This household is single-currency; the model may omit currency (it
    # defaults) but may not name a different one.
    supplied = args.get("currency")
    if supplied is None:
        return household_currency, None
    if not isinstance(supplied, str) or supplied.upper() != household_currency:
        return None, _invalid(
            f"this household operates in {household_currency}; other currencies are not supported"
        )
    return household_currency, None


# --- tool handlers ----------------------------------------------------------


# Spendability categories (M33): shared with the overview endpoint since M38.
_CATEGORY_BY_TYPE = finance_service.ASSET_CATEGORY_BY_TYPE


def _get_net_worth(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    today, err = _month_to_today(args)
    if err:
        return err
    if today is not None:
        from family_cfo_financial_engine import Money as _Money

        from family_cfo_api import repository
        from family_cfo_api.api.budgets import _month_window

        _, end = _month_window(today)
        minor = repository.net_worth_as_of(engine, household_id, end, currency)
        return {
            "as_of_month": args.get("month"),
            "net_worth": _money_out(_Money(minor, currency)),
            "note": (
                "Net worth from the snapshot at/near that month's end. The asset breakdown "
                "and spendability detail are only available for the current month."
            ),
        }

    result, calc_id = finance_service.compute_net_worth_with_ref(engine, household_id, currency)
    payload = _result_payload(result, calc_id)

    # M33: break assets into spendability categories so the model never treats
    # retirement or education money as available for a purchase.
    from family_cfo_financial_engine import Money as _Money

    from family_cfo_api import repository

    totals: dict[str, int] = {}
    emergency_reserved = 0
    for balance in repository.list_account_balances(engine, household_id):
        if balance.currency != currency:
            continue
        # M36: user-designated emergency reservations (any account, any category).
        emergency_reserved += repository.emergency_fund_reserved_minor(
            balance.emergency_fund_percent, balance.emergency_fund_minor, balance.balance_minor
        )
        if balance.balance_minor < 0 or balance.account_type not in _CATEGORY_BY_TYPE:
            continue
        category = _CATEGORY_BY_TYPE[balance.account_type]
        totals[category] = totals.get(category, 0) + balance.balance_minor
    payload["asset_breakdown"] = {
        category: _money_out(_Money(minor, currency)) for category, minor in totals.items()
    }
    payload["emergency_fund_reserved"] = _money_out(_Money(emergency_reserved, currency))
    payload["spendability_note"] = (
        "Only 'liquid' is readily spendable. 'investments' can be sold but may "
        "trigger taxes. 'retirement' and 'education' are NOT available for "
        "purchases (early-withdrawal penalties / different purpose). 'property' "
        "is illiquid. 'emergency_fund_reserved' is money the family set aside "
        "for emergencies — subtract it from liquid funds before judging "
        "affordability; never suggest spending it on a purchase."
    )
    return payload


def _get_emergency_fund(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    result, calc_id = finance_service.compute_emergency_fund_with_ref(
        engine, household_id, currency
    )
    return _result_payload(result, calc_id)


def _get_safe_to_spend(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    result, calc_id = finance_service.compute_safe_to_spend(engine, household_id, currency)
    return _result_payload(result, calc_id)


# Roughly the long-run return of a diversified portfolio / prevailing inflation:
# below this, aggressively paying down a debt loses to investing or saving.
_LOW_RATE_THRESHOLD = 0.05


def _debt_strategy_note(debt) -> str | None:
    """Per-debt guidance the advisor must honor when prioritizing paydown — the
    rate-and-401(k) nuances a naive 'smallest balance first' plan gets wrong."""
    if debt.account_type in repository.RETIREMENT_LOAN_TYPES:
        return (
            "401(k) loan: the interest is paid to your OWN retirement account and "
            "it is repaid by payroll deduction, so its rate is not a true cost — "
            "do not prioritize paying it off early just because the rate looks high."
        )
    if debt.annual_interest_rate <= _LOW_RATE_THRESHOLD:
        return (
            f"Low rate ({debt.annual_interest_rate * 100:.2f}% APR), at or below "
            "inflation and typical investment returns — LOW priority; do not rush to "
            "pay it off ahead of higher-rate debt or saving/investing."
        )
    return None


def _get_debt_outlook(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    outlook = finance_service.compute_debt_outlook(engine, household_id, currency)
    # Per-debt detail so the advisor NEVER asks the user for a balance, rate, or
    # minimum payment the app already stores (the minimize-duplicate-input rule).
    # A debt is "interest-only" when its minimum barely covers the monthly
    # interest, so it never amortizes — worth flagging, but the numbers are known.
    debts = []
    for debt in repository.list_debts_with_terms(engine, household_id):
        if debt.currency != currency:
            continue
        monthly_interest = round(debt.balance_owed_minor * debt.annual_interest_rate / 12)
        debts.append(
            {
                "name": debt.name,
                "type": debt.account_type,
                "balance": _money_out(Money(debt.balance_owed_minor, debt.currency)),
                "annual_interest_rate": debt.annual_interest_rate,
                "minimum_payment": _money_out(
                    Money(debt.minimum_payment_minor, debt.currency)
                ),
                # The one-time amount that clears this debt today: balance plus
                # ~a month's interest. The advisor must never advise sending more.
                "payoff_now": _money_out(
                    Money(debt.balance_owed_minor + monthly_interest, debt.currency)
                ),
                "interest_only": debt.minimum_payment_minor <= monthly_interest,
                # Editorial guidance the advisor must honor when prioritizing.
                "strategy_note": _debt_strategy_note(debt),
            }
        )
    return {
        "modeled_debts": outlook.modeled_count,
        "unmodeled_debts": outlook.unmodeled_count,
        "debts": debts,
        "total_interest_remaining": (
            _money_out(outlook.total_interest_remaining)
            if outlook.total_interest_remaining is not None
            else None
        ),
        "longest_payoff_months": outlook.longest_months,
        "warnings": list(outlook.warnings),
    }


def _get_debt_history(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    history = finance_service.debt_history(engine, household_id, currency)
    return {
        "months": [
            {"month": p.month, "total_owed": _money_out(p.total_owed)} for p in history.points
        ],
        "average_debt": _money_out(history.average),
        "months_covered": history.months_covered,
        "note": (
            "Total debt at each month-end, reconstructed from transaction history — "
            "approximate before daily balances began, exact after. 'Lifetime' spans only "
            "the months of data that exist; say so rather than implying a longer record."
        ),
    }


def _project_purchase_impact(
    engine: Engine, household_id: str, currency: str, args: dict[str, Any]
):
    resolved_currency, error = _currency_arg(args, currency)
    if error:
        return error
    price_minor, error = _int_arg(args, "price_minor", minimum=0)
    if error:
        return error
    result, calc_id = finance_service.compute_purchase_impact(
        engine, household_id, resolved_currency, Money(price_minor, resolved_currency)
    )
    return _result_payload(result, calc_id)


def _future_value(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    resolved_currency, error = _currency_arg(args, currency)
    if error:
        return error
    present_value_minor, error = _int_arg(args, "present_value_minor", minimum=0)
    if error:
        return error
    rate, error = _rate_arg(args, "annual_return_rate")
    if error:
        return error
    years, error = _int_arg(args, "years", minimum=0)
    if error:
        return error
    result, calc_id = finance_service.compute_future_value(
        engine,
        household_id,
        FutureValueInput(
            present_value=Money(present_value_minor, resolved_currency),
            annual_return_rate=rate,
            years=years,
        ),
    )
    return _result_payload(result, calc_id)


def _project_retirement(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    resolved_currency, error = _currency_arg(args, currency)
    if error:
        return error
    current_age, error = _int_arg(args, "current_age", minimum=0)
    if error:
        return error
    retirement_age, error = _int_arg(args, "retirement_age", minimum=0)
    if error:
        return error
    if retirement_age <= current_age:
        return _invalid("retirement_age must be greater than current_age")
    current_savings_minor, error = _int_arg(args, "current_savings_minor", minimum=0)
    if error:
        return error
    monthly_contribution_minor, error = _int_arg(args, "monthly_contribution_minor", minimum=0)
    if error:
        return error
    rate, error = _rate_arg(args, "annual_return_rate")
    if error:
        return error
    # annual_expenses is optional -- only supplied when the user asks about
    # expense coverage / "can we retire" style questions.
    annual_expenses = None
    if args.get("annual_expenses_minor") is not None:
        expenses_minor, error = _int_arg(args, "annual_expenses_minor", minimum=0)
        if error:
            return error
        annual_expenses = Money(expenses_minor, resolved_currency)

    result, calc_id = finance_service.compute_retirement_projection(
        engine,
        household_id,
        RetirementInput(
            current_age=current_age,
            retirement_age=retirement_age,
            current_savings=Money(current_savings_minor, resolved_currency),
            monthly_contribution=Money(monthly_contribution_minor, resolved_currency),
            annual_return_rate=rate,
            annual_expenses=annual_expenses,
        ),
    )
    return _result_payload(result, calc_id)


def _debt_payoff(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    resolved_currency, error = _currency_arg(args, currency)
    if error:
        return error
    balance_minor, error = _int_arg(args, "balance_minor", minimum=0)
    if error:
        return error
    rate, error = _rate_arg(args, "annual_interest_rate")
    if error:
        return error
    minimum_payment_minor, error = _int_arg(args, "minimum_payment_minor", minimum=0)
    if error:
        return error
    extra = None
    if args.get("extra_monthly_payment_minor") is not None:
        extra_minor, error = _int_arg(args, "extra_monthly_payment_minor", minimum=0)
        if error:
            return error
        extra = Money(extra_minor, resolved_currency)

    result, calc_id = finance_service.compute_debt_payoff(
        engine,
        household_id,
        DebtInput(
            debt_id="hypothetical",
            name="hypothetical debt",
            balance=Money(balance_minor, resolved_currency),
            annual_interest_rate=rate,
            minimum_payment=Money(minimum_payment_minor, resolved_currency),
            extra_monthly_payment=extra,
        ),
    )
    return _result_payload(result, calc_id)


# --- Live-data tools (ADR 0014) ----------------------------------------------

_ISO_CODE = re.compile(r"^[A-Za-z]{3}$")
_RATE_PROVIDER = "https://open.er-api.com/v6/latest/{base}"
_LIVE_TIMEOUT_SECONDS = 6.0
_SEARCH_QUERY_MAX = 200


def _get_exchange_rate(args: dict[str, Any]) -> dict[str, Any]:
    base, quote = str(args.get("base", "")).upper(), str(args.get("quote", "")).upper()
    if not _ISO_CODE.match(base):
        return _missing("base") if not base else _invalid("base must be a 3-letter currency code")
    if not _ISO_CODE.match(quote):
        return _missing("quote") if not quote else _invalid("quote must be a 3-letter currency code")
    amount_minor = None
    if args.get("amount_minor") is not None:
        amount_minor, error = _int_arg(args, "amount_minor", minimum=0)
        if error:
            return error
    try:
        response = httpx.get(_RATE_PROVIDER.format(base=base), timeout=_LIVE_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        rate = (data.get("rates") or {}).get(quote)
    except (httpx.HTTPError, ValueError):
        return {"error": "lookup_failed", "detail": "exchange-rate provider unreachable"}
    if rate is None:
        return _invalid(f"no rate available for {base}->{quote}")
    result: dict[str, Any] = {
        "base": base,
        "quote": quote,
        "rate": rate,
        "as_of": data.get("time_last_update_utc", "unknown"),
        "source": "open.er-api.com (ECB-style daily rates)",
    }
    if amount_minor is not None:
        # The engine calculates, never the model (ADR 0003): the conversion is
        # done here with Decimal so the model can quote a grounded figure.
        converted_minor = int(
            (Decimal(amount_minor) * Decimal(str(rate))).to_integral_value(rounding=ROUND_HALF_UP)
        )
        result["amount"] = _money_out(Money(amount_minor, base))
        result["converted"] = _money_out(Money(converted_minor, quote))
    return result


def _web_search(args: dict[str, Any], searxng_url: str) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return _missing("query")
    if len(query) > _SEARCH_QUERY_MAX:
        return _invalid(f"query must be at most {_SEARCH_QUERY_MAX} characters")
    try:
        response = httpx.get(
            f"{searxng_url.rstrip('/')}/search",
            params={"q": query, "format": "json"},
            timeout=_LIVE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        results = (response.json().get("results") or [])[:5]
    except (httpx.HTTPError, ValueError):
        return {"error": "lookup_failed", "detail": "search service unreachable"}
    return {
        "query": query,
        "results": [
            {
                "title": item.get("title", ""),
                "snippet": (item.get("content") or "")[:300],
                "url": item.get("url", ""),
            }
            for item in results
        ],
    }


def _search_backends(settings: Settings):
    """The embedder + vector store pair; a seam so tests inject fakes."""
    from family_cfo_api.embeddings import get_default_embedder
    from family_cfo_api.vector_store import QdrantVectorStore

    return get_default_embedder(), QdrantVectorStore(settings.qdrant_url)


def _search_records(household_id: str, args: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """M69: embed the query, search the household's indexed records.

    Best-effort recall — the deterministic aggregate tools remain the
    authority for totals. Every figure returned here is grounded via the
    normal tool trace.
    """
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "invalid_arguments", "detail": "query is required"}
    try:
        embedder, store = _search_backends(settings)
        vector = embedder.embed([query])[0]
        hits = store.search(vector, household_id, limit=8)
    except Exception as exc:  # noqa: BLE001 — honest failure beats a crash
        return {"error": "lookup_failed", "detail": str(exc)[:200]}
    return {
        "matches": [
            {
                "kind": hit.payload.get("kind"),
                "date": hit.payload.get("date"),
                "description": hit.payload.get("text"),
                "amount": hit.payload.get("amount_display"),
                "account": hit.payload.get("account"),
            }
            for hit in hits
        ],
        "note": "Semantic matches from the household's own records; use the "
        "aggregate tools for totals.",
    }


def _schema_money_out(money) -> dict[str, Any]:
    """schemas.Money → the same shape _money_out gives engine Money."""
    return _money_out(Money(money.amount_minor, money.currency))


def _get_income_and_tax(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    """M64: the M61–M63 income analysis + tax estimate, compacted for the model."""
    from family_cfo_api.api.income_analysis import build_income_analysis

    household = repository.get_household(engine, household_id)
    if household is None:
        return {"error": "missing_input", "detail": "household not found"}

    today, err = _month_to_today(args)
    if err:
        return err
    if today is not None:
        from family_cfo_financial_engine import Money as _Money

        from family_cfo_api.api.budgets import _month_window

        start, end = _month_window(today)
        minor = repository.sum_income(engine, household_id, start, end, currency)
        return {
            "month": args.get("month"),
            "income_received": _money_out(_Money(minor, currency)),
            "note": (
                "Actual money categorized as Income that month. The compensation profile and "
                "tax estimate are the current setup only, not historical."
            ),
        }

    analysis = build_income_analysis(engine, household)
    tax = analysis.tax
    warnings: list[str] = []
    if analysis.coverage_warning:
        warnings.append(analysis.coverage_warning)
    # M73: declared compensation — the authority on gross income when present.
    profile_out = None
    if analysis.profile is not None:
        profile_out = {
            "expected_annual_gross": _schema_money_out(analysis.profile.expected_annual_gross),
            "earners": [
                {
                    "label": earner.label,
                    "base_salary": _schema_money_out(earner.base_salary),
                    "rsu_annual_value": _schema_money_out(earner.rsu_annual),
                    "rsu_vesting": earner.rsu_frequency,
                    "rsu_next_vest_date": (
                        earner.rsu_next_vest_date.isoformat()
                        if earner.rsu_next_vest_date is not None
                        else None
                    ),
                    "bonus_percent_of_base": earner.bonus_percent,
                    "bonus_month": earner.bonus_month,
                    # W2 actuals were only a prose assumption line before;
                    # structured values let the model quote Box 2 exactly.
                    "last_year_w2": (
                        {
                            "year": earner.w2_year,
                            "box1_wages": _schema_money_out(earner.w2_wages),
                            "box2_federal_withheld": _schema_money_out(earner.w2_withheld),
                        }
                        if earner.w2_wages is not None and earner.w2_withheld is not None
                        else None
                    ),
                }
                for earner in analysis.profile.earners
            ],
            "upcoming_income_events": [
                {
                    "date": event.date.isoformat(),
                    "label": event.label,
                    "amount": _schema_money_out(event.amount),
                }
                for event in analysis.profile.expected_events
            ],
            "note": (
                "All profile amounts and upcoming income events are PRE-TAX "
                "(gross). RSU taxes are typically withheld at vest (shares "
                "sold to cover), so the cash or shares received are worth "
                "less than the vest value."
            ),
        }
    return {
        "compensation_profile": profile_out,
        "income_sources": [
            {
                "name": source.name,
                "frequency": source.frequency,
                "typical_deposit": _schema_money_out(source.typical_amount),
                "total_in_window": _schema_money_out(source.total_amount),
                "deposit_count": len(source.transactions),
            }
            for source in analysis.sources
        ],
        "annual_income": _schema_money_out(analysis.rollup.annual_income),
        "monthly_average_income": _schema_money_out(analysis.rollup.monthly_average),
        "window_days": analysis.rollup.window_days,
        "tax_estimate": {
            "tax_year": tax.tax_year,
            "filing_status": tax.filing_status,
            "income_treated_as_take_home": tax.income_treated_as_net,
            "state": tax.state,
            "estimated_gross_income": _schema_money_out(tax.gross_income),
            "federal_income_tax": _schema_money_out(tax.federal_income_tax),
            "social_security_and_medicare": _schema_money_out(tax.fica_tax),
            "state_income_tax": (
                _schema_money_out(tax.state_income_tax)
                if tax.state_income_tax is not None
                else None
            ),
            "estimated_total_tax": _schema_money_out(tax.total_tax),
            "effective_rate": tax.effective_rate,
        },
        "assumptions": list(tax.assumptions),
        "warnings": warnings,
    }


def _get_bills(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    """M64: recurring bills plus the next-14-days upcoming view."""
    bills = repository.list_bills(engine, household_id)
    upcoming = finance_service.upcoming_bills(engine, household_id, currency)
    return {
        "bills": [
            {
                "name": bill.name,
                "amount": _money_out(Money(bill.amount_minor, bill.currency)),
                "frequency": bill.frequency,
                "next_due_date": bill.next_due_date.isoformat() if bill.next_due_date else None,
            }
            for bill in bills
        ],
        "due_within_14_days": [
            {
                "name": bill.name,
                "amount": _money_out(bill.amount),
                "due_date": bill.due_date.isoformat(),
                "days_until": bill.days_until,
            }
            for bill in upcoming
        ],
    }


def _month_to_today(args: dict[str, Any]) -> tuple[date | None, dict[str, Any] | None]:
    """Turn an optional `month` (YYYY-MM) arg into a `today` inside that month —
    any day picks the month's window. Returns (today, error) where error is a
    ready-to-return note dict when the month can't be parsed; today is None for
    the current month (no arg)."""
    month = args.get("month")
    if not month:
        return None, None
    try:
        year, mon = (int(p) for p in str(month).split("-")[:2])
        return date(year, mon, 15), None
    except (ValueError, TypeError):
        return None, {"note": f"Couldn't read month '{month}'. Use YYYY-MM, e.g. 2026-05."}


# Reusable schema for the optional month argument on time-scoped tools.
_MONTH_PARAM = {
    "type": "object",
    "properties": {
        "month": {
            "type": "string",
            "description": "Target month as YYYY-MM (e.g. 2026-05). Omit for the current month.",
        }
    },
    "additionalProperties": False,
}


def _get_budgets(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    """M64: per-category envelope progress. Defaults to the current month; a
    `month` (YYYY-MM) arg pulls a past month's budget vs actual."""
    from family_cfo_api.api.budgets import budgets_with_progress

    today, err = _month_to_today(args)
    if err:
        return {"month_budgets": [], **err}
    budgets = budgets_with_progress(engine, household_id, currency, today=today)
    return {
        "month_budgets": [
            {
                "category": budget.category_name,
                "limit": _schema_money_out(budget.limit),
                "spent_so_far": _schema_money_out(budget.spent),
                "remaining": _schema_money_out(budget.remaining),
                "percent_used": budget.percent_used,
                "status": budget.status,
            }
            for budget in budgets
        ],
    }


def _get_spending_by_category(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    """M94: outflow grouped by category — answers 'how much did I spend on
    groceries/dining/…'. Defaults to the current month; a `month` (YYYY-MM)
    arg pulls any past month so the advisor can compare across months. Reuses
    the Overview builder so the chat answer and the Overview card never disagree."""
    from family_cfo_api.api.household import _spending_by_category

    today, err = _month_to_today(args)
    if err:
        return {"month": None, "categories": [], **err}

    result = _spending_by_category(engine, household_id, currency, today=today)
    if result is None:
        label = args.get("month") or "this month"
        return {"month": args.get("month"), "categories": [], "note": f"No spending recorded for {label}."}
    return {
        "month": result.month_label,
        "categories": [
            {"category": c.category_name, "spent": _schema_money_out(c.amount)}
            for c in result.categories
        ],
        "categorized_total": _schema_money_out(result.categorized_total),
        "uncategorized": _schema_money_out(result.uncategorized),
    }


def _get_spending_insights(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    """M64: spending vs the same window the prior month + top merchants. Defaults
    to the current month; a `month` (YYYY-MM) arg compares that month to the one
    before it."""
    from family_cfo_api.api.household import _spending_insights

    today, err = _month_to_today(args)
    if err:
        return {"top_merchants": [], **err}
    insights = _spending_insights(engine, household_id, currency, today=today)
    return {
        "month_to_date_spending": _schema_money_out(insights.this_month),
        "same_window_last_month": _schema_money_out(insights.last_month),
        "change_percent": insights.change_percent,
        "top_merchants": [
            {"merchant": m.merchant, "total": _schema_money_out(m.amount)}
            for m in insights.top_merchants
        ],
    }


def _find_savings(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    """Waste-first savings opportunities (ADR 0047): the needs/wants split,
    discretionary spend ranked, subscriptions and likely waste, the activities
    the household values (protect these), and their goals (tie trims to these)."""
    from family_cfo_api import savings

    report = savings.find_savings(engine, household_id, currency)
    return {
        "essential_monthly": _money_out(report.essential_monthly),
        "discretionary_monthly": _money_out(report.discretionary_monthly),
        "recurring_discretionary": [
            {"category": c.name, "monthly_avg": _money_out(c.monthly_avg)}
            for c in report.recurring_ranked
        ],
        "one_off_purchases": [
            {"category": o.name, "total_spent": _money_out(o.total)} for o in report.one_off
        ],
        "subscriptions": [
            {"merchant": s.merchant, "amount": _money_out(s.amount), "cadence": s.cadence}
            for s in report.subscriptions
        ],
        "possible_waste": report.possible_waste,
        "valued_activities": report.valued_activities,
        "goals": [{"name": name, "gap_to_target": _money_out(gap)} for name, gap in report.goals],
        "note": (
            "Suggest trims from RECURRING_DISCRETIONARY (real monthly habits) and cut WASTE "
            "first (possible_waste, unused subscriptions). one_off_purchases are already spent "
            "(a renovation, a trip) — do NOT ask the user to 'cut' those; mention them only as "
            "context. PROTECT valued_activities. Tie each trim to a goal. Amounts are averaged "
            "over the last 3 complete months."
        ),
    }


_HANDLERS = {
    "get_net_worth": _get_net_worth,
    "get_emergency_fund": _get_emergency_fund,
    "get_safe_to_spend": _get_safe_to_spend,
    "get_debt_outlook": _get_debt_outlook,
    "get_debt_history": _get_debt_history,
    "project_purchase_impact": _project_purchase_impact,
    "future_value": _future_value,
    "project_retirement": _project_retirement,
    "debt_payoff": _debt_payoff,
    "get_income_and_tax": _get_income_and_tax,
    "get_bills": _get_bills,
    "get_budgets": _get_budgets,
    "get_spending_insights": _get_spending_insights,
    "get_spending_by_category": _get_spending_by_category,
    "find_savings": _find_savings,
}

_MONEY_FIELD = {"type": "integer", "description": "amount in minor currency units (e.g. cents)"}
_CURRENCY_FIELD = {
    "type": "string",
    "description": "ISO currency code; defaults to the household base currency",
}
_RATE_FIELD = {"type": "number", "description": "annual rate as a decimal fraction, e.g. 0.06 for 6%"}


def build_tools(settings: Settings | None = None) -> list[ToolSpec]:
    """The JSON-schema descriptors advertised to the model (some settings-gated)."""
    settings = settings or get_settings()
    tools = [
        ToolSpec(
            name="get_net_worth",
            description=(
                "Household net worth, total assets, and total liabilities. Defaults to now "
                "(with an asset breakdown); pass `month` (YYYY-MM) for the net worth at that "
                "past month's end."
            ),
            parameters=_MONTH_PARAM,
        ),
        ToolSpec(
            name="get_emergency_fund",
            description=(
                "Months of essential expenses the household's liquid savings would cover. "
                "Monthly essential expenses = recurring bills + debt minimum payments + "
                "everyday spending above those bills (not bills alone)."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="get_safe_to_spend",
            description=(
                "THE tool for 'how much money do I have to spend', 'can I afford X', or any "
                "discretionary-spending question. Returns safe_to_spend: liquid cash MINUS the "
                "designated emergency fund, MINUS bills falling due, MINUS minimum debt "
                "payments. Never derive spendable money yourself — this figure already nets "
                "out every obligation, and its warnings say when it is overstated."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="get_debt_outlook",
            description=(
                "The household's debts with terms: a `debts` array giving EACH debt's name, "
                "type, current balance, interest rate, minimum payment, and whether it is "
                "interest-only, plus aggregate remaining interest and the longest payoff "
                "horizon. These balances/rates/minimums are already stored — ALWAYS call this "
                "before asking the user for any debt figure, and feed its numbers straight "
                "into debt_payoff. Never ask the user for a balance or minimum this returns."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="get_debt_history",
            description=(
                "Total debt at each month-end over the household's history, plus the AVERAGE "
                "debt across that window and how many months it covers. THE tool for 'average "
                "debt over time', 'how has my debt changed', or any multi-month/lifetime debt "
                "question — get_debt_outlook is only the current snapshot. 'Lifetime' spans "
                "only the months of data that exist (see months_covered); never imply more."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="project_purchase_impact",
            description="How a one-off purchase of the given price affects net worth and cash buffers.",
            parameters={
                "type": "object",
                "properties": {"price_minor": _MONEY_FIELD, "currency": _CURRENCY_FIELD},
                "required": ["price_minor"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="future_value",
            description=(
                "Future value of a lump sum after N years of annual compounding -- use for "
                "opportunity-cost questions (what an amount could grow to if invested)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "present_value_minor": _MONEY_FIELD,
                    "annual_return_rate": _RATE_FIELD,
                    "years": {"type": "integer", "description": "number of whole years"},
                    "currency": _CURRENCY_FIELD,
                },
                "required": ["present_value_minor", "annual_return_rate", "years"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="project_retirement",
            description=(
                "Project retirement savings from current age to retirement age given monthly "
                "contributions and a return rate. Supply annual_expenses_minor to also estimate "
                "how many years of expenses the balance would cover."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "current_age": {"type": "integer"},
                    "retirement_age": {"type": "integer"},
                    "current_savings_minor": _MONEY_FIELD,
                    "monthly_contribution_minor": _MONEY_FIELD,
                    "annual_return_rate": _RATE_FIELD,
                    "annual_expenses_minor": {
                        **_MONEY_FIELD,
                        "description": "optional: annual retirement spending, for coverage estimate",
                    },
                    "currency": _CURRENCY_FIELD,
                },
                "required": [
                    "current_age",
                    "retirement_age",
                    "current_savings_minor",
                    "monthly_contribution_minor",
                    "annual_return_rate",
                ],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="debt_payoff",
            description=(
                "Months to pay off a debt and total interest paid. For one of the "
                "household's REAL debts, first call get_debt_outlook and pass that debt's "
                "balance, rate, and minimum straight in — do not ask the user for numbers it "
                "already returned. Only ask the user when modeling a purely hypothetical debt "
                "they describe that isn't in their accounts."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "balance_minor": _MONEY_FIELD,
                    "annual_interest_rate": _RATE_FIELD,
                    "minimum_payment_minor": _MONEY_FIELD,
                    "extra_monthly_payment_minor": {
                        **_MONEY_FIELD,
                        "description": "optional: extra paid on top of the minimum each month",
                    },
                    "currency": _CURRENCY_FIELD,
                },
                "required": ["balance_minor", "annual_interest_rate", "minimum_payment_minor"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="get_income_and_tax",
            description=(
                "The household's income picture: the declared compensation profile (base "
                "salary, RSU annual value and VESTING SCHEDULE with upcoming vest dates and "
                "amounts, bonus percent and month, last year's W2 actuals — Box 1 wages and "
                "Box 2 federal withholding), detected recurring deposits, and the "
                "deterministic annual tax estimate (federal + FICA + modeled state, with "
                "assumptions). Use for ANY question about income, salary, RSU vests, "
                "bonuses, W2s, withholding, upcoming pay, or taxes. Pass `month` (YYYY-MM) "
                "to get the actual income received in a past month."
            ),
            parameters=_MONTH_PARAM,
        ),
        ToolSpec(
            name="get_bills",
            description=(
                "The household's recurring bills and which are due within the next 14 days. "
                "Use for questions about bills, subscriptions, or upcoming payments."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="get_budgets",
            description=(
                "Budget envelopes per spending category: limit, spent, remaining, and status. "
                "Use for budget questions. Defaults to the current month; pass `month` (YYYY-MM) "
                "for a past month."
            ),
            parameters=_MONTH_PARAM,
        ),
        ToolSpec(
            name="get_spending_insights",
            description=(
                "Spending vs the same window the prior month, plus the top merchants. Use for "
                "questions about spending habits. Defaults to the current month; pass `month` "
                "(YYYY-MM) to compare any month to the one before it."
            ),
            parameters=_MONTH_PARAM,
        ),
        ToolSpec(
            name="find_savings",
            description=(
                "THE tool for 'where can I cut', 'how can I save money', 'what should I trim', "
                "or any reduce-spending question. Returns the needs/wants split, RECURRING "
                "discretionary habits ranked (where trims actually stick), one-off purchases "
                "shown separately (already spent — don't tell the user to cut those), "
                "subscriptions and likely waste, the activities the household VALUES (never "
                "suggest cutting them), and their goals. Trim recurring habits and waste first, "
                "tie every trim to a goal, present options — never moralize."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="get_spending_by_category",
            description=(
                "Spending grouped by category (Groceries, Dining, …), plus what's still "
                "uncategorized. THE tool for 'how much did I spend on <category>' or 'where did "
                "my money go'. Defaults to the current month; pass `month` (YYYY-MM) for any past "
                "month — to compare several months, call it once per month."
            ),
            parameters=_MONTH_PARAM,
        ),
    ]
    if settings.live_data_enabled:
        tools.append(
            ToolSpec(
                name="get_exchange_rate",
                description=(
                    "Current currency exchange rate between two ISO codes (live, daily). Pass "
                    "amount_minor to also get the converted amount computed for you — never "
                    "multiply amounts by the rate yourself."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "base": {"type": "string", "description": "3-letter code, e.g. USD"},
                        "quote": {"type": "string", "description": "3-letter code, e.g. VND"},
                        "amount_minor": {
                            "type": "integer",
                            "description": "optional amount in base minor units to convert",
                        },
                    },
                    "required": ["base", "quote"],
                    "additionalProperties": False,
                },
            )
        )
    if settings.qdrant_url:
        tools.append(
            ToolSpec(
                name="search_records",
                description=(
                    "Semantic search over the household's OWN transaction history and "
                    "remembered facts — use for recall questions like 'when did we last "
                    "pay X' or 'how much was that repair'. Returns matching records with "
                    "dates and amounts; use the aggregate tools for totals."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "what to look for"}
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            )
        )
    if settings.searxng_url:
        tools.append(
            ToolSpec(
                name="web_search",
                description=(
                    "Search the web for public facts like current item prices. Query the item or "
                    "fact only — never include household or personal information."
                ),
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "search terms"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            )
        )
    return tools


def build_executor(
    engine: Engine, household_id: str, currency: str, settings: Settings | None = None
) -> ToolExecutor:
    """A household-scoped dispatcher the tool-calling loop invokes for each tool call."""
    settings = settings or get_settings()

    def execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        # Live-data tools (ADR 0014): only reachable when registered above.
        if name == "get_exchange_rate" and settings.live_data_enabled:
            return _get_exchange_rate(args)
        if name == "web_search" and settings.searxng_url:
            return _web_search(args, settings.searxng_url)
        # M69 (ADR 0017): semantic recall over the household's own records.
        if name == "search_records" and settings.qdrant_url:
            return _search_records(household_id, args, settings)
        handler = _HANDLERS.get(name)
        if handler is None:
            return {"error": "unknown_tool", "name": name}
        return handler(engine, household_id, currency, args)

    return execute


def _rounded_variants(number: str) -> set[str]:
    """Rounded forms of a numeric string, so "9.6470588" grounds "9.6"/"9.65"/"10".

    The guardrail is a string match; models naturally round long decimals when
    speaking. Rounding a tool figure is honest reporting, not fabrication.
    """
    try:
        value = float(number)
    except ValueError:
        return set()
    variants = {number}
    for digits in (0, 1, 2):
        rounded = round(value, digits)
        variants.add(f"{rounded:.{digits}f}")
        if rounded == int(rounded):
            variants.add(str(int(rounded)))
    return variants


def grounded_values(result: ToolCallingResult) -> set[str]:
    """Numbers the model was allowed to use: everything in the tool call trace.

    Both tool inputs (echoing a user-supplied figure is legitimate) and tool
    outputs (the engine's computed figures) count as grounded — including their
    rounded forms. Any number in the final answer outside this set is an
    invented figure and fails the guardrail.
    """
    known: set[str] = set()
    for record in result.tool_calls:
        for number in extract_numbers(json.dumps(record.arguments)) | extract_numbers(
            json.dumps(record.result)
        ):
            known |= _rounded_variants(number)
    return known

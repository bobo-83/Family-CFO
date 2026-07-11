"""Income analysis from checking-account deposits + annual tax estimate (M61).

Deterministic like everything money-facing (ADR 0003): recurring deposits are
detected by cadence pattern-matching with every underlying transaction shown,
the family edits the evidence per transaction (exclude a deposit, add one the
scan missed), and the tax figure is a bracket calculation with its assumptions
attached — not an AI guess.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine
from statistics import median

from family_cfo_financial_engine import (
    FILING_STATUSES,
    Money as EngineMoney,
    estimate_annual_tax,
    gross_up_from_net,
)

from family_cfo_api import audit, income_detection, repository
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    ErrorResponse,
    IncomeAnalysisResponse,
    IncomeAnalysisTransaction,
    IncomeOverrideRequest,
    IncomeRollup,
    IncomeSourceAnalysis,
    IncomeTaxSettingsRequest,
    TaxEstimate,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Income"])

# Trailing 12 months: the rollup is ACTUAL deposits over this window, not an
# annualized projection from cadence guesses.
ANALYSIS_WINDOW_DAYS = 365

_OTHER_INFLOWS_CAP = 100

DEFAULT_FILING_STATUS = "married_joint"


def _txn_item(
    txn: income_detection.IncomeTransaction, *, excluded: bool = False
) -> IncomeAnalysisTransaction:
    return IncomeAnalysisTransaction(
        transaction_id=txn.id,
        occurred_at=txn.occurred_at,
        amount=MoneySchema(amount_minor=txn.amount_minor, currency=txn.currency),
        name=txn.display_name,
        excluded=excluded,
    )


def _tax_estimate(
    annual_income_minor: int, currency: str, filing_status: str, treated_as_net: bool
) -> TaxEstimate:
    income = EngineMoney(annual_income_minor, currency)
    result = (
        gross_up_from_net(income, filing_status)
        if treated_as_net
        else estimate_annual_tax(income, filing_status)
    )
    outputs = result.outputs

    def money(key: str) -> MoneySchema:
        value = outputs[key]
        return MoneySchema(amount_minor=value.amount_minor, currency=value.currency)

    net = outputs.get("net_income")
    return TaxEstimate(
        tax_year=result.inputs["tax_year"],
        filing_status=filing_status,
        income_treated_as_net=treated_as_net,
        gross_income=money("gross_income"),
        net_income=(
            MoneySchema(amount_minor=net.amount_minor, currency=net.currency) if net else None
        ),
        standard_deduction=money("standard_deduction"),
        taxable_income=money("taxable_income"),
        federal_income_tax=money("federal_income_tax"),
        fica_tax=money("fica_tax"),
        total_tax=money("total_tax"),
        effective_rate=float(outputs["effective_rate"]),
        assumptions=list(result.assumptions),
    )


@router.get(
    "/income/analysis",
    operation_id="getIncomeAnalysis",
    response_model=IncomeAnalysisResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Detect income from checking-account deposits and estimate annual tax",
)
async def get_income_analysis(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> IncomeAnalysisResponse:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")

    since = date.today() - timedelta(days=ANALYSIS_WINDOW_DAYS)
    rows = repository.list_income_detection_transactions(
        engine, session.household_id, since=since
    )
    transactions = [
        income_detection.IncomeTransaction(
            id=txn_id,
            occurred_at=occurred_at,
            amount_minor=amount_minor,
            currency=currency,
            merchant=merchant,
            description=description,
        )
        for txn_id, occurred_at, amount_minor, currency, merchant, description in rows
    ]
    overrides = repository.list_income_overrides(engine, session.household_id)
    excluded_ids = {txn_id for txn_id, verdict in overrides.items() if verdict == "exclude"}
    included_ids = {txn_id for txn_id, verdict in overrides.items() if verdict == "include"}

    candidates = income_detection.detect_income_sources(
        transactions, excluded_ids=excluded_ids
    )
    detected_ids = {t.id for c in candidates for t in c.transactions}

    sources: list[IncomeSourceAnalysis] = []
    total_minor = 0
    count = 0
    for candidate in candidates:
        source_total = sum(t.amount_minor for t in candidate.transactions)
        if candidate.currency == household.base_currency:
            total_minor += source_total
            count += len(candidate.transactions)
        sources.append(
            IncomeSourceAnalysis(
                source_key=candidate.source_key,
                name=candidate.name,
                frequency=candidate.frequency,
                manually_added=False,
                typical_amount=MoneySchema(
                    amount_minor=candidate.typical_amount_minor, currency=candidate.currency
                ),
                total_amount=MoneySchema(
                    amount_minor=source_total, currency=candidate.currency
                ),
                transactions=[_txn_item(t) for t in candidate.transactions],
            )
        )

    # Deposits the family added by hand ("you missed this one").
    manual = [
        t
        for t in transactions
        if t.id in included_ids and t.id not in detected_ids and t.id not in excluded_ids
    ]
    if manual:
        manual_total = sum(t.amount_minor for t in manual)
        base_manual = [t for t in manual if t.currency == household.base_currency]
        total_minor += sum(t.amount_minor for t in base_manual)
        count += len(base_manual)
        sources.append(
            IncomeSourceAnalysis(
                source_key="_manual",
                name="Added by you",
                frequency="irregular",
                manually_added=True,
                typical_amount=MoneySchema(
                    amount_minor=int(median([t.amount_minor for t in manual])),
                    currency=household.base_currency,
                ),
                total_amount=MoneySchema(
                    amount_minor=manual_total, currency=household.base_currency
                ),
                transactions=[_txn_item(t) for t in manual],
            )
        )

    # Everything not currently counted: unclassified deposits (addable) and
    # explicitly excluded ones (restorable). Newest first, capped.
    counted = detected_ids | {t.id for t in manual}
    other = [
        _txn_item(t, excluded=t.id in excluded_ids)
        for t in sorted(transactions, key=lambda t: t.occurred_at, reverse=True)
        if t.id not in counted
    ][:_OTHER_INFLOWS_CAP]

    filing_status = household.tax_filing_status or DEFAULT_FILING_STATUS
    treated_as_net = (
        household.income_treated_as_net if household.income_treated_as_net is not None else True
    )
    return IncomeAnalysisResponse(
        sources=sources,
        other_inflows=other,
        rollup=IncomeRollup(
            annual_income=MoneySchema(
                amount_minor=total_minor, currency=household.base_currency
            ),
            monthly_average=MoneySchema(
                amount_minor=total_minor // 12, currency=household.base_currency
            ),
            transaction_count=count,
            window_days=ANALYSIS_WINDOW_DAYS,
        ),
        tax=_tax_estimate(
            total_minor, household.base_currency, filing_status, treated_as_net
        ),
    )


@router.post(
    "/income/analysis/overrides",
    operation_id="setIncomeOverride",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Transaction not found", "model": ErrorResponse},
    },
    summary="Include, exclude, or reset a deposit in the income analysis",
)
async def set_income_override(
    payload: IncomeOverrideRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    if not repository.set_income_override(
        engine, session.household_id, payload.transaction_id, payload.verdict
    ):
        raise HTTPException(status_code=404, detail="Transaction not found")
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "income_override.set",
        "transaction",
        payload.transaction_id,
        f"Income analysis override: {payload.verdict}",
    )
    return Response(status_code=204)


@router.put(
    "/income/analysis/settings",
    operation_id="updateIncomeTaxSettings",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Set the tax-estimate filing status and net/gross treatment",
)
async def update_income_tax_settings(
    payload: IncomeTaxSettingsRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    if payload.tax_filing_status not in FILING_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown filing status")
    repository.update_tax_settings(
        engine,
        session.household_id,
        tax_filing_status=payload.tax_filing_status,
        income_treated_as_net=payload.income_treated_as_net,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "income_tax_settings.updated",
        "household",
        session.household_id,
        f"Tax settings: {payload.tax_filing_status}, net={payload.income_treated_as_net}",
    )
    return Response(status_code=204)

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

from family_cfo_api import audit, finance_service, income_detection, repository, rights, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_right
from family_cfo_api.finance_service import add_months
from family_cfo_api.schemas import (
    ErrorResponse,
    ExpectedIncomeEvent,
    IncomeAnalysisResponse,
    IncomeAnalysisTransaction,
    IncomeEarner,
    IncomeEarnerCreateRequest,
    IncomeOverrideRequest,
    IncomeProfile,
    IncomeRollup,
    IncomeSourceAnalysis,
    IncomeTaxSettingsRequest,
    TaxEstimate,
    W2ScanRequest,
    W2ScanResult,
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
        merchant=txn.merchant,
        description=txn.description,
        account_name=txn.account_name,
        institution=txn.institution,
        excluded=excluded,
    )


# USPS codes accepted for the state setting (M65).
US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


def _tax_estimate(
    annual_income_minor: int,
    currency: str,
    filing_status: str,
    treated_as_net: bool,
    state: str | None = None,
) -> TaxEstimate:
    income = EngineMoney(annual_income_minor, currency)
    result = (
        gross_up_from_net(income, filing_status, state)
        if treated_as_net
        else estimate_annual_tax(income, filing_status, state)
    )
    outputs = result.outputs

    def money(key: str) -> MoneySchema:
        value = outputs[key]
        return MoneySchema(amount_minor=value.amount_minor, currency=value.currency)

    net = outputs.get("net_income")
    state_tax = outputs.get("state_income_tax")
    return TaxEstimate(
        tax_year=result.inputs["tax_year"],
        filing_status=filing_status,
        income_treated_as_net=treated_as_net,
        state=state,
        gross_income=money("gross_income"),
        net_income=(
            MoneySchema(amount_minor=net.amount_minor, currency=net.currency) if net else None
        ),
        standard_deduction=money("standard_deduction"),
        taxable_income=money("taxable_income"),
        federal_income_tax=money("federal_income_tax"),
        fica_tax=money("fica_tax"),
        state_income_tax=(
            MoneySchema(amount_minor=state_tax.amount_minor, currency=state_tax.currency)
            if state_tax is not None
            else None
        ),
        total_tax=money("total_tax"),
        effective_rate=float(outputs["effective_rate"]),
        assumptions=list(result.assumptions),
    )


# RSU vests per year by cadence (M73).
_VESTS_PER_YEAR = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}


def _earner_expected_gross_minor(record: repository.IncomeProfileRecord) -> int:
    bonus = int(record.base_salary_minor * record.bonus_percent / 100)
    return record.base_salary_minor + record.rsu_annual_minor + bonus


def _earner_events(
    record: repository.IncomeProfileRecord, currency: str, *, today: date
) -> list[ExpectedIncomeEvent]:
    events: list[ExpectedIncomeEvent] = []
    vests = _VESTS_PER_YEAR.get(record.rsu_frequency or "")
    if vests and record.rsu_annual_minor > 0 and record.rsu_next_vest_date:
        step_months = 12 // vests
        vest_date = record.rsu_next_vest_date
        while vest_date < today:
            vest_date = add_months(vest_date, step_months)
        per_vest = record.rsu_annual_minor // vests
        for i in range(2):
            events.append(
                ExpectedIncomeEvent(
                    date=add_months(vest_date, step_months * i),
                    label=f"{record.label} — RSU vest",
                    amount=MoneySchema(amount_minor=per_vest, currency=currency),
                )
            )
    bonus_minor = int(record.base_salary_minor * record.bonus_percent / 100)
    if record.bonus_month and bonus_minor > 0:
        year = today.year if record.bonus_month >= today.month else today.year + 1
        events.append(
            ExpectedIncomeEvent(
                date=date(year, record.bonus_month, 15),
                label=f"{record.label} — annual bonus (~{record.bonus_percent:g}% of base)",
                amount=MoneySchema(amount_minor=bonus_minor, currency=currency),
            )
        )
    return events


def _earner_schema(record: repository.IncomeProfileRecord, currency: str) -> IncomeEarner:
    def money(minor: int | None) -> MoneySchema | None:
        return MoneySchema(amount_minor=minor, currency=currency) if minor is not None else None

    return IncomeEarner(
        id=record.id,
        label=record.label,
        base_salary=MoneySchema(amount_minor=record.base_salary_minor, currency=currency),
        rsu_annual=MoneySchema(amount_minor=record.rsu_annual_minor, currency=currency),
        rsu_frequency=record.rsu_frequency,
        rsu_next_vest_date=record.rsu_next_vest_date,
        bonus_percent=record.bonus_percent,
        bonus_month=record.bonus_month,
        w2_year=record.w2_year,
        w2_wages=money(record.w2_wages_minor),
        w2_withheld=money(record.w2_withheld_minor),
    )


def _profile_block(
    engine: Engine, household: repository.HouseholdRecord
) -> IncomeProfile | None:
    records = repository.list_income_profiles(engine, household.id)
    if not records:
        return None
    currency = household.base_currency
    today = date.today()
    events: list[ExpectedIncomeEvent] = []
    for record in records:
        events.extend(_earner_events(record, currency, today=today))
    events.sort(key=lambda e: e.date)
    total = sum(_earner_expected_gross_minor(r) for r in records)
    return IncomeProfile(
        earners=[_earner_schema(r, currency) for r in records],
        expected_annual_gross=MoneySchema(amount_minor=total, currency=currency),
        expected_events=events[:4],
    )


def _profile_assumptions(records: list[repository.IncomeProfileRecord]) -> list[str]:
    lines = [
        "Gross income comes from your DECLARED compensation profile "
        "(base + RSU value + bonus), not from deposit inference."
    ]
    for r in records:
        parts = [f"{r.label}: base {r.base_salary_minor / 100:,.0f}"]
        if r.rsu_annual_minor:
            parts.append(
                f"RSU {r.rsu_annual_minor / 100:,.0f}/yr"
                + (f" vesting {r.rsu_frequency}" if r.rsu_frequency else "")
            )
        if r.bonus_percent:
            parts.append(f"bonus {r.bonus_percent:g}% of base")
        lines.append("; ".join(parts) + ".")
        if r.w2_wages_minor and r.w2_withheld_minor:
            rate = r.w2_withheld_minor / r.w2_wages_minor
            lines.append(
                f"{r.label}'s {r.w2_year or 'last-year'} W2: wages "
                f"{r.w2_wages_minor / 100:,.0f} with {rate:.1%} federal withholding — "
                "compare against this estimate's effective rate."
            )
    lines.append(
        "RSU values assume the declared annual value; actual vests move with "
        "the stock price."
    )
    lines.append(
        "All declared amounts are PRE-TAX. RSU taxes are typically withheld "
        "at vest (shares sold to cover), so brokerage deposits arrive smaller "
        "than the vest value shown."
    )
    return lines


def build_income_analysis(
    engine: Engine, household: repository.HouseholdRecord
) -> IncomeAnalysisResponse:
    """The full M61–M63 income analysis; shared by the endpoint and the chat tool (M64)."""
    since = date.today() - timedelta(days=ANALYSIS_WINDOW_DAYS)
    # M112: detection (transfer exclusion + overrides + grouping) is shared with
    # the cash outlook, so both features see the same income sources.
    transactions, candidates, included_ids, excluded_ids = (
        finance_service.recurring_income_candidates(engine, household.id, since=since)
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

    # M63: disclose when the synced history does not span the full window —
    # a mid-year start understates both income and the tax on it. Uses the raw
    # inflow rows (pre transfer-exclusion): coverage is about how far back the
    # SYNCED history goes, not how much of it counted as income.
    raw_rows = repository.list_income_detection_transactions(
        engine, household.id, since=since
    )
    coverage_start = raw_rows[0][1] if raw_rows else None
    coverage_days = (date.today() - coverage_start).days if coverage_start else 0
    coverage_warning: str | None = None
    if coverage_start is None:
        coverage_warning = (
            "No checking-account transaction history is available yet; income "
            "and the tax estimate will be meaningful after the first bank sync."
        )
    elif coverage_days < ANALYSIS_WINDOW_DAYS - 7:
        coverage_warning = (
            f"Synced history only starts {coverage_start.strftime('%b %-d, %Y')} "
            f"({coverage_days} days) — not a full year of data. Income and the "
            "tax estimate are likely underestimated."
        )

    filing_status = household.tax_filing_status or DEFAULT_FILING_STATUS
    treated_as_net = (
        household.income_treated_as_net if household.income_treated_as_net is not None else True
    )

    # M73: a declared compensation profile is the authority on gross income —
    # no net→gross guessing, which structurally misreads RSU-heavy pay.
    profile = _profile_block(engine, household)
    if profile is not None:
        tax = _tax_estimate(
            profile.expected_annual_gross.amount_minor,
            household.base_currency,
            filing_status,
            False,  # declared amounts ARE gross
            household.state,
        )
        records = repository.list_income_profiles(engine, household.id)
        tax = tax.model_copy(
            update={"assumptions": [*_profile_assumptions(records), *tax.assumptions]}
        )
        coverage_warning = None  # the estimate no longer depends on deposit coverage
    else:
        tax = _tax_estimate(
            total_minor,
            household.base_currency,
            filing_status,
            treated_as_net,
            household.state,
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
            coverage_start=coverage_start,
            coverage_days=coverage_days,
        ),
        coverage_warning=coverage_warning,
        profile=profile,
        tax=tax,
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
    return build_income_analysis(engine, household)


@router.post(
    "/income/profile/earners",
    operation_id="createIncomeEarner",
    response_model=IncomeEarner,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Declare an earner's compensation (base, RSU, bonus, W2 actuals)",
)
async def create_income_earner(
    payload: IncomeEarnerCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> IncomeEarner:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")
    if payload.rsu_frequency is not None and payload.rsu_frequency not in _VESTS_PER_YEAR:
        raise HTTPException(status_code=422, detail="Unknown RSU vesting frequency")
    profile_id = repository.create_income_profile(
        engine,
        session.household_id,
        label=payload.label.strip(),
        base_salary_minor=payload.base_salary_minor,
        rsu_annual_minor=payload.rsu_annual_minor,
        rsu_frequency=payload.rsu_frequency,
        rsu_next_vest_date=payload.rsu_next_vest_date,
        bonus_percent=payload.bonus_percent,
        bonus_month=payload.bonus_month,
        w2_year=payload.w2_year,
        w2_wages_minor=payload.w2_wages_minor,
        w2_withheld_minor=payload.w2_withheld_minor,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "income_profile.created",
        "income_profile",
        profile_id,
        f"Declared compensation for '{payload.label.strip()}'",
        undo_token=undo_actions.created("income_profile", profile_id),
    )
    records = [
        r for r in repository.list_income_profiles(engine, session.household_id)
        if r.id == profile_id
    ]
    return _earner_schema(records[0], household.base_currency)


@router.delete(
    "/income/profile/earners/{earner_id}",
    operation_id="deleteIncomeEarner",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Earner not found", "model": ErrorResponse},
    },
    summary="Remove an earner's declared compensation",
)
async def delete_income_earner(
    earner_id: str,
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    before = next(
        (r for r in repository.list_income_profiles(engine, session.household_id)
         if r.id == earner_id),
        None,
    )
    if before is None or not repository.delete_income_profile(
        engine, session.household_id, earner_id
    ):
        raise HTTPException(status_code=404, detail="Earner not found")
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "income_profile.deleted",
        "income_profile",
        earner_id,
        f"Removed declared compensation for '{before.label}'",
        undo_token=undo_actions.income_profile_deleted(before),
    )
    return Response(status_code=204)


_W2_PROMPT = (
    "This image is a US W-2 tax form. Extract ONLY a JSON object, no prose: "
    '{"year": tax year integer or null, "employer": employer name string or '
    'null, "wages": Box 1 wages as a number or null, "federal_withheld": '
    'Box 2 federal income tax withheld as a number or null}. Use null for '
    "anything unreadable."
)


def parse_w2_scan(text: str) -> W2ScanResult:
    """Defensive parse of the vision model's W2 extraction (candidates only)."""
    import json as _json
    import re as _re

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = _re.sub(r"^```[a-z]*\s*|\s*```$", "", cleaned, flags=_re.IGNORECASE)
    try:
        data = _json.loads(cleaned)
        assert isinstance(data, dict)
    except (ValueError, AssertionError):
        return W2ScanResult(note="The photo could not be read as a W-2 — enter values manually.")

    def money_minor(key: str) -> int | None:
        value = data.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(round(float(value) * 100))
        return None

    year = data.get("year")
    return W2ScanResult(
        year=int(year) if isinstance(year, int) and 1990 < year < 2100 else None,
        employer=str(data["employer"])[:120] if data.get("employer") else None,
        wages_minor=money_minor("wages"),
        federal_withheld_minor=money_minor("federal_withheld"),
        note=(
            "Read by the on-box photo model — CONFIRM every value against the "
            "paper form before saving. Nothing is stored until you save."
        ),
    )


# M78: some providers put a cover sheet or instructions before the actual W-2;
# scanning is capped so a huge PDF can't hold the vision model for minutes.
_W2_PDF_MAX_PAGES = 4


def pdf_page_pngs(pdf_bytes: bytes, max_pages: int = _W2_PDF_MAX_PAGES) -> list[bytes]:
    """M77: rasterize pages on-box — the vision model reads pixels, not PDF bytes."""
    import io

    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(pdf_bytes)
        try:
            pages = []
            for index in range(min(len(document), max_pages)):
                image = document[index].render(scale=2.0).to_pil()
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                pages.append(buffer.getvalue())
        finally:
            document.close()
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail="The PDF could not be read (encrypted or empty?) — try a photo instead.",
        ) from exc
    if not pages:
        raise HTTPException(
            status_code=422,
            detail="The PDF has no pages — try a photo instead.",
        )
    return pages


@router.post(
    "/income/profile/scan-w2",
    operation_id="scanW2",
    response_model=W2ScanResult,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        422: {"description": "Unreadable PDF", "model": ErrorResponse},
        503: {"description": "No vision model available", "model": ErrorResponse},
    },
    summary="Read a W-2 photo or PDF into candidate values (user confirms before saving)",
)
async def scan_w2(
    payload: W2ScanRequest,
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> W2ScanResult:
    import base64
    import binascii

    from family_cfo_ai_orchestrator import RuntimeMessage, RuntimeUnavailableError

    from family_cfo_api.ai_runtime_selection import select_vision_describer

    # PDF handling first: a 422 here must not leak an un-closed describer.
    if payload.image_media_type == "application/pdf":
        try:
            pdf_bytes = base64.b64decode(payload.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid PDF upload") from exc
        data_urls = [
            "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            for png in pdf_page_pngs(pdf_bytes)
        ]
    else:
        data_urls = [f"data:{payload.image_media_type};base64,{payload.image_base64}"]

    describer, source = select_vision_describer(engine, session.household_id)
    if describer is None:
        raise HTTPException(status_code=503, detail="No vision model is configured")
    try:
        # M78: try each page until one reads as a W-2 (cover sheets and
        # instruction pages parse to all-null and are skipped).
        result = None
        for page_index, data_url in enumerate(data_urls):
            completion = describer.complete(
                [RuntimeMessage(role="user", content=_W2_PROMPT, image_data_url=data_url)],
                temperature=0.0,
                max_tokens=200,
            )
            result = parse_w2_scan(completion.text)
            if result.wages_minor is not None or result.federal_withheld_minor is not None:
                if page_index > 0:
                    result.note = f"Read from page {page_index + 1} of the PDF. {result.note}"
                return result
    except RuntimeUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Vision model unavailable") from exc
    finally:
        describer.close()
    del source  # attribution not persisted; the scan stores nothing
    if result is None:  # unreachable: there is always at least one image
        raise HTTPException(status_code=422, detail="Nothing to scan")
    if len(data_urls) > 1:
        result.note = (
            f"No W-2 amounts found on the first {len(data_urls)} pages of the "
            "PDF — enter values manually."
        )
    return result


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
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    previous = repository.list_income_overrides(engine, session.household_id).get(
        payload.transaction_id
    )
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
        undo_token=undo_actions.income_override_set(payload.transaction_id, previous),
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
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    if payload.tax_filing_status not in FILING_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown filing status")
    state = payload.state.upper() if payload.state else None
    if state is not None and state not in US_STATES:
        raise HTTPException(status_code=422, detail="Unknown state code")
    before = repository.get_household(engine, session.household_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Household not found")
    repository.update_tax_settings(
        engine,
        session.household_id,
        tax_filing_status=payload.tax_filing_status,
        income_treated_as_net=payload.income_treated_as_net,
        state=state,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "income_tax_settings.updated",
        "household",
        session.household_id,
        f"Tax settings: {payload.tax_filing_status}, net={payload.income_treated_as_net}, "
        f"state={state or 'unset'}",
        undo_token=undo_actions.tax_settings_updated(before),
    )
    return Response(status_code=204)

"""M38: enriched household context (emergency-fund target, cash flow, assets, debt)."""

import pytest


async def _context(demo_client, demo_token):
    response = await demo_client.get(
        "/api/v1/household", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.anyio
async def test_context_includes_enriched_summary(demo_client, demo_token) -> None:
    body = await _context(demo_client, demo_token)

    # Cash flow from the demo fixtures: $6,000 income − ($2,000 + $80) bills.
    cash_flow = body["monthly_cash_flow"]
    assert cash_flow["income"]["amount_minor"] == 600_000
    assert cash_flow["bills"]["amount_minor"] == 208_000
    assert cash_flow["net"]["amount_minor"] == 392_000

    # Emergency fund: no designations in fixtures → all-liquid fallback,
    # and the reported months must equal reserved / monthly expenses.
    fund = body["emergency_fund"]
    assert fund["using_designations"] is False
    assert fund["monthly_expenses"]["amount_minor"] == 208_000
    assert fund["target_months_min"] == 3
    assert fund["target_months_recommended"] == 6
    assert fund["months"] == pytest.approx(fund["reserved"]["amount_minor"] / 208_000)
    assert fund["gap_to_recommended"]["amount_minor"] == max(
        0, 6 * 208_000 - fund["reserved"]["amount_minor"]
    )
    assert fund["status"] in {"no_fund", "getting_started", "on_track", "fully_funded"}

    # Assets are grouped in spendability order; debt is a positive total.
    categories = [entry["category"] for entry in body["asset_breakdown"]]
    order = ["liquid", "investments", "retirement", "education", "property"]
    assert categories == [c for c in order if c in categories]
    assert body["total_debt"]["amount_minor"] >= 0

    # M93: safe-to-spend on the Overview — liquid minus every obligation, the
    # same figure the advisor now quotes, so the two can never disagree.
    sts = body["safe_to_spend"]
    liquid = sts["liquid_balance"]["amount_minor"]
    assert liquid == 2_000_000  # checking 500_000 + savings 1_500_000
    assert sts["bills_due"]["amount_minor"] > 0  # demo household has bills
    committed = (
        sts["emergency_fund_reserved"]["amount_minor"]
        + sts["bills_due"]["amount_minor"]
        + sts["minimum_debt_payments"]["amount_minor"]
    )
    assert sts["committed_total"]["amount_minor"] == committed
    assert sts["safe_to_spend"]["amount_minor"] == liquid - committed
    # Not the old, obligation-blind figure (liquid − emergency fund only).
    assert sts["safe_to_spend"]["amount_minor"] != liquid - sts["emergency_fund_reserved"]["amount_minor"]


@pytest.mark.anyio
async def test_designation_drives_status_and_gap(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    # A dedicated savings account holding exactly one month of expenses.
    account_id = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "EF Savings", "type": "savings", "currency": "USD"},
        )
    ).json()["id"]
    await demo_client.post(
        f"/api/v1/accounts/{account_id}/balances",
        headers=headers,
        json={"balance": {"amount_minor": 208_000, "currency": "USD"}},
    )
    await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"emergency_fund_amount": {"amount_minor": 208_000, "currency": "USD"}},
    )

    fund = (await _context(demo_client, demo_token))["emergency_fund"]
    assert fund["using_designations"] is True
    assert fund["reserved"]["amount_minor"] == 208_000
    assert fund["months"] == pytest.approx(1.0)
    assert fund["status"] == "getting_started"
    # M75: the gap is the LARGER of the months gap (5 * 208,000) and the gap
    # to the fixture's $18k emergency-fund goal (1,800,000 - 208,000).
    assert fund["gap_to_recommended"]["amount_minor"] == 1_800_000 - 208_000


@pytest.mark.anyio
async def test_fully_funded_has_zero_gap(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    account_id = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "Big EF", "type": "savings", "currency": "USD"},
        )
    ).json()["id"]
    await demo_client.post(
        f"/api/v1/accounts/{account_id}/balances",
        headers=headers,
        json={"balance": {"amount_minor": 2_000_000, "currency": "USD"}},
    )
    await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"emergency_fund_percent": 100},
    )

    fund = (await _context(demo_client, demo_token))["emergency_fund"]
    assert fund["status"] == "fully_funded"
    assert fund["gap_to_recommended"]["amount_minor"] == 0
    assert fund["months"] >= 6


@pytest.mark.anyio
async def test_no_bills_falls_back_to_the_goal_view(demo_client, demo_token) -> None:
    """M75: with no bills, the fixture's emergency-fund goal still gives a status."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    bills = (await demo_client.get("/api/v1/bills", headers=headers)).json()["bills"]
    for bill in bills:
        deleted = await demo_client.delete(f"/api/v1/bills/{bill['id']}", headers=headers)
        assert deleted.status_code == 204

    body = await _context(demo_client, demo_token)
    fund = body["emergency_fund"]
    # Months can't be computed, but the $18k goal can be measured against.
    assert fund["months"] is None
    assert body["emergency_fund_months"] is None
    assert fund["goal_target"]["amount_minor"] == 1_800_000
    assert fund["status"] in ("getting_started", "on_track", "fully_funded")


# --- M75: goal-aware emergency-fund status ---


def _ef_goal(engine, target_minor: int) -> None:
    from family_cfo_api import fixtures, repository

    repository.create_goal(
        engine, fixtures.DEMO_HOUSEHOLD_ID, "Emergency fund", "emergency_fund",
        target_minor, "USD", None, 1,
    )


async def _context(client, token):
    resp = await client.get(
        "/api/v1/household", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.anyio
async def test_ef_goal_overrides_rosy_months_status(
    demo_client, demo_engine, demo_token
) -> None:
    """$1.1k against a $90k goal must NOT read fully funded, however few bills exist."""
    from family_cfo_api import fixtures, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    savings = repository.create_account(engine=demo_engine, household_id=hh,
                                        name="EF Savings", account_type="savings",
                                        currency="USD")
    repository.record_account_balance(demo_engine, savings.id, 115_411)
    repository.update_account(demo_engine, hh, savings.id, emergency_fund_percent=100.0)
    repository.create_bill(demo_engine, hh, "Netflix", 1_549, "USD", "monthly")
    _ef_goal(demo_engine, 9_000_000)

    fund = (await _context(demo_client, demo_token))["emergency_fund"]

    # Months coverage alone (~74 months of a $15.49 bill) said fully_funded.
    assert fund["status"] == "getting_started"
    assert fund["goal_target"]["amount_minor"] == 9_000_000
    assert fund["gap_to_recommended"]["amount_minor"] == 9_000_000 - 115_411


@pytest.mark.anyio
async def test_ef_goal_rescues_the_no_bills_case(
    demo_client, demo_engine, demo_token
) -> None:
    from family_cfo_api import fixtures, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    savings = repository.create_account(engine=demo_engine, household_id=hh,
                                        name="EF Savings", account_type="savings",
                                        currency="USD")
    repository.record_account_balance(demo_engine, savings.id, 6_000_000)
    repository.update_account(demo_engine, hh, savings.id, emergency_fund_percent=100.0)
    _ef_goal(demo_engine, 9_000_000)

    fund = (await _context(demo_client, demo_token))["emergency_fund"]

    # 6k/9k = 66% -> on_track, instead of the old "no_bills" shrug.
    assert fund["status"] == "on_track"


@pytest.mark.anyio
async def test_no_goal_keeps_months_based_behavior(
    demo_client, demo_engine, demo_token
) -> None:
    from family_cfo_api import fixtures, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    savings = repository.create_account(engine=demo_engine, household_id=hh,
                                        name="EF Savings", account_type="savings",
                                        currency="USD")
    # A fund comfortably above 6x the fixture's total monthly bills.
    repository.record_account_balance(demo_engine, savings.id, 10_000_000)
    repository.update_account(demo_engine, hh, savings.id, emergency_fund_percent=100.0)
    # Remove the fixture's seeded emergency-fund goal: this test is the
    # no-goal baseline.
    from sqlalchemy import delete
    from family_cfo_api import models
    with demo_engine.begin() as conn:
        conn.execute(delete(models.goals).where(models.goals.c.household_id == hh))

    fund = (await _context(demo_client, demo_token))["emergency_fund"]

    assert fund["status"] == "fully_funded"  # 6 months of $2k bills
    assert fund["goal_target"] is None


@pytest.mark.anyio
async def test_context_reports_spending_by_category(demo_client, demo_token) -> None:
    """M94: the payoff of categorizing — this month's spend grouped by category,
    with the still-uncategorized amount so the user sees the value of filing more."""
    body = await _context(demo_client, demo_token)

    sbc = body.get("spending_by_category")
    # The demo fixtures may have no current-month spend; when present, it must be
    # internally consistent.
    if sbc is None:
        return
    assert "month_label" in sbc
    cat_sum = sum(c["amount"]["amount_minor"] for c in sbc["categories"])
    assert sbc["categorized_total"]["amount_minor"] == cat_sum
    # Highest-first ordering.
    amounts = [c["amount"]["amount_minor"] for c in sbc["categories"]]
    assert amounts == sorted(amounts, reverse=True)
    assert sbc["uncategorized"]["amount_minor"] >= 0


@pytest.mark.anyio
async def test_emergency_fund_goal_tracks_the_reserved_fund(
    demo_client, demo_engine, demo_token
) -> None:
    """M41 fix: an emergency-fund goal must show the household's actual reserved
    fund as its progress, not a stale stored current of $0."""
    from family_cfo_api import fixtures, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    # A savings account holding $1,500, all designated as emergency fund.
    acct = repository.create_account(
        engine=demo_engine, household_id=hh, name="EF", account_type="savings", currency="USD")
    repository.record_account_balance(demo_engine, acct.id, 150_000)
    repository.update_account(demo_engine, hh, acct.id, emergency_fund_percent=100.0)
    # An emergency-fund goal with current stored as 0.
    repository.create_goal(
        demo_engine, hh, "6mo EF", "emergency_fund", 9_000_000, "USD", None, 1)

    body = await _context(demo_client, demo_token)
    goal = body["top_goal"]
    assert goal["type"] == "emergency_fund"
    # Progress reflects the reserved fund ($1,500), not the stored $0.
    assert goal["current"]["amount_minor"] == 150_000
    assert goal["percent_complete"] == round(150_000 / 9_000_000 * 100)

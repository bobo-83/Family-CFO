"""M44: savings-rate metric (recurring income vs trailing-3-month actual spending)."""

from datetime import date

import pytest

from family_cfo_api import fixtures, repository
from family_cfo_api.api.household import _savings_rate

_HH = fixtures.DEMO_HOUSEHOLD_ID


def _account(demo_engine) -> str:
    return repository.list_account_balances(demo_engine, _HH)[0].account_id


def _spend(demo_engine, account_id, occurred, amount_minor) -> None:
    repository.create_transaction(
        demo_engine,
        household_id=_HH,
        account_id=account_id,
        occurred_at=occurred,
        amount_minor=amount_minor,
        currency="USD",
        merchant="Store",
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )


def test_window_is_three_complete_months_excluding_current(demo_engine) -> None:
    account_id = _account(demo_engine)
    # Today = Apr 20 2027 -> window Jan/Feb/Mar 2027.
    _spend(demo_engine, account_id, date(2027, 1, 10), -30_000)
    _spend(demo_engine, account_id, date(2027, 2, 10), -30_000)
    _spend(demo_engine, account_id, date(2027, 3, 10), -30_000)
    # April (current month) spend must be excluded from the trailing window.
    _spend(demo_engine, account_id, date(2027, 4, 5), -99_000)
    # December (before the window) must be excluded too.
    _spend(demo_engine, account_id, date(2026, 12, 15), -99_000)

    rate = _savings_rate(demo_engine, _HH, "USD", today=date(2027, 4, 20))
    # 90,000 over 3 months -> 30,000/month average.
    assert rate.average_monthly_spending.amount_minor == 30_000
    # Demo recurring income is $6,000/month -> (600000-30000)/600000 = 95%.
    assert rate.monthly_income.amount_minor == 600_000
    assert rate.percent == 95


def test_negative_rate_when_spending_exceeds_income(demo_engine) -> None:
    account_id = _account(demo_engine)
    # 3 months at $9,000/month spending vs $6,000 income -> negative rate.
    for month in (1, 2, 3):
        _spend(demo_engine, account_id, date(2027, month, 12), -900_000)
    rate = _savings_rate(demo_engine, _HH, "USD", today=date(2027, 4, 20))
    assert rate.average_monthly_spending.amount_minor == 900_000
    assert rate.percent == -50  # (600000-900000)/600000


@pytest.mark.anyio
async def test_context_exposes_savings_rate(demo_client, demo_token) -> None:
    body = (
        await demo_client.get(
            "/api/v1/household", headers={"Authorization": f"Bearer {demo_token}"}
        )
    ).json()
    assert "savings_rate" in body
    assert "monthly_income" in body["savings_rate"]


def _add_earner_with_deductions(demo_engine, retirement_minor, hsa_minor) -> None:
    repository.create_income_profile(
        demo_engine, _HH, label="Earner",
        base_salary_minor=20_000_000,
        retirement_contribution_annual_minor=retirement_minor,
        hsa_contribution_annual_minor=hsa_minor,
    )


def test_payroll_deductions_count_and_do_not_touch_the_residual(demo_engine) -> None:
    """#6: 401(k)/HSA are saved but never in take-home, so they add to total
    saved without changing the residual (no double count)."""
    account_id = _account(demo_engine)
    for month in (1, 2, 3):
        _spend(demo_engine, account_id, date(2027, month, 10), -30_000)
    # $24k/yr 401(k) + $6k/yr HSA = $2,500/mo payroll saving.
    _add_earner_with_deductions(demo_engine, 24_000_00, 6_000_00)

    rate = _savings_rate(demo_engine, _HH, "USD", today=date(2027, 4, 20))
    assert rate.payroll_deductions.amount_minor == 2_500_00
    assert rate.payroll_profile_present is True
    # Residual is unchanged by payroll: take-home 600000 - spending 30000 = 570000.
    assert rate.residual.amount_minor == 570_000
    # gross = take-home 600000 + payroll 250000 = 850000; saved = 250000+570000.
    assert rate.gross_income.amount_minor == 850_000
    assert rate.total_saved.amount_minor == 820_000
    assert rate.percent == round(820_000 / 850_000 * 100)


@pytest.mark.anyio
async def test_declared_transfer_is_its_own_bucket_not_double_counted(
    demo_client, demo_token
) -> None:
    """#6: a declared 529 transfer is pulled out of the residual into transfers,
    so it is counted once."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    checking = (await demo_client.post(
        "/api/v1/accounts", headers=headers,
        json={"name": "Checking", "type": "checking", "currency": "USD"},
    )).json()["id"]
    college = (await demo_client.post(
        "/api/v1/accounts", headers=headers,
        json={"name": "529", "type": "529", "currency": "USD"},
    )).json()["id"]
    await demo_client.post(
        "/api/v1/savings/contributions", headers=headers,
        json={
            "source_account_id": checking, "destination_account_id": college,
            "amount": {"amount_minor": 500_00, "currency": "USD"}, "frequency": "monthly",
        },
    )
    body = (await demo_client.get("/api/v1/household", headers=headers)).json()
    sr = body["savings_rate"]
    assert sr["transfers"]["amount_minor"] == 500_00
    assert sr["declared_transfers_present"] is True
    # residual = take_home - spending - transfers: the $500 is not also here.
    assert (
        sr["residual"]["amount_minor"]
        == sr["monthly_income"]["amount_minor"]
        - sr["average_monthly_spending"]["amount_minor"]
        - 500_00
    )

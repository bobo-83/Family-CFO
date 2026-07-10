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

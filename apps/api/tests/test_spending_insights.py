"""M42: month-to-date spending insights vs the same period last month."""

from datetime import date

import pytest

from family_cfo_api import fixtures, repository
from family_cfo_api.api.household import _spending_insights

_HH = fixtures.DEMO_HOUSEHOLD_ID


def _account(demo_engine) -> str:
    return repository.list_account_balances(demo_engine, _HH)[0].account_id


def _spend(demo_engine, account_id, occurred, amount_minor, merchant) -> None:
    repository.create_transaction(
        demo_engine,
        household_id=_HH,
        account_id=account_id,
        occurred_at=occurred,
        amount_minor=amount_minor,
        currency="USD",
        merchant=merchant,
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )


def test_sum_spending_excludes_income_and_sums_outflows(demo_engine) -> None:
    account_id = _account(demo_engine)
    day = date(2026, 6, 10)
    _spend(demo_engine, account_id, day, -5_000, "Coffee")
    _spend(demo_engine, account_id, day, -2_500, "Books")
    _spend(demo_engine, account_id, day, 900_000, "Salary")  # income, must be ignored

    total = repository.sum_spending(demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD")
    assert total == 7_500


def test_top_merchants_ranked_with_null_folded_to_other(demo_engine) -> None:
    account_id = _account(demo_engine)
    day = date(2026, 6, 12)
    _spend(demo_engine, account_id, day, -10_000, "Whole Foods")
    _spend(demo_engine, account_id, day, -4_000, "Whole Foods")
    _spend(demo_engine, account_id, day, -6_000, None)  # -> "Other"

    top = repository.top_spending_merchants(
        demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD", limit=5
    )
    assert [(m.merchant, m.amount_minor) for m in top] == [
        ("Whole Foods", 14_000),
        ("Other", 6_000),
    ]


def test_month_to_date_compares_same_day_range(demo_engine) -> None:
    account_id = _account(demo_engine)
    # A fixture-free month keeps the window free of seeded demo spending.
    _spend(demo_engine, account_id, date(2027, 3, 3), -30_000, "A")
    _spend(demo_engine, account_id, date(2027, 2, 5), -10_000, "B")
    # A prior-month transaction AFTER the 10th must not count in the MTD window.
    _spend(demo_engine, account_id, date(2027, 2, 20), -99_000, "C")

    insights = _spending_insights(demo_engine, _HH, "USD", today=date(2027, 3, 10))
    assert insights.this_month.amount_minor == 30_000
    assert insights.last_month.amount_minor == 10_000
    assert insights.change_percent == 200  # (300-100)/100


def test_change_percent_is_null_when_last_month_zero(demo_engine) -> None:
    account_id = _account(demo_engine)
    _spend(demo_engine, account_id, date(2027, 3, 2), -5_000, "A")
    insights = _spending_insights(demo_engine, _HH, "USD", today=date(2027, 3, 9))
    assert insights.last_month.amount_minor == 0
    assert insights.change_percent is None


@pytest.mark.anyio
async def test_context_exposes_spending_insights(demo_client, demo_token) -> None:
    body = (
        await demo_client.get(
            "/api/v1/household", headers={"Authorization": f"Bearer {demo_token}"}
        )
    ).json()
    assert "spending_insights" in body
    assert "this_month" in body["spending_insights"]

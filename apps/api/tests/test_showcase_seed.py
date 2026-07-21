"""M74: the showcase seeder must exercise every scenario and stay idempotent.

Persona: a senior software engineer at Anthropic living in Austin, TX, with two
full years of history.
"""

from datetime import date, timedelta

from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, repository
from family_cfo_api.api.income_analysis import build_income_analysis


def test_showcase_seed_is_rich_and_idempotent(demo_engine: Engine) -> None:
    assert fixtures.seed_showcase_data(demo_engine) is True
    assert fixtures.seed_showcase_data(demo_engine) is False  # guarded re-run

    hh = fixtures.DEMO_HOUSEHOLD_ID
    household = repository.get_household(demo_engine, hh)
    analysis = build_income_analysis(demo_engine, household)

    # M65: the biweekly Anthropic payroll clusters into one source.
    paycheck = [s for s in analysis.sources if "ANTHROPIC" in s.name.upper()]
    assert paycheck and paycheck[0].frequency == "biweekly"
    # The trailing-12-month analysis window sees ~26 biweekly deposits with no
    # coverage warning; the RAW history goes back two full years (53 paychecks).
    assert len(paycheck[0].transactions) >= 24
    assert analysis.coverage_warning is None
    two_years_back = date.today() - timedelta(days=740)
    raw = repository.list_income_detection_transactions(demo_engine, hh, since=two_years_back)
    payroll_rows = [r for r in raw if "ANTHROPIC" in (r[4] or "").upper()]
    assert len(payroll_rows) >= 50
    assert min(r[1] for r in payroll_rows) <= date.today() - timedelta(days=700)
    # M63: matched checking<->savings transfer pairs are suppressed entirely.
    assert not any("Internal Transfer" in t.name for t in analysis.other_inflows)
    # M73: the declared profile drives the tax estimate (declared = gross).
    assert analysis.profile is not None
    assert analysis.profile.expected_annual_gross.amount_minor == 43_000_000
    assert analysis.tax.net_income is None
    # Austin, TX: no state income tax on wages.
    assert analysis.tax.state == "TX"
    assert (
        analysis.tax.state_income_tax is None
        or analysis.tax.state_income_tax.amount_minor == 0
    )
    assert any(e for e in analysis.profile.expected_events if "RSU vest" in e.label)

    # M58/M59: fresh suggestions (Austin merchants) and one drift update (stale
    # Netflix bill at 12.99 vs charges at 15.49).
    from family_cfo_api import bill_detection

    since = date.today() - timedelta(days=bill_detection.LOOKBACK_DAYS)
    rows = repository.list_bill_detection_transactions(demo_engine, hh, since=since)
    candidates = bill_detection.detect_bill_candidates(
        [bill_detection.DetectionTransaction(*r) for r in rows]
    )
    keys = {c.merchant_key for c in candidates}
    assert {"city of austin water", "spotify usa", "golds gym austin"} <= keys
    netflix = [c for c in candidates if c.merchant_key == "netflix com"]
    assert netflix and netflix[0].amount_minor == 1_549  # vs the 12.99 bill -> drift

    # M46: budget envelopes exist across categories; M41 goal has progress.
    budgets = repository.list_budgets(demo_engine, hh)
    assert len(budgets) >= 3
    goals = repository.list_goals(demo_engine, hh)
    assert any(g.current_minor > 0 for g in goals)
    # M57 memories and M40 history are present.
    assert len(repository.list_household_memories(demo_engine, hh)) >= 4


def test_reset_demo_data_wipes_and_allows_reseed(demo_engine: Engine) -> None:
    """A persona change rebuilds the demo from scratch: reset deletes the data
    (keeping identity — users, memberships, roles) and the seeder runs again."""
    assert fixtures.seed_showcase_data(demo_engine) is True

    deleted = fixtures.reset_demo_data(demo_engine)
    assert deleted > 0

    hh = fixtures.DEMO_HOUSEHOLD_ID
    # Data gone…
    assert repository.list_transactions(demo_engine, hh) == []
    assert repository.list_account_balances(demo_engine, hh) == []
    # …identity kept: the demo owner can still be resolved.
    assert repository.get_household(demo_engine, hh) is not None
    assert repository.get_user_by_email(demo_engine, fixtures.DEMO_USER_EMAIL) is not None

    # And the showcase seeds cleanly again.
    assert fixtures.seed_showcase_data(demo_engine) is True

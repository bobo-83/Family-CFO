"""M74: the showcase seeder must exercise every scenario and stay idempotent."""

from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, repository
from family_cfo_api.api.income_analysis import build_income_analysis


def test_showcase_seed_is_rich_and_idempotent(demo_engine: Engine) -> None:
    assert fixtures.seed_showcase_data(demo_engine) is True
    assert fixtures.seed_showcase_data(demo_engine) is False  # guarded re-run

    hh = fixtures.DEMO_HOUSEHOLD_ID
    household = repository.get_household(demo_engine, hh)
    analysis = build_income_analysis(demo_engine, household)

    # M65: the biweekly paycheck clusters out of the generic transfer label.
    paycheck = [s for s in analysis.sources if "Online Transfer" in s.name]
    assert paycheck and paycheck[0].frequency == "biweekly"
    # M63: matched checking<->savings transfer pairs are suppressed entirely.
    assert not any("Internal Transfer" in t.name for t in analysis.other_inflows)
    # M73: the declared profile drives the tax estimate (declared = gross).
    assert analysis.profile is not None
    assert analysis.profile.expected_annual_gross.amount_minor == 41_000_000
    assert analysis.tax.net_income is None
    assert analysis.tax.state == "CA"
    assert analysis.tax.state_income_tax is not None
    assert any(e for e in analysis.profile.expected_events if "RSU vest" in e.label)

    # M58/M59: three fresh suggestions and one drift update (stale Netflix).
    from family_cfo_api import bill_detection
    from datetime import date, timedelta

    since = date.today() - timedelta(days=bill_detection.LOOKBACK_DAYS)
    rows = repository.list_bill_detection_transactions(demo_engine, hh, since=since)
    candidates = bill_detection.detect_bill_candidates(
        [bill_detection.DetectionTransaction(*r) for r in rows]
    )
    keys = {c.merchant_key for c in candidates}
    assert {"pg e utility", "goldfish swim school", "spotify usa"} <= keys
    netflix = [c for c in candidates if c.merchant_key == "netflix com"]
    assert netflix and netflix[0].amount_minor == 1_549  # vs the 12.99 bill -> drift

    # M46: budget envelopes exist across categories; M41 goal has progress.
    budgets = repository.list_budgets(demo_engine, hh)
    assert len(budgets) >= 3
    goals = repository.list_goals(demo_engine, hh)
    assert any(g.current_minor > 0 for g in goals)
    # M57 memories and M40 history are present.
    assert len(repository.list_household_memories(demo_engine, hh)) >= 3

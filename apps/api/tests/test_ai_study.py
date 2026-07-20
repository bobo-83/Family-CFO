"""ADR 0040: idle-time study of the transaction history."""

from datetime import date

import pytest
from sqlalchemy.engine import Engine

from family_cfo_ai_orchestrator import RuntimeCompletion

from family_cfo_api import ai_study, fixtures, repository


class _StubStudyRuntime:
    """Returns one scripted insight payload per complete() call."""

    def __init__(self, texts: list[str]):
        self._texts = texts
        self.calls = 0

    def complete(self, messages, *, temperature=0.2, max_tokens=400):
        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1
        return RuntimeCompletion(text=text, model="stub", raw={})

    def close(self):
        pass


def _seed_month(engine: Engine, month: str, amount_minor: int = -50_000) -> None:
    account = repository.create_account(
        engine, fixtures.DEMO_HOUSEHOLD_ID, f"Study Checking {month}", "checking", "USD"
    )
    repository.create_transaction(
        engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=account.id,
        occurred_at=date(int(month[:4]), int(month[5:7]), 15),
        amount_minor=amount_minor,
        currency="USD",
        merchant="Study Mart",
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )


def test_complete_months_excludes_current_partial_month(demo_engine: Engine) -> None:
    _seed_month(demo_engine, "2026-03")
    months = ai_study.complete_months(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, today=date(2026, 6, 15)
    )
    assert months == ["2026-03", "2026-04", "2026-05"]


def test_complete_months_empty_without_transactions(demo_engine: Engine) -> None:
    # The minimal demo fixture has transactions; a fresh household has none.
    bootstrap = repository.create_household_with_owner(
        demo_engine, "Empty Family", "USD", "empty@example.com", "x", "Empty"
    )
    assert (
        ai_study.complete_months(demo_engine, bootstrap.household_id, today=date(2026, 6, 15))
        == []
    )


def test_digest_fingerprint_changes_when_the_month_changes(demo_engine: Engine) -> None:
    _seed_month(demo_engine, "2026-03")
    before = ai_study.digest_fingerprint(
        ai_study.build_month_digest(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD", "2026-03")
    )
    _seed_month(demo_engine, "2026-03", amount_minor=-9_900)
    after = ai_study.digest_fingerprint(
        ai_study.build_month_digest(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD", "2026-03")
    )
    assert before != after


def test_study_month_upserts_insights_and_records_coverage(demo_engine: Engine) -> None:
    _seed_month(demo_engine, "2026-03")
    runtime = _StubStudyRuntime(
        ['[{"key": "grocery_spending_pattern", "value": "Groceries run about $500 a month."}]']
    )

    count = ai_study.study_month(
        runtime, demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD", "2026-03", model="stub-model"
    )

    assert count == 1
    insights = repository.list_study_insights(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert [(m.key, m.source) for m in insights] == [("grocery_spending_pattern", "study")]
    rows = repository.list_study_months(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert [(r.month, r.insight_count, r.model) for r in rows] == [("2026-03", 1, "stub-model")]


def test_restudy_updates_insight_in_place_not_a_duplicate(demo_engine: Engine) -> None:
    _seed_month(demo_engine, "2026-03")
    ai_study.study_month(
        _StubStudyRuntime(['[{"key": "grocery_spending_pattern", "value": "About $500."}]']),
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        "USD",
        "2026-03",
    )
    ai_study.study_month(
        _StubStudyRuntime(['[{"key": "grocery_spending_pattern", "value": "About $650 now."}]']),
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        "USD",
        "2026-03",
    )

    insights = repository.list_study_insights(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert len(insights) == 1
    assert insights[0].value == "About $650 now."
    assert len(repository.list_study_months(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)) == 1


def test_next_month_prefers_newest_unstudied_then_stale(demo_engine: Engine) -> None:
    for month in ("2026-03", "2026-04"):
        _seed_month(demo_engine, month)
    hh = fixtures.DEMO_HOUSEHOLD_ID

    assert ai_study._next_month_to_study(demo_engine, hh, "USD", today=date(2026, 6, 15)) == "2026-05"

    stub = _StubStudyRuntime(["[]"])
    for month in ("2026-05", "2026-04", "2026-03"):
        ai_study.study_month(stub, demo_engine, hh, "USD", month)
    assert ai_study._next_month_to_study(demo_engine, hh, "USD", today=date(2026, 6, 15)) is None

    # New data in an already-studied month marks it stale for re-study.
    _seed_month(demo_engine, "2026-04", amount_minor=-7_700)
    assert ai_study._next_month_to_study(demo_engine, hh, "USD", today=date(2026, 6, 15)) == "2026-04"


def test_study_status_reports_coverage(demo_engine: Engine) -> None:
    for month in ("2026-03", "2026-04"):
        _seed_month(demo_engine, month)
    hh = fixtures.DEMO_HOUSEHOLD_ID
    ai_study.study_month(_StubStudyRuntime(["[]"]), demo_engine, hh, "USD", "2026-04")

    status = ai_study.study_status(demo_engine, hh, today=date(2026, 6, 15))

    assert status.total_months == 3  # Mar, Apr, May 2026
    assert status.studied_months == 1
    assert status.last_studied_at is not None
    # Default test settings have no usable runtime configured.
    assert status.runtime_usable is False


def test_run_study_tick_without_usable_runtime_is_a_noop(demo_engine: Engine) -> None:
    _seed_month(demo_engine, "2026-03")
    ai_study.run_study_tick(demo_engine)
    assert repository.list_study_months(demo_engine, fixtures.DEMO_HOUSEHOLD_ID) == []


@pytest.mark.anyio
async def test_get_ai_study_status_requires_authentication(demo_client) -> None:
    response = await demo_client.get("/api/v1/ai/study")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_get_ai_study_status_reports_coverage(demo_client, demo_token, demo_engine) -> None:
    ai_study.study_month(
        _StubStudyRuntime(['[{"key": "income_rhythm", "value": "Paychecks arrive monthly."}]']),
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        "USD",
        "2026-03",
    )

    response = await demo_client.get(
        "/api/v1/ai/study", headers={"Authorization": f"Bearer {demo_token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["studied_months"] >= 0
    assert body["total_months"] >= 0
    assert 0 <= body["coverage_percent"] <= 100
    assert body["runtime_usable"] is False
    assert [i["key"] for i in body["insights"]] == ["income_rhythm"]

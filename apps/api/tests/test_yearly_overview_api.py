import pytest

from family_cfo_api import repository
from family_cfo_api.yearly_review import YearMonth, _deterministic_review, _parse_review


@pytest.mark.anyio
async def test_yearly_overview_aggregates_months_and_totals(demo_client, demo_token) -> None:
    response = await demo_client.get(
        "/api/v1/overview/yearly", headers={"Authorization": f"Bearer {demo_token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["year"] == repository.utcnow().year
    assert body["months"], "the seeded demo has current-year transactions"
    first = body["months"][0]
    assert set(first) >= {"month", "income", "spending", "net"}
    total_net = sum(m["net"]["amount_minor"] for m in body["months"])
    assert body["total_net"]["amount_minor"] == total_net
    assert body["review"] is None  # not generated yet


@pytest.mark.anyio
async def test_generate_review_falls_back_deterministically_and_caches(
    demo_client, demo_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    generated = await demo_client.post("/api/v1/overview/yearly/review", headers=headers)
    assert generated.status_code == 200
    review = generated.json()
    # No runtime in tests -> deterministic narrative, correct and grounded.
    assert "months of" in review["summary"]
    assert review["model"] is None
    assert review["months_covered"] >= 1

    # The cache now serves it on the plain GET.
    overview = await demo_client.get("/api/v1/overview/yearly", headers=headers)
    assert overview.json()["review"]["summary"] == review["summary"]


@pytest.mark.anyio
async def test_yearly_overview_for_an_empty_year(demo_client, demo_token) -> None:
    response = await demo_client.get(
        "/api/v1/overview/yearly?year=1999", headers={"Authorization": f"Bearer {demo_token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["months"] == []
    assert body["total_income"]["amount_minor"] == 0


def test_deterministic_review_names_best_and_worst_months() -> None:
    months = [
        YearMonth("2026-01", 1_000_00, 400_00, 600_00, None),
        YearMonth("2026-02", 1_000_00, 1_200_00, -200_00, None),
    ]
    summary, suggestions = _deterministic_review(months, [("Groceries", 900_00)], "USD", 2026)
    assert "2026-01" in summary and "2026-02" in summary
    assert any("Groceries" in s for s in suggestions)
    # Net is positive overall (2000 in vs 1600 out) — no overspend warning.
    assert not any("outpaced" in s for s in suggestions)


def test_parse_review_splits_summary_and_suggestions() -> None:
    text = "A good year.\nSUGGESTIONS:\n- Trim subscriptions\n- Move cash to savings\nnot a bullet"
    summary, suggestions = _parse_review(text)
    assert summary == "A good year."
    assert suggestions == ["Trim subscriptions", "Move cash to savings"]

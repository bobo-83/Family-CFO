import logging

import pytest
from sqlalchemy import select

from family_cfo_api import fixtures, models


@pytest.mark.anyio
async def test_analyze_purchase_requires_authentication(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        json={"item": "a new laptop", "price": {"amount_minor": 150_000, "currency": "USD"}},
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_analyze_purchase_returns_grounded_recommendation(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"item": "a new laptop", "price": {"amount_minor": 150_000, "currency": "USD"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert len(body["calculation_refs"]) == 1
    assert body["calculation_refs"][0].startswith("financial_calculations:")
    assert any(impact["area"] == "net_worth" for impact in body["impacts"])
    assert 0 <= body["confidence"] <= 1
    assert "a new laptop" in body["answer"]


@pytest.mark.anyio
async def test_analyze_purchase_rejects_non_positive_price(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"item": "free stuff", "price": {"amount_minor": 0, "currency": "USD"}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"]


@pytest.mark.anyio
async def test_analyze_purchase_rejects_currency_mismatch(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"item": "import", "price": {"amount_minor": 100, "currency": "EUR"}},
    )

    assert response.status_code == 400


@pytest.mark.anyio
async def test_viewer_can_ask_purchase_advisor(demo_client, demo_viewer_token) -> None:
    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
        json={"item": "snacks", "price": {"amount_minor": 500, "currency": "USD"}},
    )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_purchase_advisor_never_logs_item_or_price(demo_client, demo_token, caplog) -> None:
    unique_item = "a-very-unique-item-name-xyz"

    with caplog.at_level(logging.DEBUG):
        response = await demo_client.post(
            "/api/v1/advisor/purchase",
            headers={"Authorization": f"Bearer {demo_token}"},
            json={
                "item": unique_item,
                "merchant": "Very Specific Store",
                "price": {"amount_minor": 424_242, "currency": "USD"},
            },
        )

    assert response.status_code == 200
    log_text = caplog.text
    assert unique_item not in log_text
    assert "Very Specific Store" not in log_text
    assert "424242" not in log_text
    assert "4,242.42" not in log_text


@pytest.mark.anyio
async def test_analyze_purchase_persists_scenario_and_recommendation(demo_engine, demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"item": "a new laptop", "price": {"amount_minor": 150_000, "currency": "USD"}},
    )
    assert response.status_code == 200

    with demo_engine.connect() as conn:
        scenario_rows = conn.execute(
            select(models.scenarios).where(models.scenarios.c.household_id == fixtures.DEMO_HOUSEHOLD_ID)
        ).mappings().all()
        recommendation_rows = conn.execute(
            select(models.recommendations).where(
                models.recommendations.c.household_id == fixtures.DEMO_HOUSEHOLD_ID
            )
        ).mappings().all()

    assert len(scenario_rows) == 1
    assert scenario_rows[0]["input_json"]["item"] == "a new laptop"
    assert len(recommendation_rows) == 1
    assert recommendation_rows[0]["scenario_id"] == scenario_rows[0]["id"]
    assert recommendation_rows[0]["explanation_source"] == "deterministic_stub"

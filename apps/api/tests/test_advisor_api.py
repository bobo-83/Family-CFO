import logging

import pytest
from family_cfo_ai_orchestrator import RuntimeCompletion
from sqlalchemy import select

from family_cfo_api import fixtures, models


class _StubVllmAdapter:
    def __init__(self, response_text: str, model: str = "stub-model") -> None:
        self._response_text = response_text
        self._model = model

    def __call__(self, _base_url: str, _model: str) -> "_StubVllmAdapter":
        return self

    def complete(
        self, _messages, *, temperature: float = 0.2, max_tokens: int = 400
    ) -> RuntimeCompletion:
        return RuntimeCompletion(text=self._response_text, model=self._model, raw={})

    def close(self) -> None:
        pass


async def _enable_runtime(demo_client, demo_token) -> None:
    response = await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "provider": "vllm",
            "base_url": "http://vllm.local:8000",
            "model": "stub-model",
            "enabled": True,
        },
    )
    assert response.status_code == 200


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
async def test_analyze_purchase_persists_scenario_and_recommendation(
    demo_engine, demo_client, demo_token
) -> None:
    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"item": "a new laptop", "price": {"amount_minor": 150_000, "currency": "USD"}},
    )
    assert response.status_code == 200

    with demo_engine.connect() as conn:
        scenario_rows = (
            conn.execute(
                select(models.scenarios).where(
                    models.scenarios.c.household_id == fixtures.DEMO_HOUSEHOLD_ID
                )
            )
            .mappings()
            .all()
        )
        recommendation_rows = (
            conn.execute(
                select(models.recommendations).where(
                    models.recommendations.c.household_id == fixtures.DEMO_HOUSEHOLD_ID
                )
            )
            .mappings()
            .all()
        )

    assert len(scenario_rows) == 1
    assert scenario_rows[0]["input_json"]["item"] == "a new laptop"
    assert len(recommendation_rows) == 1
    assert recommendation_rows[0]["scenario_id"] == scenario_rows[0]["id"]
    assert recommendation_rows[0]["explanation_source"] == "deterministic_stub"


@pytest.mark.anyio
async def test_analyze_purchase_uses_llm_when_runtime_enabled(
    demo_client, demo_token, demo_engine, monkeypatch
) -> None:
    monkeypatch.setattr(
        "family_cfo_api.ai_runtime_selection.VLLMAdapter",
        _StubVllmAdapter("Buying a new laptop for USD 1,500.00 is well within your budget."),
    )
    await _enable_runtime(demo_client, demo_token)

    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"item": "a new laptop", "price": {"amount_minor": 150_000, "currency": "USD"}},
    )

    assert response.status_code == 200
    assert (
        response.json()["answer"]
        == "Buying a new laptop for USD 1,500.00 is well within your budget."
    )

    with demo_engine.connect() as conn:
        row = (
            conn.execute(
                select(models.recommendations).order_by(models.recommendations.c.created_at.desc())
            )
            .mappings()
            .first()
        )

    assert row["explanation_source"] == "llm"
    assert row["model_version"] == "stub-model"
    assert row["prompt_version"] == "purchase-advisor-v1"


@pytest.mark.anyio
async def test_analyze_purchase_falls_back_to_deterministic_on_guardrail_violation(
    demo_client, demo_token, demo_engine, monkeypatch
) -> None:
    monkeypatch.setattr(
        "family_cfo_api.ai_runtime_selection.VLLMAdapter",
        _StubVllmAdapter("This purchase carries a fabricated 42.7% hidden risk premium."),
    )
    await _enable_runtime(demo_client, demo_token)

    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"item": "a new laptop", "price": {"amount_minor": 150_000, "currency": "USD"}},
    )

    assert response.status_code == 200
    assert "42.7" not in response.json()["answer"]

    with demo_engine.connect() as conn:
        row = (
            conn.execute(
                select(models.recommendations).order_by(models.recommendations.c.created_at.desc())
            )
            .mappings()
            .first()
        )

    assert row["explanation_source"] == "deterministic_stub"


@pytest.mark.anyio
async def test_analyze_purchase_disabled_runtime_uses_deterministic_stub(
    demo_client, demo_token, monkeypatch
) -> None:
    monkeypatch.setattr(
        "family_cfo_api.ai_runtime_selection.VLLMAdapter",
        _StubVllmAdapter("this should never be called"),
    )
    response = await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "provider": "vllm",
            "base_url": "http://vllm.local:8000",
            "model": "stub-model",
            "enabled": False,
        },
    )
    assert response.status_code == 200

    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"item": "a new laptop", "price": {"amount_minor": 150_000, "currency": "USD"}},
    )

    assert response.status_code == 200
    assert "this should never be called" not in response.json()["answer"]

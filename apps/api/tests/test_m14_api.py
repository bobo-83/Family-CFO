import pytest


@pytest.mark.anyio
async def test_account_debt_terms_round_trip(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    created = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "name": "Visa",
            "type": "credit_card",
            "currency": "USD",
            "annual_interest_rate": 0.199,
            "minimum_payment": {"amount_minor": 5000, "currency": "USD"},
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["annual_interest_rate"] == pytest.approx(0.199)
    assert body["minimum_payment"] == {"amount_minor": 5000, "currency": "USD"}

    account_id = body["id"]
    updated = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"annual_interest_rate": 0.149},
    )
    assert updated.status_code == 200
    assert updated.json()["annual_interest_rate"] == pytest.approx(0.149)


@pytest.mark.anyio
async def test_account_next_payment_due_date_round_trip(demo_client, demo_token) -> None:
    """ADR 0033: a next payment due date can be set by hand and reads back on the
    account (create, update, and list)."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    created = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "name": "U.S. Department of Education",
            "type": "student_loan",
            "currency": "USD",
            "minimum_payment": {"amount_minor": 7801, "currency": "USD"},
            "next_payment_due_date": "2026-08-08",
        },
    )
    assert created.status_code == 201
    account_id = created.json()["id"]
    assert created.json()["next_payment_due_date"] == "2026-08-08"

    updated = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"next_payment_due_date": "2026-09-08"},
    )
    assert updated.status_code == 200
    assert updated.json()["next_payment_due_date"] == "2026-09-08"

    # A loan carries a balance, so it appears in the list — which reads through a
    # different (JOIN) path; the due date must round-trip there too.
    await demo_client.post(
        f"/api/v1/accounts/{account_id}/balances",
        headers=headers,
        json={"balance": {"amount_minor": -1_000_000, "currency": "USD"}},
    )
    listed = await demo_client.get("/api/v1/accounts", headers=headers)
    account = next(a for a in listed.json()["accounts"] if a["id"] == account_id)
    assert account["next_payment_due_date"] == "2026-09-08"


@pytest.mark.anyio
async def test_purchase_advisor_models_debt_when_terms_exist(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    # A credit card carrying terms and a balance owed.
    account_id = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={
                "name": "Card",
                "type": "credit_card",
                "currency": "USD",
                "annual_interest_rate": 0.18,
                "minimum_payment": {"amount_minor": 20000, "currency": "USD"},
            },
        )
    ).json()["id"]
    await demo_client.post(
        f"/api/v1/accounts/{account_id}/balances",
        headers=headers,
        json={"balance": {"amount_minor": -500000, "currency": "USD"}},
    )

    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers=headers,
        json={"item": "a laptop", "price": {"amount_minor": 100000, "currency": "USD"}},
    )
    assert response.status_code == 200
    debt_impacts = [i for i in response.json()["impacts"] if i["area"] == "debt"]
    assert len(debt_impacts) == 1
    summary = debt_impacts[0]["summary"]
    # Real model, not the old "cannot be modeled without interest rate and payment data" placeholder.
    assert "cannot be modeled" not in summary
    assert "months" in summary or "interest" in summary


@pytest.mark.anyio
async def test_purchase_advisor_notes_untermed_debt(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    # A liability account with a balance but no terms.
    account_id = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "Loan", "type": "auto_loan", "currency": "USD"},
        )
    ).json()["id"]
    await demo_client.post(
        f"/api/v1/accounts/{account_id}/balances",
        headers=headers,
        json={"balance": {"amount_minor": -1500000, "currency": "USD"}},
    )

    response = await demo_client.post(
        "/api/v1/advisor/purchase",
        headers=headers,
        json={"item": "a chair", "price": {"amount_minor": 30000, "currency": "USD"}},
    )
    debt_impacts = [i for i in response.json()["impacts"] if i["area"] == "debt"]
    assert len(debt_impacts) == 1
    assert "not modeled" in debt_impacts[0]["summary"]


@pytest.mark.anyio
async def test_retirement_scenario_returns_grounded_recommendation(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    response = await demo_client.post(
        "/api/v1/advisor/retirement",
        headers=headers,
        json={
            "current_age": 40,
            "retirement_age": 65,
            "current_savings": {"amount_minor": 5000000, "currency": "USD"},
            "monthly_contribution": {"amount_minor": 50000, "currency": "USD"},
            "annual_return_rate": 0.06,
            "annual_expenses": {"amount_minor": 6000000, "currency": "USD"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["calculation_refs"]  # grounded in a persisted calculation
    assert any(i["area"] == "retirement" for i in body["impacts"])
    assert "grow to about" in body["answer"]


@pytest.mark.anyio
async def test_retirement_rejects_retirement_age_not_after_current(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/advisor/retirement",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "current_age": 65,
            "retirement_age": 65,
            "current_savings": {"amount_minor": 0, "currency": "USD"},
            "monthly_contribution": {"amount_minor": 0, "currency": "USD"},
            "annual_return_rate": 0.05,
        },
    )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_retirement_requires_auth(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/advisor/retirement",
        json={
            "current_age": 30,
            "retirement_age": 60,
            "current_savings": {"amount_minor": 0, "currency": "USD"},
            "monthly_contribution": {"amount_minor": 0, "currency": "USD"},
            "annual_return_rate": 0.05,
        },
    )
    assert response.status_code == 401

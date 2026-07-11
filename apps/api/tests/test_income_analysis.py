"""M61: income analysis from checking deposits + annual tax estimate."""

from datetime import date, timedelta

import pytest


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_checking_with_payroll(client, token: str) -> dict[str, str]:
    """A checking account with 6 biweekly ACME payroll deposits + 1 one-off."""
    headers = _headers(token)
    account = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Everyday Checking", "type": "checking", "currency": "USD"},
    )
    account_id = account.json()["id"]
    ids: dict[str, str] = {}
    today = date.today()
    for i in range(6):
        occurred = today - timedelta(days=14 * (6 - i))
        response = await client.post(
            "/api/v1/transactions",
            headers=headers,
            json={
                "account_id": account_id,
                "occurred_at": occurred.isoformat(),
                "amount": {"amount_minor": 461_538, "currency": "USD"},
                "merchant": "ACME CORP PAYROLL",
            },
        )
        ids[f"payroll_{i}"] = response.json()["id"]
    one_off = await client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": account_id,
            "occurred_at": (today - timedelta(days=40)).isoformat(),
            "amount": {"amount_minor": 90_000, "currency": "USD"},
            "merchant": "VENMO CASHOUT",
        },
    )
    ids["one_off"] = one_off.json()["id"]
    return ids


async def _analysis(client, token: str) -> dict:
    response = await client.get("/api/v1/income/analysis", headers=_headers(token))
    assert response.status_code == 200
    return response.json()


@pytest.mark.anyio
async def test_detects_payroll_with_evidence_and_rollup(demo_client, demo_token) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)

    body = await _analysis(demo_client, demo_token)

    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert source["source_key"] == "acme corp payroll"
    assert source["frequency"] == "biweekly"
    assert source["typical_amount"]["amount_minor"] == 461_538
    assert len(source["transactions"]) == 6
    assert source["total_amount"]["amount_minor"] == 6 * 461_538

    # The one-off cash-out is offered for manual classification, not counted.
    assert [t["name"] for t in body["other_inflows"]] == ["VENMO CASHOUT"]
    # M62: full evidence details ride along on every transaction.
    other = body["other_inflows"][0]
    assert other["merchant"] == "VENMO CASHOUT"
    assert other["account_name"] == "Everyday Checking"
    assert source["transactions"][0]["account_name"] == "Everyday Checking"
    assert body["rollup"]["annual_income"]["amount_minor"] == 6 * 461_538
    assert body["rollup"]["transaction_count"] == 6


@pytest.mark.anyio
async def test_tax_estimate_grosses_up_net_income_by_default(demo_client, demo_token) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)

    tax = (await _analysis(demo_client, demo_token))["tax"]

    net = 6 * 461_538
    assert tax["filing_status"] == "married_joint"
    assert tax["income_treated_as_net"] is True
    assert tax["net_income"]["amount_minor"] == net
    # gross = net + tax(gross): strictly larger, and internally consistent.
    assert tax["gross_income"]["amount_minor"] > net
    assert (
        abs(tax["gross_income"]["amount_minor"] - tax["total_tax"]["amount_minor"] - net) <= 2
    )
    assert any("state income tax is NOT included" in a for a in tax["assumptions"])


@pytest.mark.anyio
async def test_exclude_removes_a_deposit_and_shrinks_the_rollup(
    demo_client, demo_token
) -> None:
    ids = await _seed_checking_with_payroll(demo_client, demo_token)
    headers = _headers(demo_token)

    response = await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=headers,
        json={"transaction_id": ids["payroll_0"], "verdict": "exclude"},
    )
    assert response.status_code == 204

    body = await _analysis(demo_client, demo_token)
    assert body["rollup"]["annual_income"]["amount_minor"] == 5 * 461_538
    assert len(body["sources"][0]["transactions"]) == 5
    excluded = [t for t in body["other_inflows"] if t["excluded"]]
    assert [t["transaction_id"] for t in excluded] == [ids["payroll_0"]]

    # "clear" restores the deposit to its detected source.
    await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=headers,
        json={"transaction_id": ids["payroll_0"], "verdict": "clear"},
    )
    body = await _analysis(demo_client, demo_token)
    assert body["rollup"]["annual_income"]["amount_minor"] == 6 * 461_538


@pytest.mark.anyio
async def test_include_adds_a_missed_deposit(demo_client, demo_token) -> None:
    ids = await _seed_checking_with_payroll(demo_client, demo_token)

    response = await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=_headers(demo_token),
        json={"transaction_id": ids["one_off"], "verdict": "include"},
    )
    assert response.status_code == 204

    body = await _analysis(demo_client, demo_token)
    manual = [s for s in body["sources"] if s["manually_added"]]
    assert len(manual) == 1
    assert manual[0]["name"] == "Added by you"
    assert [t["transaction_id"] for t in manual[0]["transactions"]] == [ids["one_off"]]
    assert body["rollup"]["annual_income"]["amount_minor"] == 6 * 461_538 + 90_000
    assert body["other_inflows"] == []


@pytest.mark.anyio
async def test_settings_switch_filing_status_and_gross_treatment(
    demo_client, demo_token
) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)

    response = await demo_client.put(
        "/api/v1/income/analysis/settings",
        headers=_headers(demo_token),
        json={"tax_filing_status": "single", "income_treated_as_net": False},
    )
    assert response.status_code == 204

    tax = (await _analysis(demo_client, demo_token))["tax"]
    assert tax["filing_status"] == "single"
    assert tax["income_treated_as_net"] is False
    # Treated as gross: the income IS the gross figure.
    assert tax["gross_income"]["amount_minor"] == 6 * 461_538
    assert tax["net_income"] is None


# --- M65: amount clustering + state tax ---


@pytest.mark.anyio
async def test_paycheck_detected_inside_mixed_amount_transfer_label(
    demo_client, demo_token
) -> None:
    """Biweekly ~$2,830 deposits auto-detect even when big one-offs share the label."""
    headers = _headers(demo_token)
    checking = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "My Checking", "type": "checking", "currency": "USD"},
        )
    ).json()["id"]
    today = date.today()
    for i in range(6):
        await demo_client.post(
            "/api/v1/transactions",
            headers=headers,
            json={
                "account_id": checking,
                "occurred_at": (today - timedelta(days=14 * (6 - i))).isoformat(),
                "amount": {"amount_minor": 283_078 + i, "currency": "USD"},
                "merchant": "Online Transfer",
            },
        )
    # Both one-offs sit far outside the paycheck's amount band (>30% gap).
    for amount, days in ((2_312_400, 40), (800_000, 3)):
        await demo_client.post(
            "/api/v1/transactions",
            headers=headers,
            json={
                "account_id": checking,
                "occurred_at": (today - timedelta(days=days)).isoformat(),
                "amount": {"amount_minor": amount, "currency": "USD"},
                "merchant": "Online Transfer",
            },
        )

    body = await _analysis(demo_client, demo_token)

    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert source["frequency"] == "biweekly"
    assert len(source["transactions"]) == 6
    assert "2,830" in source["name"]  # disambiguated with the typical amount
    # The one-offs stay offered, not silently absorbed.
    assert len(body["other_inflows"]) == 2


@pytest.mark.anyio
async def test_state_setting_changes_the_tax_estimate(demo_client, demo_token) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)
    headers = _headers(demo_token)

    response = await demo_client.put(
        "/api/v1/income/analysis/settings",
        headers=headers,
        json={"tax_filing_status": "married_joint", "income_treated_as_net": True, "state": "ca"},
    )
    assert response.status_code == 204

    tax = (await _analysis(demo_client, demo_token))["tax"]
    assert tax["state"] == "CA"
    assert tax["state_income_tax"] is not None
    assert any("2024 FTB brackets" in a for a in tax["assumptions"])

    bad = await demo_client.put(
        "/api/v1/income/analysis/settings",
        headers=headers,
        json={"tax_filing_status": "single", "income_treated_as_net": True, "state": "ZZ"},
    )
    assert bad.status_code == 422


# --- M63: internal transfers, reject, coverage ---


@pytest.mark.anyio
async def test_matched_pair_transfer_is_hidden_entirely(demo_client, demo_token) -> None:
    """A deposit whose amount left a sibling account is money movement, not income."""
    headers = _headers(demo_token)
    checking = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "My Checking", "type": "checking", "currency": "USD"},
        )
    ).json()["id"]
    savings = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "My Savings", "type": "savings", "currency": "USD"},
        )
    ).json()["id"]
    today = date.today()
    inflow = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": checking,
            "occurred_at": (today - timedelta(days=10)).isoformat(),
            "amount": {"amount_minor": 500_000, "currency": "USD"},
            "merchant": "Online Transfer",
        },
    )
    await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": savings,
            "occurred_at": (today - timedelta(days=11)).isoformat(),
            "amount": {"amount_minor": -500_000, "currency": "USD"},
            "merchant": "Online Transfer",
        },
    )

    body = await _analysis(demo_client, demo_token)

    assert body["sources"] == []
    assert body["other_inflows"] == []

    # An explicit include verdict overrides suppression — the user decides.
    await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=headers,
        json={"transaction_id": inflow.json()["id"], "verdict": "include"},
    )
    body = await _analysis(demo_client, demo_token)
    assert body["rollup"]["annual_income"]["amount_minor"] == 500_000


@pytest.mark.anyio
async def test_bank_labeled_internal_transfer_is_hidden(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    checking = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "My Checking", "type": "checking", "currency": "USD"},
        )
    ).json()["id"]
    await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": checking,
            "occurred_at": (date.today() - timedelta(days=5)).isoformat(),
            "amount": {"amount_minor": 700_000, "currency": "USD"},
            "merchant": "Internal Transfer Credit Savings",
            "description": "Internal Transfer Credit: Savings -2061",
        },
    )

    body = await _analysis(demo_client, demo_token)

    assert body["other_inflows"] == []
    assert body["rollup"]["annual_income"]["amount_minor"] == 0


@pytest.mark.anyio
async def test_coverage_warning_for_partial_history(demo_client, demo_token) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)  # starts ~84 days ago

    body = await _analysis(demo_client, demo_token)

    assert body["coverage_warning"] is not None
    assert "not a full year" in body["coverage_warning"]
    assert 80 <= body["rollup"]["coverage_days"] <= 90
    assert body["rollup"]["coverage_start"] is not None


@pytest.mark.anyio
async def test_no_coverage_warning_with_full_window(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    checking = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "My Checking", "type": "checking", "currency": "USD"},
        )
    ).json()["id"]
    await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": checking,
            "occurred_at": (date.today() - timedelta(days=364)).isoformat(),
            "amount": {"amount_minor": 10_000, "currency": "USD"},
            "merchant": "Old Deposit",
        },
    )

    body = await _analysis(demo_client, demo_token)

    assert body["coverage_warning"] is None
    assert body["rollup"]["coverage_days"] >= 358


@pytest.mark.anyio
async def test_override_on_foreign_transaction_is_404(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=_headers(demo_token),
        json={"transaction_id": "00000000-0000-0000-0000-000000000000", "verdict": "exclude"},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_viewer_cannot_edit_overrides_or_settings(
    demo_client, demo_viewer_token
) -> None:
    headers = _headers(demo_viewer_token)
    override = await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=headers,
        json={"transaction_id": "x", "verdict": "exclude"},
    )
    assert override.status_code == 403
    settings = await demo_client.put(
        "/api/v1/income/analysis/settings",
        headers=headers,
        json={"tax_filing_status": "single", "income_treated_as_net": True},
    )
    assert settings.status_code == 403

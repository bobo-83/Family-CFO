"""ADR 0049: flag sizeable inflows misfiled as Transfer that have no matching
internal leg, so the user can confirm them as income (or keep them as transfers)."""

from datetime import date, timedelta

import pytest


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _account(client, token: str, name: str, kind: str = "checking") -> str:
    resp = await client.post(
        "/api/v1/accounts",
        headers=_headers(token),
        json={"name": name, "type": kind, "currency": "USD"},
    )
    return resp.json()["id"]


async def _category(client, token: str, name: str) -> str:
    resp = await client.post("/api/v1/categories", headers=_headers(token), json={"name": name})
    return resp.json()["id"]


async def _txn(
    client, token: str, account_id: str, amount_minor: int, *, days_ago: int,
    merchant: str = "Online Transfer", category_id: str | None = None,
) -> str:
    body = {
        "account_id": account_id,
        "occurred_at": (date.today() - timedelta(days=days_ago)).isoformat(),
        "amount": {"amount_minor": amount_minor, "currency": "USD"},
        "merchant": merchant,
    }
    if category_id is not None:
        body["category_id"] = category_id
    resp = await client.post("/api/v1/transactions", headers=_headers(token), json=body)
    return resp.json()["id"]


async def _suspected(client, token: str) -> list[dict]:
    resp = await client.get(
        "/api/v1/transactions/review?kind=suspected_income", headers=_headers(token)
    )
    assert resp.status_code == 200
    return resp.json()["transactions"]


@pytest.mark.anyio
async def test_lone_transfer_inflow_is_flagged_matched_pair_is_not(
    demo_client, demo_token
) -> None:
    checking = await _account(demo_client, demo_token, "Checking")
    savings = await _account(demo_client, demo_token, "Savings", "savings")
    transfers = await _category(demo_client, demo_token, "Transfers")

    # A: a real paycheck misfiled as Transfer — no sibling leg -> suspected.
    misfiled = await _txn(
        demo_client, demo_token, checking, 300_000, days_ago=5, category_id=transfers
    )
    # B: a genuine internal move — has the opposite leg in savings -> not suspected.
    internal = await _txn(
        demo_client, demo_token, checking, 500_000, days_ago=10, category_id=transfers
    )
    await _txn(demo_client, demo_token, savings, -500_000, days_ago=11, category_id=transfers)
    # C: below the $200 floor -> ignored even with no sibling.
    tiny = await _txn(
        demo_client, demo_token, checking, 5_000, days_ago=3, category_id=transfers
    )

    flagged = {t["id"] for t in await _suspected(demo_client, demo_token)}
    assert misfiled in flagged
    assert internal not in flagged
    assert tiny not in flagged

    # The flag rides on the transaction wherever it is serialized.
    listed = (
        await demo_client.get("/api/v1/transactions", headers=_headers(demo_token))
    ).json()["transactions"]
    by_id = {t["id"]: t for t in listed}
    assert by_id[misfiled]["suspected_income"] is True
    assert by_id[internal]["suspected_income"] is False


@pytest.mark.anyio
async def test_dismissing_as_transfer_stops_flagging(demo_client, demo_token) -> None:
    checking = await _account(demo_client, demo_token, "Checking")
    transfers = await _category(demo_client, demo_token, "Transfers")
    txn = await _txn(
        demo_client, demo_token, checking, 300_000, days_ago=5, category_id=transfers
    )
    assert txn in {t["id"] for t in await _suspected(demo_client, demo_token)}

    # "Keep as transfer" -> an exclude override; it must not be re-flagged.
    resp = await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=_headers(demo_token),
        json={"transaction_id": txn, "verdict": "exclude"},
    )
    assert resp.status_code in (200, 204)
    assert txn not in {t["id"] for t in await _suspected(demo_client, demo_token)}


@pytest.mark.anyio
async def test_confirming_as_income_clears_the_flag(demo_client, demo_token) -> None:
    checking = await _account(demo_client, demo_token, "Checking")
    transfers = await _category(demo_client, demo_token, "Transfers")
    income = await _category(demo_client, demo_token, "Income")
    txn = await _txn(
        demo_client, demo_token, checking, 300_000, days_ago=5, category_id=transfers
    )
    assert txn in {t["id"] for t in await _suspected(demo_client, demo_token)}

    # "Confirm as income" recategorizes to Income -> no longer a transfer candidate.
    resp = await demo_client.patch(
        f"/api/v1/transactions/{txn}",
        headers=_headers(demo_token),
        json={"category_id": income},
    )
    assert resp.status_code == 200
    assert txn not in {t["id"] for t in await _suspected(demo_client, demo_token)}


@pytest.mark.anyio
async def test_payment_credit_on_a_loan_account_is_not_suspected(
    demo_client, demo_token
) -> None:
    """A positive posting on a liability account is a loan/lease payment credit,
    not a paycheck — it must never be flagged as income (real-data false positive)."""
    loan = await _account(demo_client, demo_token, "Auto Loan", "auto_loan")
    transfers = await _category(demo_client, demo_token, "Transfers")
    payment = await _txn(
        demo_client, demo_token, loan, 42_828, days_ago=5,
        merchant="Payment", category_id=transfers,
    )
    assert payment not in {t["id"] for t in await _suspected(demo_client, demo_token)}


@pytest.mark.anyio
async def test_regular_income_deposit_is_never_suspected(demo_client, demo_token) -> None:
    checking = await _account(demo_client, demo_token, "Checking")
    income = await _category(demo_client, demo_token, "Income")
    txn = await _txn(
        demo_client, demo_token, checking, 300_000, days_ago=5,
        merchant="ACME PAYROLL", category_id=income,
    )
    assert txn not in {t["id"] for t in await _suspected(demo_client, demo_token)}

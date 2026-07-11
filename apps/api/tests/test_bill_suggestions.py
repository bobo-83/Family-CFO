"""M58: bill suggestions detected from checking/credit-card transactions."""

from datetime import date, timedelta

import pytest

from family_cfo_api.bill_detection import (
    DetectionTransaction,
    detect_bill_candidates,
    normalize_merchant,
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _txn(
    occurred_at: date,
    amount_minor: int,
    merchant: str | None = "NETFLIX.COM",
    description: str | None = None,
    currency: str = "USD",
) -> DetectionTransaction:
    return DetectionTransaction(
        occurred_at=occurred_at,
        amount_minor=amount_minor,
        currency=currency,
        merchant=merchant,
        description=description,
    )


def _monthly(merchant: str, amount: int, months: int, day: int = 3) -> list[DetectionTransaction]:
    return [
        _txn(date(2026, month, day), -amount, merchant) for month in range(8 - months, 8)
    ][:months]


# --- normalization ---


def test_normalize_merchant_strips_store_numbers_and_punctuation() -> None:
    assert normalize_merchant("NETFLIX.COM *4029") == "netflix com"
    assert normalize_merchant("Netflix.com") == "netflix com"
    assert normalize_merchant("  PG&E 2026-06 ") == "pg e"
    assert normalize_merchant(None) == ""


# --- detection ---


def test_detects_monthly_subscription() -> None:
    candidates = detect_bill_candidates(_monthly("NETFLIX.COM *401", 1549, 4))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.merchant_key == "netflix com"
    assert candidate.frequency == "monthly"
    assert candidate.amount_minor == 1549
    assert candidate.occurrences == 4
    assert candidate.last_seen == date(2026, 7, 3)
    assert candidate.next_due_date == date(2026, 8, 3)


def test_two_occurrences_are_not_enough_for_monthly() -> None:
    assert detect_bill_candidates(_monthly("Spotify", 999, 2)) == []


def test_detects_annual_with_two_occurrences() -> None:
    txns = [
        _txn(date(2025, 7, 15), -84_000, "State Farm Insurance"),
        _txn(date(2026, 7, 14), -85_000, "State Farm Insurance"),
    ]

    candidates = detect_bill_candidates(txns)

    assert len(candidates) == 1
    assert candidates[0].frequency == "annual"


def test_irregular_dates_are_rejected() -> None:
    txns = [
        _txn(date(2026, 3, 1), -5_000, "Corner Cafe"),
        _txn(date(2026, 3, 9), -5_000, "Corner Cafe"),
        _txn(date(2026, 5, 2), -5_000, "Corner Cafe"),
        _txn(date(2026, 7, 21), -5_000, "Corner Cafe"),
    ]

    assert detect_bill_candidates(txns) == []


def test_wildly_varying_amounts_are_rejected() -> None:
    txns = [
        _txn(date(2026, 5, 3), -2_000, "Amazon"),
        _txn(date(2026, 6, 3), -19_000, "Amazon"),
        _txn(date(2026, 7, 3), -4_500, "Amazon"),
    ]

    assert detect_bill_candidates(txns) == []


def test_utility_style_variation_within_tolerance_passes() -> None:
    txns = [
        _txn(date(2026, 4, 20), -11_000, "PG&E"),
        _txn(date(2026, 5, 20), -12_500, "PG&E"),
        _txn(date(2026, 6, 19), -14_000, "PG&E"),
        _txn(date(2026, 7, 20), -12_000, "PG&E"),
    ]

    candidates = detect_bill_candidates(txns)

    assert len(candidates) == 1
    assert candidates[0].frequency == "monthly"
    assert candidates[0].amount_minor == 12_250  # median


def test_income_and_unnamed_transactions_are_ignored() -> None:
    txns = [
        _txn(date(2026, 5, 1), 500_000, "Employer Payroll"),
        _txn(date(2026, 6, 1), 500_000, "Employer Payroll"),
        _txn(date(2026, 7, 1), 500_000, "Employer Payroll"),
        _txn(date(2026, 5, 2), -1_000, merchant=None, description=None),
    ]

    assert detect_bill_candidates(txns) == []


def test_description_is_the_fallback_grouping_key() -> None:
    txns = [
        _txn(date(2026, 5, 5), -3_000, merchant=None, description="GYM MEMBERSHIP 0505"),
        _txn(date(2026, 6, 5), -3_000, merchant=None, description="GYM MEMBERSHIP 0605"),
        _txn(date(2026, 7, 6), -3_000, merchant=None, description="GYM MEMBERSHIP 0706"),
    ]

    candidates = detect_bill_candidates(txns)

    assert len(candidates) == 1
    assert candidates[0].merchant_key == "gym membership"


# --- API ---


async def _seed_recurring_charges(client, token: str, merchant: str, day_offsets: int = 3) -> None:
    """Create a checking account with a recent monthly charge history."""
    headers = _headers(token)
    account = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": f"{merchant} checking", "type": "checking", "currency": "USD"},
    )
    account_id = account.json()["id"]
    today = date.today()
    for months_back in range(day_offsets, 0, -1):
        occurred = today - timedelta(days=30 * months_back)
        response = await client.post(
            "/api/v1/transactions",
            headers=headers,
            json={
                "account_id": account_id,
                "occurred_at": occurred.isoformat(),
                "amount": {"amount_minor": -1_549, "currency": "USD"},
                "merchant": merchant,
            },
        )
        assert response.status_code == 201


@pytest.mark.anyio
async def test_suggestions_round_trip_confirm_and_dismiss(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    await _seed_recurring_charges(demo_client, demo_token, "NETFLIX.COM")

    listed = (await demo_client.get("/api/v1/bills/suggestions", headers=headers)).json()
    suggestions = listed["suggestions"]
    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion["merchant_key"] == "netflix com"
    assert suggestion["frequency"] == "monthly"
    assert suggestion["amount"]["amount_minor"] == 1549

    # Confirming = creating the bill with the suggested values; the matching
    # name then excludes the candidate from the next fetch.
    created = await demo_client.post(
        "/api/v1/bills",
        headers=headers,
        json={
            "name": suggestion["name"],
            "amount": suggestion["amount"],
            "frequency": suggestion["frequency"],
            "next_due_date": suggestion["next_due_date"],
        },
    )
    assert created.status_code == 201
    listed = (await demo_client.get("/api/v1/bills/suggestions", headers=headers)).json()
    assert listed["suggestions"] == []


@pytest.mark.anyio
async def test_dismissed_suggestion_stays_hidden(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    await _seed_recurring_charges(demo_client, demo_token, "GYM CO")

    dismissed = await demo_client.post(
        "/api/v1/bills/suggestions/dismissals",
        headers=headers,
        json={"merchant_key": "gym co"},
    )
    assert dismissed.status_code == 204

    listed = (await demo_client.get("/api/v1/bills/suggestions", headers=headers)).json()
    assert listed["suggestions"] == []


@pytest.mark.anyio
async def test_viewer_cannot_dismiss_suggestions(demo_client, demo_viewer_token) -> None:
    response = await demo_client.post(
        "/api/v1/bills/suggestions/dismissals",
        headers=_headers(demo_viewer_token),
        json={"merchant_key": "netflix com"},
    )
    assert response.status_code == 403

"""Scan a bill photo/PDF into candidate values for the New Bill form."""

import pytest

from family_cfo_api.api.bills import parse_bill_scan


def test_bill_scan_reads_name_amount_due_date_and_frequency() -> None:
    result = parse_bill_scan(
        '{"biller": "City Water & Sewer", "amount_due": 84.37, '
        '"due_date": "2026-08-01", "frequency": "monthly"}'
    )

    assert result.name == "City Water & Sewer"
    assert result.amount_minor == 8_437
    assert result.frequency == "monthly"
    assert result.next_due_date is not None and result.next_due_date.isoformat() == "2026-08-01"
    assert "CONFIRM" in result.note


def test_bill_scan_json_in_code_fence_is_parsed() -> None:
    result = parse_bill_scan('```json\n{"biller": "Verizon", "amount_due": 120}\n```')
    assert result.name == "Verizon"
    assert result.amount_minor == 12_000


def test_bill_scan_unknown_frequency_is_dropped_never_guessed() -> None:
    result = parse_bill_scan('{"biller": "PG&E", "amount_due": 210.5, "frequency": "sometimes"}')
    assert result.frequency is None
    assert result.amount_minor == 21_050


def test_bill_scan_unreadable_output_falls_back_to_manual_entry() -> None:
    result = parse_bill_scan("I could not find a bill in this image.")
    assert result.name is None
    assert result.amount_minor is None
    assert "manually" in result.note


def test_bill_scan_negative_or_zero_amount_is_rejected() -> None:
    result = parse_bill_scan('{"biller": "Acme", "amount_due": -5}')
    assert result.amount_minor is None


@pytest.mark.anyio
async def test_scan_bill_requires_authentication(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/bills/scan", json={"image_base64": "aGk=", "image_media_type": "image/jpeg"}
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_scan_bill_without_vision_model_is_503(demo_client, demo_token) -> None:
    # Default test settings configure no vision describer.
    response = await demo_client.post(
        "/api/v1/bills/scan",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"image_base64": "aGk=", "image_media_type": "image/jpeg"},
    )
    assert response.status_code == 503

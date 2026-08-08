"""#25: matching statement lines to synced transactions.

The bias throughout: a WRONG match is worse than no match, because it hides a
real hole in the feed. These tests pin that bias.
"""

from datetime import date

from family_cfo_api.statement_reconciliation import (
    LedgerTransaction,
    StatementLine,
    reconcile,
)


def _line(id_, day, desc, minor):
    return StatementLine(
        id=id_, occurred_on=date(2026, 8, day), description=desc, amount_minor=minor
    )


def _txn(id_, day, merchant, minor):
    return LedgerTransaction(
        id=id_, occurred_at=date(2026, 8, day), merchant=merchant, amount_minor=minor
    )


def test_same_charge_matches_across_a_posting_date_gap() -> None:
    """Statement and feed disagree on the day by a few days — routinely."""
    r = reconcile(
        [_line("l1", 3, "BLUE BOTTLE COFFEE", -1_250)],
        [_txn("t1", 5, "SQ *BLUE BOTTLE", -1_250)],
    )
    assert r.matched_count == 1
    assert r.matches[0].transaction_id == "t1"
    assert r.matches[0].kind == "exact"
    assert r.missing_from_sync_count == 0


def test_a_charge_the_feed_never_delivered_is_reported_not_guessed() -> None:
    """The headline value: naming the hole in the ledger."""
    r = reconcile(
        [_line("l1", 3, "CORNER STORE", -4_200)],
        [_txn("t1", 3, "SOMETHING ELSE ENTIRELY", -9_900)],
    )
    assert r.missing_from_sync_count == 1
    assert r.matches[0].transaction_id is None
    assert r.not_on_statement_count == 1
    assert r.unmatched_transaction_ids == ["t1"]


def test_one_transaction_cannot_explain_two_identical_lines() -> None:
    """Two identical charges, one synced: exactly one gap must remain visible."""
    r = reconcile(
        [_line("l1", 3, "COFFEE", -500), _line("l2", 3, "COFFEE", -500)],
        [_txn("t1", 3, "COFFEE", -500)],
    )
    assert r.matched_count == 1
    assert r.missing_from_sync_count == 1


def test_a_tip_adjustment_is_flagged_rather_than_silently_accepted() -> None:
    r = reconcile(
        [_line("l1", 3, "RESTAURANT ABC", -10_000)],
        [_txn("t1", 3, "RESTAURANT ABC", -10_400)],
    )
    assert r.amount_differs_count == 1
    assert r.matches[0].kind == "amount_differs"
    assert r.matches[0].transaction_id == "t1"


def test_a_different_amount_needs_the_name_to_agree() -> None:
    """Without a name match, a near amount is not evidence of the same charge."""
    r = reconcile(
        [_line("l1", 3, "MERCHANT ONE", -10_000)],
        [_txn("t1", 3, "COMPLETELY UNRELATED", -10_200)],
    )
    assert r.matched_count == 0
    assert r.missing_from_sync_count == 1


def test_a_payment_never_matches_a_charge() -> None:
    """Signs must agree: a credit is not a purchase of the same size."""
    r = reconcile(
        [_line("l1", 3, "PAYMENT THANK YOU", 50_000)],
        [_txn("t1", 3, "PAYMENT THANK YOU", -50_000)],
    )
    assert r.matched_count == 0


def test_a_charge_outside_the_date_window_is_not_matched() -> None:
    r = reconcile([_line("l1", 1, "STORE", -2_500)], [_txn("t1", 20, "STORE", -2_500)])
    assert r.matched_count == 0


def test_the_best_pairing_wins_when_candidates_compete() -> None:
    r = reconcile(
        [_line("l1", 3, "ALPHA MARKET", -1_000), _line("l2", 4, "BETA FUEL", -2_000)],
        [_txn("t1", 3, "ALPHA MARKET", -1_000), _txn("t2", 4, "BETA FUEL", -2_000)],
    )
    assert r.matched_count == 2
    pairs = {m.line_id: m.transaction_id for m in r.matches}
    assert pairs == {"l1": "t1", "l2": "t2"}
    assert r.not_on_statement_count == 0


def test_reconciliation_is_reproducible() -> None:
    """Equal-scoring ties must resolve identically every run, or a re-run would
    churn the labels."""
    lines = [_line("l1", 3, "SAME", -1_000), _line("l2", 3, "SAME", -1_000)]
    txns = [_txn("t1", 3, "SAME", -1_000), _txn("t2", 3, "SAME", -1_000)]
    first = reconcile(lines, txns)
    second = reconcile(lines, txns)
    assert [(m.line_id, m.transaction_id) for m in first.matches] == [
        (m.line_id, m.transaction_id) for m in second.matches
    ]


def test_empty_statement_reports_everything_as_unaccounted() -> None:
    r = reconcile([], [_txn("t1", 3, "STORE", -1_000)])
    assert r.matched_count == 0
    assert r.not_on_statement_count == 1
    assert r.missing_from_sync_count == 0


# --- end-to-end over real records -------------------------------------------

import pytest


async def _card_with_statement(demo_client, headers):
    card = (await demo_client.post(
        "/api/v1/accounts", headers=headers,
        json={"name": "Recon Card", "type": "credit_card", "currency": "USD"},
    )).json()["id"]
    statement = (await demo_client.post(
        "/api/v1/accounts/card-statements", headers=headers,
        json={
            "account_id": card,
            "statement_balance": {"amount_minor": 20_000, "currency": "USD"},
            "due_date": "2026-09-15",
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
        },
    )).json()["id"]
    return card, statement


@pytest.mark.anyio
async def test_reconciliation_names_the_charge_the_feed_missed(demo_client, demo_token):
    """The whole point: a statement line with no synced transaction is a hole
    in the ledger, and the app says so."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    card, statement = await _card_with_statement(demo_client, headers)

    # One charge WAS synced; the other never arrived.
    await demo_client.post(
        "/api/v1/transactions", headers=headers,
        json={
            "account_id": card,
            "occurred_at": "2026-08-10",
            "amount": {"amount_minor": -4_250, "currency": "USD"},
            "merchant": "SQ *BLUE BOTTLE",
        },
    )

    result = await demo_client.put(
        f"/api/v1/accounts/card-statements/{statement}/lines",
        headers=headers,
        json={
            "lines": [
                {
                    "occurred_on": "2026-08-09",
                    "description": "BLUE BOTTLE COFFEE",
                    "amount": {"amount_minor": -4_250, "currency": "USD"},
                },
                {
                    "occurred_on": "2026-08-12",
                    "description": "CORNER HARDWARE",
                    "amount": {"amount_minor": -9_900, "currency": "USD"},
                },
            ]
        },
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["matched_count"] == 1
    assert body["missing_from_sync_count"] == 1
    assert body["period_label"] == "August 2026"

    missing = [line for line in body["lines"] if line["matched_transaction_id"] is None]
    assert len(missing) == 1
    assert missing[0]["description"] == "CORNER HARDWARE"


@pytest.mark.anyio
async def test_rescanning_replaces_lines_instead_of_doubling_them(
    demo_client, demo_token
):
    headers = {"Authorization": f"Bearer {demo_token}"}
    _card, statement = await _card_with_statement(demo_client, headers)
    payload = {
        "lines": [
            {
                "occurred_on": "2026-08-09",
                "description": "STORE",
                "amount": {"amount_minor": -1_000, "currency": "USD"},
            }
        ]
    }
    await demo_client.put(
        f"/api/v1/accounts/card-statements/{statement}/lines", headers=headers, json=payload
    )
    second = await demo_client.put(
        f"/api/v1/accounts/card-statements/{statement}/lines", headers=headers, json=payload
    )
    assert len(second.json()["lines"]) == 1


@pytest.mark.anyio
async def test_a_later_sync_closes_the_gap_without_re_uploading(
    demo_client, demo_token
):
    """Reconciliation re-runs on read, so the feed catching up is reflected."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    card, statement = await _card_with_statement(demo_client, headers)
    await demo_client.put(
        f"/api/v1/accounts/card-statements/{statement}/lines",
        headers=headers,
        json={
            "lines": [
                {
                    "occurred_on": "2026-08-09",
                    "description": "LATE ARRIVAL",
                    "amount": {"amount_minor": -2_500, "currency": "USD"},
                }
            ]
        },
    )
    first = await demo_client.get(
        f"/api/v1/accounts/card-statements/{statement}/reconciliation", headers=headers
    )
    assert first.json()["missing_from_sync_count"] == 1

    # The feed finally delivers it.
    await demo_client.post(
        "/api/v1/transactions", headers=headers,
        json={
            "account_id": card,
            "occurred_at": "2026-08-09",
            "amount": {"amount_minor": -2_500, "currency": "USD"},
            "merchant": "LATE ARRIVAL",
        },
    )
    second = await demo_client.get(
        f"/api/v1/accounts/card-statements/{statement}/reconciliation", headers=headers
    )
    assert second.json()["missing_from_sync_count"] == 0
    assert second.json()["matched_count"] == 1


@pytest.mark.anyio
async def test_transactions_after_the_cycle_are_reported_not_treated_as_errors(
    demo_client, demo_token
):
    headers = {"Authorization": f"Bearer {demo_token}"}
    card, statement = await _card_with_statement(demo_client, headers)
    await demo_client.post(
        "/api/v1/transactions", headers=headers,
        json={
            "account_id": card,
            "occurred_at": "2026-09-02",
            "amount": {"amount_minor": -3_300, "currency": "USD"},
            "merchant": "POSTED AFTER CLOSE",
        },
    )
    result = await demo_client.put(
        f"/api/v1/accounts/card-statements/{statement}/lines",
        headers=headers,
        json={"lines": []},
    )
    body = result.json()
    assert body["not_on_statement_count"] == 1
    assert body["unaccounted"][0]["merchant"] == "POSTED AFTER CLOSE"

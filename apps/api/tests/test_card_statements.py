"""#11: a credit card's EXACT amount due, from its statement.

The synced running balance is not what is due — it includes spending that
posted after the cycle closed. These tests pin the two things that make the
feature trustworthy: the statement replaces (never adds to) the balance
estimate, and an unpaid statement claims cash on a known date.
"""

from datetime import date, timedelta

import pytest


async def _card(demo_client, headers, name="Visa") -> str:
    created = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": name, "type": "credit_card", "currency": "USD"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _record(demo_client, headers, account_id, *, minor, due, minimum=None):
    body = {
        "account_id": account_id,
        "statement_balance": {"amount_minor": minor, "currency": "USD"},
        "due_date": due.isoformat(),
    }
    if minimum is not None:
        body["minimum_due"] = {"amount_minor": minimum, "currency": "USD"}
    return await demo_client.post(
        "/api/v1/accounts/card-statements", headers=headers, json=body
    )


@pytest.mark.anyio
async def test_statement_round_trips_and_lists_newest_first(demo_client, demo_token):
    headers = {"Authorization": f"Bearer {demo_token}"}
    card = await _card(demo_client, headers)
    today = date.today()

    older = await _record(
        demo_client, headers, card, minor=100_00, due=today - timedelta(days=40)
    )
    newer = await _record(
        demo_client, headers, card, minor=250_00, due=today + timedelta(days=5),
        minimum=35_00,
    )
    assert older.status_code == 201 and newer.status_code == 201, newer.text
    assert newer.json()["minimum_due"]["amount_minor"] == 35_00
    assert newer.json()["account_name"] == "Visa"

    listed = await demo_client.get("/api/v1/accounts/card-statements", headers=headers)
    rows = listed.json()["statements"]
    assert [r["statement_balance"]["amount_minor"] for r in rows][:2] == [250_00, 100_00]


@pytest.mark.anyio
async def test_recording_the_same_cycle_updates_instead_of_stacking(
    demo_client, demo_token
):
    """Re-uploading a statement must not charge the household twice."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    card = await _card(demo_client, headers, "Amex")
    due = date.today() + timedelta(days=7)

    first = await _record(demo_client, headers, card, minor=400_00, due=due)
    second = await _record(demo_client, headers, card, minor=425_00, due=due)
    assert first.json()["id"] == second.json()["id"]

    listed = await demo_client.get(
        f"/api/v1/accounts/card-statements?account_id={card}", headers=headers
    )
    rows = listed.json()["statements"]
    assert len(rows) == 1
    assert rows[0]["statement_balance"]["amount_minor"] == 425_00


@pytest.mark.anyio
async def test_statement_replaces_the_balance_estimate_in_safe_to_spend(
    demo_client, demo_token, demo_engine
):
    """The headline risk: counting BOTH the statement and the running balance
    would reserve the same money twice."""
    from family_cfo_api import repository

    headers = {"Authorization": f"Bearer {demo_token}"}
    # Pay-in-full mode is what makes the running balance a committed figure.
    await demo_client.patch(
        "/api/v1/household", headers=headers, json={"credit_cards_paid_in_full": True}
    )
    card = await _card(demo_client, headers, "Sapphire")
    # The SYNCED balance is what safe-to-spend estimates from (a transaction
    # alone doesn't move the balance snapshot).
    repository.record_account_balance(demo_engine, card, -900_00)
    before = (await demo_client.get("/api/v1/household", headers=headers)).json()
    committed_before = before["safe_to_spend"]["committed_total"]["amount_minor"]

    # The statement says a SMALLER amount is actually due this cycle.
    await _record(
        demo_client, headers, card, minor=300_00, due=date.today() + timedelta(days=10)
    )
    after = (await demo_client.get("/api/v1/household", headers=headers)).json()
    committed_after = after["safe_to_spend"]["committed_total"]["amount_minor"]

    # Committed changes by the DIFFERENCE, not by the statement amount added on
    # top: the card contributes 300.00 instead of its 900.00 balance.
    assert committed_after == committed_before - 900_00 + 300_00


@pytest.mark.anyio
async def test_unpaid_statement_appears_in_the_timeline_as_exact(
    demo_client, demo_token
):
    headers = {"Authorization": f"Bearer {demo_token}"}
    card = await _card(demo_client, headers, "Timeline Card")
    due = date.today() + timedelta(days=6)
    await _record(demo_client, headers, card, minor=512_34, due=due)

    timeline = await demo_client.get("/api/v1/bills/timeline", headers=headers)
    assert timeline.status_code == 200, timeline.text
    row = next(i for i in timeline.json()["items"] if i["id"] == card)
    assert row["amount"]["amount_minor"] == 512_34
    assert row["due_date"] == due.isoformat()
    # Provenance so no UI can present an estimate as exact.
    assert row["source"] == "statement"
    assert row["statement_id"]


@pytest.mark.anyio
async def test_marking_paid_drops_the_claim_and_undo_restores_it(
    demo_client, demo_token
):
    headers = {"Authorization": f"Bearer {demo_token}"}
    card = await _card(demo_client, headers, "Paid Card")
    recorded = await _record(
        demo_client, headers, card, minor=200_00, due=date.today() + timedelta(days=3)
    )
    statement_id = recorded.json()["id"]

    paid = await demo_client.post(
        f"/api/v1/accounts/card-statements/{statement_id}/paid",
        headers=headers,
        json={"paid_at": date.today().isoformat()},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["paid_at"] == date.today().isoformat()

    # A paid cycle no longer claims cash.
    timeline = await demo_client.get("/api/v1/bills/timeline", headers=headers)
    row = next((i for i in timeline.json()["items"] if i["id"] == card), None)
    assert row is None or row["status"] == "paid"

    # ADR 0023: the mark is undoable, restoring the previous (unpaid) state.
    audit_rows = (await demo_client.get("/api/v1/audit", headers=headers)).json()["events"]
    event = next(a for a in audit_rows if a["action"] == "card_statement.paid")
    undone = await demo_client.post(
        f"/api/v1/audit/{event['id']}/undo", headers=headers
    )
    assert undone.status_code in (200, 204), undone.text
    listed = await demo_client.get(
        f"/api/v1/accounts/card-statements?account_id={card}", headers=headers
    )
    assert listed.json()["statements"][0]["paid_at"] is None


@pytest.mark.anyio
async def test_only_credit_cards_take_statements(demo_client, demo_token):
    headers = {"Authorization": f"Bearer {demo_token}"}
    checking = (await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Checking", "type": "checking", "currency": "USD"},
    )).json()["id"]
    rejected = await _record(
        demo_client, headers, checking, minor=100_00, due=date.today()
    )
    assert rejected.status_code == 422


def test_scan_parses_a_statement_and_degrades_honestly() -> None:
    """Candidates only — an unreadable statement must say so, never guess."""
    from family_cfo_api.api.accounts import parse_card_statement_scan

    good = parse_card_statement_scan(
        '{"statement_balance": 1234.56, "minimum_due": 35, '
        '"due_date": "2026-09-15", "period_start": "2026-08-01", '
        '"period_end": "2026-08-31"}'
    )
    assert good.statement_balance_minor == 123_456
    assert good.minimum_due_minor == 35_00
    assert good.due_date == date(2026, 9, 15)

    unreadable = parse_card_statement_scan("I could not read this image")
    assert unreadable.statement_balance_minor is None
    assert "manually" in unreadable.note

    # A zero balance is legitimate (card paid off), not a parse failure.
    zero = parse_card_statement_scan('{"statement_balance": 0, "due_date": "2026-09-15"}')
    assert zero.statement_balance_minor == 0


def test_scan_reads_the_transaction_table_in_ledger_signs() -> None:
    """#25: the rows are what reconciliation matches on, and the matcher
    compares SIGNS — a printed charge has to come back negative."""
    from family_cfo_api.api.accounts import parse_card_statement_scan

    read = parse_card_statement_scan(
        '{"statement_balance": 300.00, "due_date": "2026-09-15", '
        '"transactions": ['
        '{"date": "2026-08-03", "description": "BLUE BOTTLE COFFEE", "amount": -6.75},'
        '{"date": "08/14/2026", "description": "COSTCO WHSE #1102", "amount": "-$182.40"},'
        '{"date": "2026-08-20", "description": "PAYMENT THANK YOU", "amount": 250.00}]}'
    )
    assert read.statement_balance_minor == 300_00
    assert [(line.occurred_on, line.description, line.amount_minor) for line in read.lines] == [
        (date(2026, 8, 3), "BLUE BOTTLE COFFEE", -6_75),
        (date(2026, 8, 14), "COSTCO WHSE #1102", -182_40),
        (date(2026, 8, 20), "PAYMENT THANK YOU", 250_00),
    ]
    # Charges negative, payments positive — the ledger's own convention.
    assert sum(line.amount_minor for line in read.lines) == 60_85


def test_scan_skips_unreadable_rows_without_dropping_the_good_ones() -> None:
    """A row is skipped, never guessed at: no date, no amount, no description
    each mean the row cannot be matched, and inventing one would invent a
    charge. The rows around it must survive."""
    from family_cfo_api.api.accounts import parse_card_statement_scan

    read = parse_card_statement_scan(
        '{"statement_balance": 100.00, "transactions": ['
        '{"date": "2026-08-01", "description": "GOOD ONE", "amount": -10.00},'
        '{"description": "NO DATE PRINTED", "amount": -20.00},'
        '{"date": "08/14", "description": "NO YEAR PRINTED", "amount": -21.00},'
        '{"date": "2026-08-05", "description": "NO AMOUNT"},'
        '{"date": "2026-08-06", "description": "  ", "amount": -30.00},'
        '"not even a row",'
        '{"date": "2026-08-07", "description": "ALSO GOOD", "amount": -40.00}]}'
    )
    assert [line.description for line in read.lines] == ["GOOD ONE", "ALSO GOOD"]
    # The summary is untouched by a bad table.
    assert read.statement_balance_minor == 100_00


def test_scan_summary_still_works_when_there_is_no_table() -> None:
    """A summary-only statement (or a page whose table is unreadable) must keep
    working exactly as it did before #25 — lines are additive, not required."""
    from family_cfo_api.api.accounts import parse_card_statement_scan

    no_key = parse_card_statement_scan(
        '{"statement_balance": 1234.56, "due_date": "2026-09-15"}'
    )
    assert no_key.lines == []
    assert no_key.statement_balance_minor == 123_456

    wrong_shape = parse_card_statement_scan(
        '{"statement_balance": 1234.56, "transactions": "could not read the table"}'
    )
    assert wrong_shape.lines == []
    assert wrong_shape.statement_balance_minor == 123_456


@pytest.mark.anyio
async def test_scan_accumulates_lines_across_pages(demo_client, demo_token, monkeypatch):
    """#25: the transaction table runs past page 1. The scan must keep reading
    and ACCUMULATE, taking the summary from wherever it appeared."""
    import base64

    from family_cfo_api.api import accounts as accounts_api

    pages = [
        (
            '{"statement_balance": 300.00, "due_date": "2026-09-15", "transactions": ['
            '{"date": "2026-08-03", "description": "PAGE ONE", "amount": -6.75}]}'
        ),
        (
            '{"statement_balance": null, "transactions": ['
            '{"date": "2026-08-09", "description": "PAGE TWO", "amount": -12.00}]}'
        ),
        "the model gave up on this page",
    ]
    answers = iter(pages)

    class _Completion:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Describer:
        def complete(self, messages, **kwargs):
            return _Completion(next(answers))

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        accounts_api, "select_vision_describer", lambda *a, **k: (_Describer(), "test"),
        raising=False,
    )
    monkeypatch.setattr(
        "family_cfo_api.ai_runtime_selection.select_vision_describer",
        lambda *a, **k: (_Describer(), "test"),
    )
    monkeypatch.setattr(
        "family_cfo_api.api.income_analysis.pdf_page_pngs",
        lambda pdf_bytes, max_pages=3: [b"page"] * min(3, max_pages),
    )

    scanned = await demo_client.post(
        "/api/v1/accounts/card-statements/scan",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "image_base64": base64.b64encode(b"%PDF-1.4").decode("ascii"),
            "image_media_type": "application/pdf",
        },
    )
    assert scanned.status_code == 200, scanned.text
    body = scanned.json()
    assert body["statement_balance_minor"] == 300_00
    assert [line["description"] for line in body["lines"]] == ["PAGE ONE", "PAGE TWO"]


@pytest.mark.anyio
async def test_scan_stops_once_the_table_ends(demo_client, demo_token, monkeypatch):
    """#25: statements end in pages of disclosures. Once the summary is in hand,
    two consecutive pages with no rows mean the table is behind us — paying for
    more vision calls on fine print is waste."""
    from family_cfo_api import ai_runtime_selection

    calls = {"n": 0}

    class _Describer:
        def complete(self, messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                body = (
                    '{"statement_balance": 100.00, "due_date": "2026-09-15", '
                    '"transactions": [{"date": "2026-08-02", "description": "SHOP", '
                    '"amount": 25.00}]}'
                )
            else:
                body = '{"transactions": []}'  # disclosures, no rows

            class _C:
                text = body

            return _C()

        def close(self):
            pass

    # The endpoint imports both symbols INSIDE the function body, so they must
    # be patched on their source modules, not on the endpoint's module.
    from family_cfo_api.api import income_analysis

    monkeypatch.setattr(
        ai_runtime_selection, "select_vision_describer", lambda *a, **k: (_Describer(), "test")
    )
    monkeypatch.setattr(income_analysis, "pdf_page_pngs", lambda *a, **k: [b"p"] * 10)

    headers = {"Authorization": f"Bearer {demo_token}"}
    import base64

    response = await demo_client.post(
        "/api/v1/accounts/card-statements/scan",
        headers=headers,
        json={
            "image_base64": base64.b64encode(b"%PDF-1.4 fake").decode(),
            "image_media_type": "application/pdf",
        },
    )
    assert response.status_code == 200, response.text
    # Page 1 (summary + row) then two empty pages -> stop. Not all 10.
    assert calls["n"] == 3, f"scanned {calls['n']} pages; expected to stop at 3"
    assert len(response.json()["lines"]) == 1


@pytest.mark.anyio
async def test_outlook_says_which_card_amounts_came_from_a_statement(
    demo_client, demo_token, demo_engine
):
    """#30: the outlook projects card payments; a recorded statement makes one
    of them EXACT, and the outlook must say so rather than hedging that all
    card amounts are running balances."""
    from family_cfo_api import repository

    headers = {"Authorization": f"Bearer {demo_token}"}
    card = await _card(demo_client, headers, "Outlook Card")
    repository.record_account_balance(demo_engine, card, -400_00)
    await _record(
        demo_client, headers, card, minor=250_00, due=date.today() + timedelta(days=8)
    )

    response = await demo_client.get("/api/v1/overview/cash-outlook", headers=headers)
    assert response.status_code == 200, response.text
    outlook = response.json()
    card_events = [e for e in outlook["events"] if e["kind"] == "credit_card"]
    assert card_events, "expected the card to appear in the outlook"
    exact = [e for e in card_events if e["source"] == "statement"]
    assert exact, "the statement-backed payment should be marked exact"
    # And it carries the statement figure, not the running balance.
    assert abs(exact[0]["amount"]["amount_minor"]) == 250_00

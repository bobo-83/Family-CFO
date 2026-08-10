"""ADR 0073: an undo restores the state as it was immediately before the action.

The ADR's enforcement clause, applied to the six actions #72 found:

    capture the state, perform the action, undo it, and assert the state
    matches what was captured.

A test that only asserts "undo returned 200" is worthless here — every one of
these six returned 200 while restoring the wrong thing. So each test below
snapshots the *rows* (through the repository, not the response body), drives the
action and the undo through the HTTP API where the token is actually minted, and
compares. The footprint includes cascades: an earner's vest schedule, a
category's transactions and budget envelope, an account's balance history.
"""

from dataclasses import asdict
from datetime import date, timedelta

import pytest
from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, repository, undo_actions

HH = fixtures.DEMO_HOUSEHOLD_ID


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _latest_event(client, headers, action: str, exclude: set[str] | None = None) -> dict:
    """The newest audit event for an action (the log is newest-first).

    ``exclude`` skips ids seen earlier, for the cases that emit the same action
    twice and where the second one is the interesting one.
    """
    listed = await client.get("/api/v1/audit", headers=headers)
    assert listed.status_code == 200, listed.text
    events = listed.json()["events"]
    return next(
        e for e in events if e["action"] == action and e["id"] not in (exclude or set())
    )


async def _undo(client, headers, event: dict):
    response = await client.post(f"/api/v1/audit/{event['id']}/undo", headers=headers)
    assert response.status_code == 200, response.text
    return response


# --- 1. card_statement.recorded — an upsert, so its undo is not a delete ------


async def _card(client, headers, name: str = "Undo Card") -> str:
    created = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": name, "type": "credit_card", "currency": "USD"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _record_statement(client, headers, account_id, *, minor, due, minimum=None):
    body = {
        "account_id": account_id,
        "statement_balance": {"amount_minor": minor, "currency": "USD"},
        "due_date": due.isoformat(),
    }
    if minimum is not None:
        body["minimum_due"] = {"amount_minor": minimum, "currency": "USD"}
    response = await client.post(
        "/api/v1/accounts/card-statements", headers=headers, json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.anyio
async def test_undoing_a_statement_correction_restores_the_original_figures(
    demo_client, demo_engine: Engine, demo_token: str
) -> None:
    """#72's worst case: correcting a statement and pressing Undo used to DELETE
    it, taking the original figures with it — the undo button causing the data
    loss it exists to prevent."""
    headers = _headers(demo_token)
    card = await _card(demo_client, headers)
    due = date(2026, 3, 15)

    first = await _record_statement(
        demo_client, headers, card, minor=250_00, due=due, minimum=35_00
    )
    before = repository.get_card_statement(demo_engine, HH, first["id"])
    assert before is not None
    seen = {(await _latest_event(demo_client, headers, "card_statement.recorded"))["id"]}

    # The same cycle, corrected: an UPDATE in place, not a second statement.
    await _record_statement(
        demo_client, headers, card, minor=999_00, due=due, minimum=99_00
    )
    assert len(repository.list_card_statements(demo_engine, HH, account_id=card)) == 1

    await _undo(
        demo_client,
        headers,
        await _latest_event(demo_client, headers, "card_statement.recorded", seen),
    )

    after = repository.get_card_statement(demo_engine, HH, first["id"])
    assert after is not None, "the correction's undo deleted the statement"
    assert asdict(after) == asdict(before)


@pytest.mark.anyio
async def test_undoing_a_first_statement_still_removes_it(
    demo_client, demo_engine: Engine, demo_token: str
) -> None:
    """The other half of the upsert: when the write genuinely created the cycle,
    deleting it IS the restored state."""
    headers = _headers(demo_token)
    card = await _card(demo_client, headers, name="Undo Card First")

    statement = await _record_statement(
        demo_client, headers, card, minor=120_00, due=date(2026, 4, 20)
    )
    await _undo(
        demo_client, headers, await _latest_event(demo_client, headers, "card_statement.recorded")
    )

    assert repository.get_card_statement(demo_engine, HH, statement["id"]) is None


# --- 2. income_profile.deleted — the cascade is the point --------------------


@pytest.mark.anyio
async def test_undoing_an_earner_delete_restores_the_rsu_schedule(
    demo_client, demo_engine: Engine, demo_token: str
) -> None:
    """Deleting an earner cascade-deletes every RSU grant and vest event they
    own. The undo used to recreate the profile alone, under a new id, and report
    success while every vest date stayed gone."""
    headers = _headers(demo_token)
    created = await demo_client.post(
        "/api/v1/income/profile/earners",
        headers=headers,
        json={
            "label": "Undo Earner",
            "base_salary_minor": 120_000_00,
            "retirement_contribution_annual_minor": 20_000_00,
            "hsa_contribution_annual_minor": 4_000_00,
        },
    )
    assert created.status_code == 201, created.text
    earner_id = created.json()["id"]
    grant = await demo_client.post(
        "/api/v1/income/rsu-grants",
        headers=headers,
        json={
            "earner_id": earner_id,
            "ticker": "ACME",
            "units": 400,
            "grant_date": "2026-01-15",
            "vest_years": 2,
            "frequency": "quarterly",
        },
    )
    assert grant.status_code == 201, grant.text

    profiles_before = [p for p in repository.list_income_profiles(demo_engine, HH)]
    grants_before = repository.list_rsu_grants(demo_engine, HH)
    events_before = repository.list_rsu_vest_events(demo_engine, HH)
    assert any(g.income_profile_id == earner_id for g in grants_before)
    assert events_before

    deleted = await demo_client.delete(
        f"/api/v1/income/profile/earners/{earner_id}", headers=headers
    )
    assert deleted.status_code == 204, deleted.text
    assert not repository.list_rsu_grants(demo_engine, HH)
    assert not repository.list_rsu_vest_events(demo_engine, HH)

    await _undo(
        demo_client, headers, await _latest_event(demo_client, headers, "income_profile.deleted")
    )

    # Same earner id — the grants point back at it, and so does anything else
    # the household recorded against that earner.
    assert [asdict(p) for p in repository.list_income_profiles(demo_engine, HH)] == [
        asdict(p) for p in profiles_before
    ]
    assert [asdict(g) for g in repository.list_rsu_grants(demo_engine, HH)] == [
        asdict(g) for g in grants_before
    ]
    restored_events = repository.list_rsu_vest_events(demo_engine, HH)
    assert [(e.grant_id, e.vest_date, e.units) for e in restored_events] == [
        (e.grant_id, e.vest_date, e.units) for e in events_before
    ]


# --- 3. category.deleted — the relationships, not just the name --------------


def _transaction(engine: Engine, account_id: str, merchant: str) -> str:
    return repository.create_transaction(
        engine, HH, account_id=account_id, occurred_at=date(2026, 2, 3),
        amount_minor=-4200, currency="USD", merchant=merchant,
        description=None, import_source=None, import_id=None, review_state="reviewed",
    )


@pytest.mark.anyio
async def test_undoing_a_category_delete_restores_its_transactions_and_envelope(
    demo_client, demo_engine: Engine, demo_token: str
) -> None:
    """The delete nulls `category_id` on every transaction filed under the
    category and removes its budget envelope. Restoring the name under a new id
    left the transactions uncategorised and the envelope gone."""
    headers = _headers(demo_token)
    account = repository.create_account(
        demo_engine, HH, name="Undo Cat Checking", account_type="checking", currency="USD"
    )
    category = repository.create_category(demo_engine, HH, "Undo Groceries")
    filed = [_transaction(demo_engine, account.id, f"Shop {i}") for i in range(3)]
    repository.set_transactions_category(demo_engine, HH, filed, category.id)
    budget_id = repository.create_budget(
        demo_engine, HH, category_id=category.id, limit_minor=500_00, currency="USD"
    )
    budgets_before = [
        asdict(b) for b in repository.list_budgets(demo_engine, HH) if b.id == budget_id
    ]

    deleted = await demo_client.delete(
        f"/api/v1/categories/{category.id}", headers=headers
    )
    assert deleted.status_code == 204, deleted.text
    assert all(
        repository.get_transaction(demo_engine, HH, t).category_id is None for t in filed
    )
    assert not [b for b in repository.list_budgets(demo_engine, HH) if b.id == budget_id]

    await _undo(
        demo_client, headers, await _latest_event(demo_client, headers, "category.deleted")
    )

    restored = repository.get_category(demo_engine, HH, category.id)
    assert restored == category, "the category must come back under its own id"
    assert all(
        repository.get_transaction(demo_engine, HH, t).category_id == category.id
        for t in filed
    ), "the transactions were left uncategorised"
    assert [
        asdict(b) for b in repository.list_budgets(demo_engine, HH) if b.id == budget_id
    ] == budgets_before


@pytest.mark.anyio
async def test_a_category_with_too_many_transactions_is_honestly_irreversible(
    demo_client, demo_engine: Engine, demo_token: str, monkeypatch
) -> None:
    """#71/ADR 0073: above the bound the token refuses to snapshot rather than
    silently covering part of the change, and the event says it can't be undone
    instead of offering an Undo that would half-work."""
    monkeypatch.setattr(undo_actions, "SNAPSHOT_ROW_LIMIT", 2)
    headers = _headers(demo_token)
    account = repository.create_account(
        demo_engine, HH, name="Undo Bulk Checking", account_type="checking", currency="USD"
    )
    category = repository.create_category(demo_engine, HH, "Undo Bulk")
    filed = [_transaction(demo_engine, account.id, f"Bulk {i}") for i in range(3)]
    repository.set_transactions_category(demo_engine, HH, filed, category.id)

    deleted = await demo_client.delete(f"/api/v1/categories/{category.id}", headers=headers)
    assert deleted.status_code == 204, deleted.text

    event = await _latest_event(demo_client, headers, "category.deleted")
    assert event["undoable"] is False
    refused = await demo_client.post(f"/api/v1/audit/{event['id']}/undo", headers=headers)
    assert refused.status_code == 400
    assert "cannot be restored" in refused.json()["error"]["message"]


# --- 4. account.deleted — the balance history is part of the account ---------


@pytest.mark.anyio
async def test_undoing_an_account_delete_restores_its_balance_history(
    demo_client, demo_engine: Engine, demo_token: str
) -> None:
    headers = _headers(demo_token)
    created = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Undo Savings", "type": "savings", "currency": "USD"},
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    for minor in (1_000_00, 1_250_00, 1_400_00):
        recorded = await demo_client.post(
            f"/api/v1/accounts/{account_id}/balances",
            headers=headers,
            json={"balance": {"amount_minor": minor, "currency": "USD"}},
        )
        assert recorded.status_code == 201, recorded.text
    patched = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"emergency_fund_percent": 40.0, "rsu_ready_to_sell": True},
    )
    assert patched.status_code == 200, patched.text

    account_before = repository.get_account(demo_engine, HH, account_id)
    history_before = repository.account_balance_history(demo_engine, account_id, 100)
    assert len(history_before) == 3

    deleted = await demo_client.delete(f"/api/v1/accounts/{account_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text

    await _undo(
        demo_client, headers, await _latest_event(demo_client, headers, "account.deleted")
    )

    assert asdict(repository.get_account(demo_engine, HH, account_id)) == asdict(
        account_before
    ), "the account must come back under its own id, designation included"
    assert repository.account_balance_history(demo_engine, account_id, 100) == history_before
    assert repository.get_latest_balance_minor(demo_engine, account_id) == 1_400_00


# --- 5. account.updated — including the fields a PATCH cannot un-set ---------


@pytest.mark.anyio
async def test_undoing_an_account_update_restores_every_field_it_wrote(
    demo_client, demo_engine: Engine, demo_token: str
) -> None:
    """`update_account` reads None as "leave unchanged", which is right for a
    PATCH and wrong for an undo: ADDING a due date or an emergency-fund
    designation to an account that had neither used to be unundoable."""
    headers = _headers(demo_token)
    created = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Undo Loan", "type": "auto_loan", "currency": "USD"},
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    before = repository.get_account(demo_engine, HH, account_id)
    assert before.next_payment_due_date is None
    assert before.emergency_fund_percent is None and before.emergency_fund_minor is None

    patched = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={
            "name": "Undo Loan Renamed",
            "annual_interest_rate": 6.5,
            "minimum_payment": {"amount_minor": 250_00, "currency": "USD"},
            "maturity_date": "2030-01-01",
            "next_payment_due_date": "2026-09-01",
            "emergency_fund_percent": 25.0,
            "rsu_ready_to_sell": True,
        },
    )
    assert patched.status_code == 200, patched.text
    assert repository.get_account(
        demo_engine, HH, account_id
    ).next_payment_due_date == date(2026, 9, 1)

    await _undo(
        demo_client, headers, await _latest_event(demo_client, headers, "account.updated")
    )

    assert asdict(repository.get_account(demo_engine, HH, account_id)) == asdict(before)


@pytest.mark.anyio
async def test_undoing_an_emergency_fund_change_restores_the_previous_one(
    demo_client, demo_engine: Engine, demo_token: str
) -> None:
    """The designation is percent XOR amount, so switching kinds has to switch
    back — not merge into an account carrying both."""
    headers = _headers(demo_token)
    created = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Undo Reserve", "type": "savings", "currency": "USD"},
    )
    account_id = created.json()["id"]
    await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"emergency_fund_percent": 30.0},
    )
    before = repository.get_account(demo_engine, HH, account_id)

    await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"emergency_fund_amount": {"amount_minor": 900_00, "currency": "USD"}},
    )
    await _undo(
        demo_client, headers, await _latest_event(demo_client, headers, "account.updated")
    )

    after = repository.get_account(demo_engine, HH, account_id)
    assert after.emergency_fund_percent == 30.0
    assert after.emergency_fund_minor is None
    assert asdict(after) == asdict(before)


# --- 6. household.updated — all five settings the PATCH can write ------------


@pytest.mark.anyio
async def test_undoing_a_household_update_restores_every_setting(
    demo_client, demo_engine: Engine, demo_token: str
) -> None:
    """`reserve_committed_savings` and `language` were audited as changed and
    silently not restored."""
    headers = _headers(demo_token)
    before = repository.get_household(demo_engine, HH)

    patched = await demo_client.patch(
        "/api/v1/household",
        headers=headers,
        json={
            "emergency_fund_target_months": 9.0,
            "credit_cards_paid_in_full": not before.credit_cards_paid_in_full,
            "reserve_committed_savings": not before.reserve_committed_savings,
            "language": "lt" if before.language != "lt" else "vi",
            "timezone": "Europe/Vilnius",
        },
    )
    assert patched.status_code == 200, patched.text

    await _undo(
        demo_client, headers, await _latest_event(demo_client, headers, "household.updated")
    )

    assert asdict(repository.get_household(demo_engine, HH)) == asdict(before)


@pytest.mark.anyio
async def test_undoing_a_first_language_choice_clears_it_again(
    demo_client, demo_engine: Engine, demo_token: str
) -> None:
    """Null language is a real state — "never chose one" — so choosing one for
    the first time has to be reversible to null, not to a look-alike default."""
    headers = _headers(demo_token)
    repository.set_household_language(demo_engine, HH, None)
    assert repository.get_household(demo_engine, HH).language is None

    await demo_client.patch("/api/v1/household", headers=headers, json={"language": "vi"})
    assert repository.get_household(demo_engine, HH).language == "vi"

    await _undo(
        demo_client, headers, await _latest_event(demo_client, headers, "household.updated")
    )

    assert repository.get_household(demo_engine, HH).language is None


# --- the shape the six shared: a token that captures less than the change ----


@pytest.mark.anyio
async def test_an_upsert_never_mints_a_delete_token(
    demo_client, demo_engine: Engine, demo_token: str
) -> None:
    """The defect behind #72's worst case, pinned directly: a write that can
    update an existing row must carry what it overwrote (ADR 0073)."""
    headers = _headers(demo_token)
    card = await _card(demo_client, headers, name="Undo Token Shape")
    due = date.today() + timedelta(days=10)

    await _record_statement(demo_client, headers, card, minor=10_00, due=due)
    first = await _latest_event(demo_client, headers, "card_statement.recorded")
    await _record_statement(demo_client, headers, card, minor=20_00, due=due)
    second = await _latest_event(demo_client, headers, "card_statement.recorded", {first["id"]})

    record = repository.get_audit_event(demo_engine, HH, second["id"])
    assert '"op": "restore"' in record.undo_token
    assert '"op": "delete"' not in record.undo_token

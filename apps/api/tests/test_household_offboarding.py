"""#189: export (the family's data, portable) and deletion (the operator's
exit door) — with the isolation and refusal edges that make both safe."""

import csv
import io
import json
import zipfile

import pytest
from sqlalchemy import select

from family_cfo_api import models, repository


async def _create_hosted(demo_client, headers, name="Cedar family"):
    created = await demo_client.post(
        "/api/v1/households/hosted",
        headers=headers,
        json={
            "display_name": name,
            "base_currency": "USD",
            "owner_email": f"{name.split()[0].lower()}@example.test",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.mark.anyio
async def test_export_contains_the_ledger_and_history(demo_client, demo_token, demo_engine) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    hh = repository.list_households(demo_engine)[0]
    repository.upsert_household_memory(demo_engine, hh, "pet", "a golden retriever")

    exported = await demo_client.get("/api/v1/household/export", headers=headers)
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"

    bundle = zipfile.ZipFile(io.BytesIO(exported.content))
    names = set(bundle.namelist())
    for expected in [
        "README.txt", "accounts.csv", "transactions.csv", "bills.csv",
        "income.csv", "goals.csv", "categories.csv", "conversations.json",
        "memories.json",
    ]:
        assert expected in names, expected

    transactions = list(csv.DictReader(io.TextIOWrapper(bundle.open("transactions.csv"))))
    assert len(transactions) > 0
    # Decrypted, human-readable values — not enc1: tokens.
    assert not any("enc1:" in (row["merchant"] or "") for row in transactions)
    assert all(row["amount_minor"].lstrip("-").isdigit() for row in transactions)

    memories = json.loads(bundle.read("memories.json"))
    assert any(m["value"] == "a golden retriever" for m in memories)


@pytest.mark.anyio
async def test_delete_hosted_household_cascades_and_spares_others(
    demo_client, demo_token, demo_engine
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    body = await _create_hosted(demo_client, headers, "Doomed family")
    hh = body["household"]["id"]

    # The joining owner adds some data of their own.
    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={"token": body["invite_token"], "password": "doomed-pw-12", "display_name": "Doom"},
    )
    owner_headers = {"Authorization": f"Bearer {accepted.json()['access_token']}"}
    account = await demo_client.post(
        "/api/v1/accounts",
        headers=owner_headers,
        json={"name": "Their checking", "type": "checking", "currency": "USD"},
    )
    assert account.status_code == 201

    resident_hh = repository.list_households(demo_engine)[0]
    resident_accounts_before = len(repository.list_account_balances(demo_engine, resident_hh))

    deleted = await demo_client.delete(
        f"/api/v1/households/hosted/{hh}", headers=headers
    )
    assert deleted.status_code == 204, deleted.text

    # Every trace gone: no rows in any household-keyed table, no user, no login.
    with demo_engine.connect() as conn:
        for table in models.metadata.sorted_tables:
            if "household_id" in table.c:
                remaining = conn.execute(
                    select(table).where(table.c.household_id == hh)
                ).first()
                assert remaining is None, table.name
    relogin = await demo_client.get("/api/v1/household", headers=owner_headers)
    assert relogin.status_code == 401

    # The resident household is untouched.
    assert (
        len(repository.list_account_balances(demo_engine, resident_hh))
        == resident_accounts_before
    )


@pytest.mark.anyio
async def test_delete_refusals(demo_client, demo_token, demo_viewer_token, demo_engine) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    own = repository.list_households(demo_engine)[0]

    self_delete = await demo_client.delete(
        f"/api/v1/households/hosted/{own}", headers=headers
    )
    assert self_delete.status_code == 409

    not_admin = await demo_client.delete(
        f"/api/v1/households/hosted/{own}",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
    )
    assert not_admin.status_code == 403

    missing = await demo_client.delete(
        "/api/v1/households/hosted/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert missing.status_code == 404

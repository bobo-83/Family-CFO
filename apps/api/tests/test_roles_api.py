"""ADR 0034: rights-based roles — presets, custom roles, and right-guarded
permissions replacing role-name checks."""

import pytest


async def _owner(demo_client, demo_token):
    return {"Authorization": f"Bearer {demo_token}"}


@pytest.mark.anyio
async def test_presets_are_seeded_and_admin_holds_every_right(demo_client, demo_token) -> None:
    listed = await demo_client.get(
        "/api/v1/household/roles", headers=await _owner(demo_client, demo_token)
    )
    assert listed.status_code == 200
    body = listed.json()
    by_name = {r["name"]: r for r in body["roles"]}
    assert {"Admin", "User", "Viewer", "Child"} <= set(by_name)
    assert all(by_name[n]["built_in"] for n in ("Admin", "User", "Viewer", "Child"))
    # Admin is the complete superset (the catalog itself).
    assert set(by_name["Admin"]["rights"]) == set(body["all_rights"])
    # User edits money but never the balance sheet or the machinery.
    user_rights = set(by_name["User"]["rights"])
    assert "budgets.manage" in user_rights and "bills.manage" in user_rights
    assert "accounts.manage" not in user_rights
    assert "backups.manage" not in user_rights
    assert "members.manage" not in user_rights


@pytest.mark.anyio
async def test_custom_role_lifecycle_and_assignment(demo_client, demo_token) -> None:
    owner = await _owner(demo_client, demo_token)
    created = await demo_client.post(
        "/api/v1/household/roles",
        headers=owner,
        json={"name": "Bills only", "rights": ["finances.view", "bills.manage"]},
    )
    assert created.status_code == 201
    role_id = created.json()["id"]

    # Assign it to a new member; that member can manage bills but nothing else.
    member = await demo_client.post(
        "/api/v1/household/members",
        headers=owner,
        json={
            "email": "billsonly@example.com",
            "password": "correcthorsebattery",
            "display_name": "Bills Person",
            "role_id": role_id,
        },
    )
    assert member.status_code == 201
    assert member.json()["role_name"] == "Bills only"

    login = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": "billsonly@example.com", "password": "correcthorsebattery"},
    )
    assert login.json()["rights"] == ["bills.manage", "finances.view"]
    token = {"Authorization": f"Bearer {login.json()['access_token']}"}

    ok = await demo_client.post(
        "/api/v1/bills",
        headers=token,
        json={
            "name": "Water", "amount": {"amount_minor": 4_000, "currency": "USD"},
            "frequency": "monthly", "next_due_date": "2026-08-01",
        },
    )
    assert ok.status_code == 201
    blocked = await demo_client.post(
        "/api/v1/budgets",
        headers=token,
        json={"category_id": "x", "limit": {"amount_minor": 1, "currency": "USD"}},
    )
    assert blocked.status_code == 403

    # In use -> can't delete; unassigned -> deletable.
    conflict = await demo_client.delete(f"/api/v1/household/roles/{role_id}", headers=owner)
    assert conflict.status_code == 409


@pytest.mark.anyio
async def test_builtin_roles_are_immutable(demo_client, demo_token) -> None:
    owner = await _owner(demo_client, demo_token)
    roles = (await demo_client.get("/api/v1/household/roles", headers=owner)).json()["roles"]
    admin = next(r for r in roles if r["name"] == "Admin")
    edited = await demo_client.patch(
        f"/api/v1/household/roles/{admin['id']}", headers=owner, json={"name": "Root"}
    )
    assert edited.status_code == 409
    deleted = await demo_client.delete(f"/api/v1/household/roles/{admin['id']}", headers=owner)
    assert deleted.status_code == 409


@pytest.mark.anyio
async def test_unknown_rights_are_rejected(demo_client, demo_token) -> None:
    created = await demo_client.post(
        "/api/v1/household/roles",
        headers=await _owner(demo_client, demo_token),
        json={"name": "Bad", "rights": ["not.a.right"]},
    )
    assert created.status_code == 422


@pytest.mark.anyio
async def test_login_and_pairing_carry_rights(demo_client, demo_token) -> None:
    """Clients gate screens with the rights in the session/credential."""
    from family_cfo_api import fixtures

    login = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": fixtures.DEMO_USER_PASSWORD},
    )
    body = login.json()
    assert body["role_name"] == "Admin"
    assert "backups.manage" in body["rights"]

    session = await demo_client.post(
        "/api/v1/pairing/sessions", headers={"Authorization": f"Bearer {demo_token}"}
    )
    confirmed = await demo_client.post(
        "/api/v1/pairing/confirm",
        json={
            "pairing_session_id": session.json()["id"],
            "device_name": "Rights Phone",
            "device_public_key": "k",
        },
    )
    credential = confirmed.json()
    assert credential["role_name"] == "Admin"
    assert "accounts.manage" in credential["rights"]


@pytest.mark.anyio
async def test_viewer_cannot_manage_roles(demo_client, demo_viewer_token) -> None:
    viewer = {"Authorization": f"Bearer {demo_viewer_token}"}
    assert (
        await demo_client.get("/api/v1/household/roles", headers=viewer)
    ).status_code == 403
    assert (
        await demo_client.post(
            "/api/v1/household/roles", headers=viewer,
            json={"name": "Sneaky", "rights": ["finances.view"]},
        )
    ).status_code == 403

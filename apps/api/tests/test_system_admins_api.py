import pytest
from sqlalchemy import select

from family_cfo_api import fixtures, models, repository, rights
from family_cfo_api.db import create_database_engine


def _fresh_seeded_engine():
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    fixtures.create_schema(engine)
    fixtures.seed_demo_household(engine)
    return engine


def test_demo_seed_grants_no_system_admin() -> None:
    # ADR 0065: the showcase's credentials are PUBLIC — the seed must leave the
    # roster empty (the test conftest grants it separately for the suite).
    engine = _fresh_seeded_engine()
    assert repository.count_system_admins(engine) == 0
    assert not repository.is_system_admin(engine, fixtures.DEMO_USER_ID)


def test_first_household_owner_becomes_system_admin() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    fixtures.create_schema(engine)
    first = repository.create_household_with_owner(
        engine,
        display_name="The Family",
        base_currency="USD",
        owner_email="owner@example.com",
        owner_password_hash="x",
        owner_display_name="Owner",
    )
    assert repository.is_system_admin(engine, first.user_id)

    # Only the FIRST bootstrap qualifies — later households don't sneak in.
    second = repository.create_household_with_owner(
        engine,
        display_name="Another",
        base_currency="USD",
        owner_email="other@example.com",
        owner_password_hash="x",
        owner_display_name="Other",
    )
    assert not repository.is_system_admin(engine, second.user_id)


def test_box_rights_come_only_from_the_roster() -> None:
    # A legacy household role carrying ai_runtime.manage grants NOTHING; the
    # roster grants it plus system.admin.
    engine = _fresh_seeded_engine()
    with engine.begin() as conn:
        role = conn.execute(
            select(models.roles.c.id, models.roles.c.rights_json).where(
                models.roles.c.household_id == fixtures.DEMO_HOUSEHOLD_ID,
                models.roles.c.name == "Admin",
            )
        ).first()
        conn.execute(
            models.roles.update()
            .where(models.roles.c.id == role.id)
            .values(rights_json=[*role.rights_json, rights.AI_RUNTIME_MANAGE])
        )
    from datetime import timedelta

    from family_cfo_api.repository import utcnow

    token_hash = "test-session-hash"
    repository.create_auth_session(
        engine,
        fixtures.DEMO_USER_ID,
        fixtures.DEMO_HOUSEHOLD_ID,
        token_hash,
        utcnow() + timedelta(hours=1),
    )
    context = repository.get_session_context(engine, token_hash)
    assert rights.AI_RUNTIME_MANAGE not in context.rights
    assert not context.is_system_admin

    repository.grant_system_admin(engine, fixtures.DEMO_USER_ID, None)
    context = repository.get_session_context(engine, token_hash)
    assert rights.AI_RUNTIME_MANAGE in context.rights
    assert rights.SYSTEM_ADMIN in context.rights
    assert context.is_system_admin


@pytest.mark.anyio
async def test_roster_management_grant_list_revoke(demo_client, demo_token, demo_engine) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    # The demo viewer exists as a second user; grant them by email.
    granted = await demo_client.post(
        "/api/v1/system/admins", headers=headers, json={"email": fixtures.DEMO_VIEWER_EMAIL}
    )
    assert granted.status_code == 201
    body = granted.json()
    assert body["email"] == fixtures.DEMO_VIEWER_EMAIL

    listing = await demo_client.get("/api/v1/system/admins", headers=headers)
    assert listing.status_code == 200
    emails = [a["email"] for a in listing.json()["admins"]]
    assert fixtures.DEMO_VIEWER_EMAIL in emails and len(emails) == 2

    again = await demo_client.post(
        "/api/v1/system/admins", headers=headers, json={"email": fixtures.DEMO_VIEWER_EMAIL}
    )
    assert again.status_code == 409

    unknown = await demo_client.post(
        "/api/v1/system/admins", headers=headers, json={"email": "nobody@example.com"}
    )
    assert unknown.status_code == 404

    revoked = await demo_client.delete(
        f"/api/v1/system/admins/{body['user_id']}", headers=headers
    )
    assert revoked.status_code == 204
    assert not repository.is_system_admin(demo_engine, body["user_id"])


@pytest.mark.anyio
async def test_the_last_system_admin_cannot_be_revoked(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    listing = await demo_client.get("/api/v1/system/admins", headers=headers)
    only = listing.json()["admins"][0]

    resp = await demo_client.delete(f"/api/v1/system/admins/{only['user_id']}", headers=headers)

    assert resp.status_code == 409


@pytest.mark.anyio
async def test_non_admin_cannot_manage_roster_or_swap(demo_client, demo_engine) -> None:
    # A user who is NOT on the roster — even a household Admin — gets 403 on
    # roster management and on the box-global runtime actions.
    repository.revoke_system_admin(demo_engine, fixtures.DEMO_USER_ID)
    login = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": fixtures.DEMO_USER_PASSWORD},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    roster = await demo_client.get("/api/v1/system/admins", headers=headers)
    assert roster.status_code == 403

    swap = await demo_client.post(
        "/api/v1/ai/runtime/apply",
        headers=headers,
        json={"main_model": "Qwen/Qwen2.5-7B-Instruct", "vision_model": None},
    )
    assert swap.status_code == 403

    # Read access to the runtime pages is untouched.
    status = await demo_client.get("/api/v1/ai/runtime/status", headers=headers)
    assert status.status_code == 200

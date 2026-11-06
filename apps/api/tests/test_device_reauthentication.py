import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import create_engine, delete, text, update

from family_cfo_api import device_reauthentication, fixtures, models, repository, security
from tests.test_migrations import _postgresql_test_engine

ROOT = "/api/v1/pairing"


def _proof(key, device_id, challenge):
    message = f"family-cfo/device-reauth/v1\n{device_id}\n{challenge}".encode()
    r, s = decode_dss_signature(key.sign(message, ec.ECDSA(hashes.SHA256())))
    return (
        base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode().rstrip("=")
    )


async def _enroll(client, key):
    point = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )[1:]
    response = await client.post(
        f"{ROOT}/login",
        json={
            "email": fixtures.DEMO_USER_EMAIL,
            "password": fixtures.DEMO_USER_PASSWORD,
            "device_name": "Synthetic iPhone",
            "device_public_key": base64.b64encode(point).decode(),
        },
    )
    assert response.status_code == 201
    return response.json()


async def _payload(client, device_id, key):
    result = await client.post(f"{ROOT}/reauthentication/challenge", json={"device_id": device_id})
    assert result.status_code == 201
    challenge = result.json()["challenge"]
    return {
        "device_id": device_id,
        "challenge": challenge,
        "email": fixtures.DEMO_USER_EMAIL,
        "password": fixtures.DEMO_USER_PASSWORD,
        "signature": _proof(key, device_id, challenge),
    }


@pytest.mark.anyio
@pytest.mark.parametrize("pruned", [False, True])
async def test_renews_same_device_after_expiry_or_pruning(demo_client, demo_engine, pruned):
    key = ec.generate_private_key(ec.SECP256R1())
    original = await _enroll(demo_client, key)
    old_hash = security.hash_token(original["access_token"])
    with demo_engine.begin() as conn:
        table = models.auth_sessions
        statement = (
            delete(table)
            if pruned
            else update(table).values(expires_at=repository.utcnow() - timedelta(seconds=1))
        )
        conn.execute(statement.where(table.c.token_hash == old_hash))

    payload = await _payload(demo_client, original["device_id"], key)
    result = await demo_client.post(f"{ROOT}/reauthentication", json=payload)
    assert result.status_code == 201, result.text
    credential = result.json()
    assert credential["device_id"] == original["device_id"]
    assert credential["access_token"] != original["access_token"]
    context = repository.get_session_context(
        demo_engine, security.hash_token(credential["access_token"])
    )
    assert context.device_id == original["device_id"]
    assert repository.get_session_context(demo_engine, old_hash) is None
    assert len(repository.list_paired_devices(demo_engine, context.household_id)) == 1
    assert (await demo_client.post(f"{ROOT}/reauthentication", json=payload)).status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", ["wrong-key", "wrong-password", "other-account", "expired", "unknown", "revoked"]
)
async def test_reauthentication_rejects_uniformly(demo_client, demo_engine, failure):
    key = ec.generate_private_key(ec.SECP256R1())
    original = await _enroll(demo_client, key)
    device_id = original["device_id"]
    requested_id = repository.new_id() if failure == "unknown" else device_id
    signing_key = ec.generate_private_key(ec.SECP256R1()) if failure == "wrong-key" else key
    payload = await _payload(demo_client, requested_id, signing_key)
    if failure == "wrong-password":
        payload["password"] = "synthetic-invalid-password"
    if failure == "other-account":
        payload["email"] = fixtures.DEMO_VIEWER_EMAIL
        payload["password"] = fixtures.DEMO_VIEWER_PASSWORD
    if failure == "expired":
        with demo_engine.begin() as conn:
            conn.execute(
                update(models.device_reauth_challenges).values(expires_at=repository.utcnow())
            )
    if failure == "revoked":
        assert repository.revoke_paired_device(demo_engine, original["household_id"], device_id)
    result = await demo_client.post(f"{ROOT}/reauthentication", json=payload)
    assert result.status_code == 401
    assert result.json()["error"]["message"] == "Unable to sign in with this device"
    if failure != "revoked":
        assert (
            repository.get_session_context(
                demo_engine, security.hash_token(original["access_token"])
            )
            is not None
        )


def test_capacity_counts_only_live_challenges(demo_engine, monkeypatch):
    monkeypatch.setattr(device_reauthentication, "CHALLENGE_CAPACITY", 1)
    assert device_reauthentication.issue_challenge(demo_engine, repository.new_id())
    assert device_reauthentication.issue_challenge(demo_engine, repository.new_id()) is None
    with demo_engine.begin() as conn:
        conn.execute(update(models.device_reauth_challenges).values(expires_at=repository.utcnow()))
    assert device_reauthentication.issue_challenge(demo_engine, repository.new_id())


@pytest.mark.anyio
async def test_malformed_completion_does_not_echo_credentials(demo_client):
    result = await demo_client.post(
        f"{ROOT}/reauthentication",
        json={
            "password": "synthetic-password-do-not-echo",
            "signature": "synthetic-proof-do-not-echo",
        },
    )
    assert result.status_code == 422
    assert "do-not-echo" not in result.text


def test_ios_wire_verification_vector():
    assert device_reauthentication.verify_proof(
        "axfR8uEsQkf4vOblY6RA8ncDfYEt6zOg9KE5RdiYwpZP40Li/hp/m47n60p8D54WK84zV2sxXs7LtkBoN79R9Q==",
        "00000000-0000-4000-8000-000000000164",
        "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        "k0W1YxjHtcknC23YxrA1h7_Vz6sSSud8QYL2O7M87g1XZERRTUVkwsVPezlVnR5QNFIFF_11z6LaW7F_8ws3mw",
    )


@pytest.fixture(params=["sqlite", "postgresql"])
def concurrent_engine(request, tmp_path):
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'reauth.db'}")
        try:
            models.metadata.create_all(engine)
            fixtures.seed_demo_household(engine)
            yield engine
        finally:
            engine.dispose()
        return
    admin = _postgresql_test_engine()
    schema = f"test_device_reauth_{uuid4().hex}"
    engine = create_engine(admin.url.update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        models.metadata.create_all(engine)
        fixtures.seed_demo_household(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.parametrize("competitor", ["same-challenge", "revoke"])
def test_concurrent_renewal_serializes_against_the_device(concurrent_engine, competitor):
    engine = concurrent_engine
    key = ec.generate_private_key(ec.SECP256R1())
    raw_public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )[1:]
    old_token = security.generate_access_token()
    old = repository.create_paired_device_with_session(
        engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        user_id=fixtures.DEMO_USER_ID,
        device_name="Synthetic concurrent device",
        device_public_key=base64.b64encode(raw_public).decode(),
        access_token=old_token,
        token_hash=security.hash_token(old_token),
        expires_at=repository.utcnow() + timedelta(days=1),
    )
    challenge = device_reauthentication.issue_challenge(engine, old.device_id)
    assert challenge
    signature = _proof(key, old.device_id, challenge)
    barrier = Barrier(2, timeout=10)

    def renew():
        barrier.wait()
        return device_reauthentication.replace_device_session(
            engine,
            device_id=old.device_id,
            user_id=fixtures.DEMO_USER_ID,
            challenge=challenge,
            signature=signature,
            ttl=timedelta(days=30),
        )

    def revoke():
        barrier.wait()
        return repository.revoke_paired_device(engine, old.household_id, old.device_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(renew)
        second = pool.submit(renew if competitor == "same-challenge" else revoke)
        result, other = first.result(timeout=15), second.result(timeout=15)

    assert repository.get_session_context(engine, security.hash_token(old_token)) is None
    if competitor == "same-challenge":
        assert (result is None) != (other is None)
        assert (
            repository.get_session_context(
                engine, security.hash_token((result or other).access_token)
            )
            is not None
        )
    else:
        assert other is True
        if result is not None:
            assert (
                repository.get_session_context(engine, security.hash_token(result.access_token))
                is None
            )


@pytest.mark.anyio
async def test_renewed_session_can_unlock_with_the_original_device_wrap(
    demo_client, demo_engine, monkeypatch
):
    from cryptography.fernet import Fernet

    from family_cfo_api import household_crypto
    from family_cfo_api.config import get_settings
    from tests._test_keys import unwrap_device_key

    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    try:
        household = fixtures.DEMO_HOUSEHOLD_ID
        ciphertext = household_crypto.encrypt_text(
            demo_engine, household, "Synthetic household fact"
        )
        key = ec.generate_private_key(ec.SECP256R1())
        credential = await _enroll(demo_client, key)
        device_id = credential["device_id"]
        original_wrap = household_crypto.device_wrap_json(demo_engine, household, device_id)
        assert original_wrap
        assert household_crypto.generate_recovery_key(demo_engine, household)
        assert household_crypto.seal_household(demo_engine, household) is None
        household_crypto.reset_cache_for_tests()

        payload = await _payload(demo_client, device_id, key)
        renewed = await demo_client.post(f"{ROOT}/reauthentication", json=payload)
        assert renewed.status_code == 201
        headers = {"Authorization": f"Bearer {renewed.json()['access_token']}"}
        wrapped = await demo_client.get("/api/v1/household/device-wrap", headers=headers)
        assert wrapped.status_code == 200
        assert wrapped.json()["wrap_json"] == original_wrap
        dek = unwrap_device_key(original_wrap, key).decode()
        unlocked = await demo_client.post(
            "/api/v1/household/key-session", headers=headers, json={"dek": dek}
        )
        assert unlocked.status_code == 200
        assert (
            household_crypto.decrypt_text(demo_engine, household, ciphertext)
            == "Synthetic household fact"
        )
    finally:
        get_settings.cache_clear()
        household_crypto.reset_cache_for_tests()


@pytest.mark.anyio
async def test_challenge_throttling_is_separate_from_account_login(demo_client, demo_app):
    from family_cfo_api.ratelimit import AuthRateLimiter

    demo_app.state.auth_rate_limiter = AuthRateLimiter(
        max_attempts=1, window_seconds=300, lockout_seconds=900
    )
    unknown_device = {"device_id": repository.new_id()}
    first = await demo_client.post(f"{ROOT}/reauthentication/challenge", json=unknown_device)
    assert first.status_code == 201
    blocked = await demo_client.post(f"{ROOT}/reauthentication/challenge", json=unknown_device)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    await _enroll(demo_client, ec.generate_private_key(ec.SECP256R1()))

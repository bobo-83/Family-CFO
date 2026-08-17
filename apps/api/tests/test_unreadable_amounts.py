"""#110: an amount that will not decrypt must refuse, not read as zero.

Text degrades honestly — the UI shows `[encrypted — key mismatch]` and nobody
mistakes it for content. An amount has no such tell: a damaged row that returns
0 is indistinguishable from a real zero, and flows into spending, cash flow and
safe-to-spend as one. During the August incident two amounts counted as zero for
six days and nothing said so. Refusing is the whole point of these tests.
"""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text as sql_text

from family_cfo_api import household_crypto, repository
from family_cfo_api.config import get_settings


@pytest.fixture
def _master_key(monkeypatch):
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()


def _damage_one_amount(engine, household_id: str) -> None:
    """Re-encrypt one transaction's amount under a key nobody holds — exactly
    what a process writing with a retired key left behind."""
    foreign = household_crypto._subkey_fernet(Fernet.generate_key(), b"rows")
    token = household_crypto.ENC_PREFIX + foreign.encrypt(b"1234").decode()
    with engine.begin() as conn:
        row_id = conn.execute(
            sql_text("select id from transactions where household_id = :h limit 1"),
            {"h": household_id},
        ).scalar_one()
        conn.execute(
            sql_text("update transactions set amount_minor = :a where id = :i"),
            {"a": token, "i": row_id},
        )


def test_an_unreadable_amount_refuses_instead_of_counting_as_zero(
    _master_key, demo_engine
) -> None:
    hh = repository.list_households(demo_engine)[0]
    _damage_one_amount(demo_engine, hh)

    with pytest.raises(household_crypto.SealedAmountUnreadableError) as raised:
        repository.list_transactions(demo_engine, hh)
    assert raised.value.household_id == hh
    assert raised.value.code == household_crypto.AMOUNT_UNREADABLE_CODE


def test_readable_amounts_are_unaffected(_master_key, demo_engine) -> None:
    """The refusal must be about damage, not about encryption being on."""
    hh = repository.list_households(demo_engine)[0]
    transactions = repository.list_transactions(demo_engine, hh)
    assert transactions
    assert all(isinstance(t.amount_minor, int) for t in transactions)


def test_unreadable_text_still_degrades_rather_than_refusing(
    _master_key, demo_engine
) -> None:
    """Deliberate asymmetry: text says it is broken and stays out of the way;
    only money refuses. Losing a merchant name should not take down a page."""
    hh = repository.list_households(demo_engine)[0]
    foreign = household_crypto._subkey_fernet(Fernet.generate_key(), b"rows")
    token = household_crypto.ENC_PREFIX + foreign.encrypt(b"a shop").decode()
    assert household_crypto.decrypt_text(demo_engine, hh, token) == "[encrypted — key mismatch]"


@pytest.mark.anyio
async def test_the_route_returns_409_with_a_code_the_client_can_act_on(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """409, not 423: signing in again cannot repair a damaged record, so the
    client must not offer that as the remedy."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    hh = repository.list_households(demo_engine)[0]
    _damage_one_amount(demo_engine, hh)

    response = await demo_client.get("/api/v1/transactions", headers=headers)
    assert response.status_code == 409, response.text
    body = response.json()["error"]
    assert body["code"] == household_crypto.AMOUNT_UNREADABLE_CODE
    assert "cannot be read" in body["message"]

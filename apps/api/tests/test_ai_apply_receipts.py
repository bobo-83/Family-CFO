"""Apply commit receipts (issue #170 Item 2, ADR 0081): the ONE transaction-
scoped household commit — initiating-household upsert that preserves
``cluster_enabled``, matching-household repoint, exactly-once receipt, and a
single deduplicated ``ai_runtime.model_applied`` audit. The SQLite suite proves
repository behavior; the PostgreSQL 17 variant runs migration 0099 for real and
proves the partial audit index under the production engine."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from family_cfo_api import fixtures, models, repository
from tests.test_migrations import _postgresql_required, _postgresql_test_engine, _run_alembic

PROVIDER = "vllm"
BASE_URL = "http://vllm:8000"
OTHER_BASE_URL = "http://ollama.local:11434"
OLD_MODEL = "Qwen/Old-Model"
NEW_MODEL = "Qwen/Qwen2.5-14B-Instruct"


def _seed_config(
    engine: Engine,
    household_id: str,
    *,
    base_url: str = BASE_URL,
    model: str = OLD_MODEL,
    enabled: bool = True,
    cluster_enabled: bool = True,
) -> None:
    repository.upsert_ai_runtime_config(
        engine,
        household_id=household_id,
        provider=PROVIDER,
        base_url=base_url,
        model=model,
        enabled=enabled,
        cluster_enabled=cluster_enabled,
    )


def _config_for(engine: Engine, household_id: str) -> dict:
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(models.ai_runtime_configs).where(
                    models.ai_runtime_configs.c.household_id == household_id
                )
            )
            .mappings()
            .first()
        )
    assert row is not None, f"no runtime config for household {household_id}"
    return dict(row)


def _configs(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(models.ai_runtime_configs)).mappings().all()
    return sorted((dict(row) for row in rows), key=lambda row: row["household_id"])


def _apply_audits(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(models.audit_events).where(
                    models.audit_events.c.entity_type == models.AI_RUNTIME_APPLY_JOB_ENTITY
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _commit(engine: Engine, **overrides):
    kwargs: dict = {
        "job_id": str(uuid4()),
        "runtime_key": "vllm+http://vllm:8000/v1",
        "plan_hash": "ab" * 32,
        "committed_model": NEW_MODEL,
        "provider": PROVIDER,
        "base_url": BASE_URL,
        "initiator_household_id": fixtures.DEMO_HOUSEHOLD_ID,
        "initiator_user_id": fixtures.DEMO_USER_ID,
        "source": "api",
    }
    kwargs.update(overrides)
    return repository.commit_ai_runtime_apply(engine, **kwargs)


def _second_household(engine: Engine, display_name: str, owner_email: str) -> str:
    result = repository.create_household_with_owner(
        engine,
        display_name=display_name,
        base_currency="USD",
        owner_email=owner_email,
        owner_password_hash="synthetic-not-a-password-hash",
        owner_display_name=f"{display_name} Owner",
    )
    return result.household_id


def test_commit_upserts_initiator_repoints_matches_and_audits_once(demo_engine: Engine) -> None:
    matching = _second_household(demo_engine, "Matching", "matching@family-cfo.local")
    unrelated = _second_household(demo_engine, "Unrelated", "unrelated@family-cfo.local")
    _seed_config(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, cluster_enabled=True)
    _seed_config(demo_engine, matching, cluster_enabled=False)
    _seed_config(demo_engine, unrelated, base_url=OTHER_BASE_URL)

    result = _commit(demo_engine)

    assert result.committed is True
    assert result.repointed_households == 2

    # The initiating household keeps its cluster decision (ADR 0071: a swap
    # never flips the toggle) and takes the committed model.
    initiator = _config_for(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert initiator["model"] == NEW_MODEL
    assert initiator["cluster_enabled"] is True
    assert initiator["enabled"] is True
    # A matching household is repointed without touching its own toggle.
    matched = _config_for(demo_engine, matching)
    assert matched["model"] == NEW_MODEL
    assert matched["cluster_enabled"] is False
    # A household on a different base_url is out of scope.
    assert _config_for(demo_engine, unrelated)["model"] == OLD_MODEL

    receipt = repository.get_ai_runtime_apply_receipt(demo_engine, result.receipt.job_id)
    assert receipt == result.receipt
    assert receipt is not None
    assert receipt.committed_model == NEW_MODEL
    assert receipt.source == "api"
    assert receipt.initiator_household_id == fixtures.DEMO_HOUSEHOLD_ID

    audits = _apply_audits(demo_engine)
    assert len(audits) == 1
    audit = audits[0]
    assert audit["action"] == "ai_runtime.model_applied"
    assert audit["entity_id"] == result.receipt.job_id
    # System-audit convention: a real affected household carries the row, and
    # the actor is None — never a fabricated end-user actor.
    assert audit["household_id"] == fixtures.DEMO_HOUSEHOLD_ID
    assert audit["actor_user_id"] is None


def test_replay_makes_no_writes(demo_engine: Engine) -> None:
    _seed_config(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, cluster_enabled=True)
    result = _commit(demo_engine)
    assert result.committed is True
    before = _configs(demo_engine)

    replay = _commit(
        demo_engine, job_id=result.receipt.job_id, committed_model="Qwen/Different-Model"
    )

    assert replay.committed is False
    assert replay.receipt == result.receipt  # the original receipt, not rewritten
    assert replay.repointed_households == 0
    # The replay's different model never lands: idempotent by receipt.
    assert _configs(demo_engine) == before
    assert len(_apply_audits(demo_engine)) == 1


def test_cli_origin_repoints_without_inventing_configs(demo_engine: Engine) -> None:
    first = _second_household(demo_engine, "First", "first@family-cfo.local")
    second = _second_household(demo_engine, "Second", "second@family-cfo.local")
    _seed_config(demo_engine, first)
    _seed_config(demo_engine, second)
    assert len(_configs(demo_engine)) == 2

    result = _commit(
        demo_engine, initiator_household_id=None, initiator_user_id=None, source="cli"
    )

    assert result.committed is True
    assert result.repointed_households == 2
    # No arbitrary initiating config is created for a CLI-origin swap.
    after = _configs(demo_engine)
    assert len(after) == 2
    assert all(row["model"] == NEW_MODEL for row in after)

    audits = _apply_audits(demo_engine)
    assert len(audits) == 1
    # Operational audit lands on one real affected household (banksync
    # scheduled-work convention), deterministic pick, actor None.
    assert audits[0]["household_id"] == min(first, second)
    assert audits[0]["actor_user_id"] is None


def test_deleted_initiator_is_tolerated(demo_engine: Engine) -> None:
    survivor = _second_household(demo_engine, "Survivor", "survivor@family-cfo.local")
    _seed_config(demo_engine, survivor)
    ghost_household = str(uuid4())
    ghost_user = str(uuid4())

    result = _commit(
        demo_engine,
        initiator_household_id=ghost_household,
        initiator_user_id=ghost_user,
    )

    assert result.committed is True
    # Opaque attribution is kept as given: no FK, no cascade, no invention.
    assert result.receipt.initiator_household_id == ghost_household
    assert result.receipt.initiator_user_id == ghost_user
    assert result.repointed_households == 1
    assert _config_for(demo_engine, survivor)["model"] == NEW_MODEL

    audits = _apply_audits(demo_engine)
    assert len(audits) == 1
    assert audits[0]["household_id"] == survivor
    assert audits[0]["actor_user_id"] is None


def test_initiator_without_existing_config_gets_one(demo_engine: Engine) -> None:
    result = _commit(demo_engine)

    assert result.committed is True
    created = _config_for(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert created["model"] == NEW_MODEL
    assert created["enabled"] is True
    # ADR 0071: an invented config never opts the household into clustering.
    assert created["cluster_enabled"] is False
    # The repoint sweeps ALL configs matching the managed provider/base_url
    # (plan L406) — the freshly created initiating config now matches too, so
    # it is counted; its model was already the committed model.
    assert result.repointed_households == 1
    audits = _apply_audits(demo_engine)
    assert len(audits) == 1
    assert audits[0]["household_id"] == fixtures.DEMO_HOUSEHOLD_ID


def test_invalid_source_is_rejected(demo_engine: Engine) -> None:
    with pytest.raises(ValueError, match="invalid apply source"):
        _commit(demo_engine, source="cron")
    assert repository.get_ai_runtime_apply_receipt(demo_engine, str(uuid4())) is None


def test_partial_audit_index_dedups_apply_outcomes_only(demo_engine: Engine) -> None:
    _seed_config(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    _commit(demo_engine)
    audit = _apply_audits(demo_engine)[0]
    now = datetime(2026, 11, 21, 12, tzinfo=UTC)

    # The same (action, entity_id) pair under a different entity type is a
    # different fact and must stay writable.
    with demo_engine.begin() as conn:
        conn.execute(
            insert(models.audit_events).values(
                id=str(uuid4()),
                household_id=fixtures.DEMO_HOUSEHOLD_ID,
                actor_user_id=None,
                action=audit["action"],
                entity_type="some_other_entity",
                entity_id=audit["entity_id"],
                summary="Synthetic non-apply outcome",
                created_at=now,
            )
        )

    # A second apply-outcome audit for the same job cannot be written: the
    # partial unique index is the dedup key reconciler races rely on.
    with pytest.raises(IntegrityError), demo_engine.begin() as conn:
        conn.execute(
            insert(models.audit_events).values(
                id=str(uuid4()),
                household_id=fixtures.DEMO_HOUSEHOLD_ID,
                actor_user_id=None,
                action=audit["action"],
                entity_type=models.AI_RUNTIME_APPLY_JOB_ENTITY,
                entity_id=audit["entity_id"],
                summary="Synthetic duplicate apply outcome",
                created_at=now,
            )
        )


def test_postgresql_17_0099_commit_replay_and_audit_dedup() -> None:
    admin_engine = _postgresql_test_engine()
    schema_name = f"family_cfo_apply_receipts_{uuid4().hex}"
    schema_url = admin_engine.url.update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    )
    database_url = schema_url.render_as_string(hide_password=False)
    schema_created = False
    try:
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            schema_created = True
        except Exception as exc:  # noqa: BLE001 - required mode turns environment failure loud
            if _postgresql_required():
                pytest.fail(
                    f"required PostgreSQL test schema is unavailable: {type(exc).__name__}"
                )
            pytest.skip(f"PostgreSQL test schema is unavailable: {type(exc).__name__}")

        upgraded = _run_alembic("upgrade", "head", database_url=database_url)
        assert upgraded.returncode == 0, upgraded.stderr

        engine = create_engine(database_url)
        try:
            first = repository.create_household_with_owner(
                engine,
                display_name="PG Apply First",
                base_currency="USD",
                owner_email="pg-first@family-cfo.local",
                owner_password_hash="synthetic-not-a-password-hash",
                owner_display_name="PG First Owner",
            )
            second = repository.create_household_with_owner(
                engine,
                display_name="PG Apply Second",
                base_currency="USD",
                owner_email="pg-second@family-cfo.local",
                owner_password_hash="synthetic-not-a-password-hash",
                owner_display_name="PG Second Owner",
            )
            _seed_config(engine, first.household_id, cluster_enabled=True)
            _seed_config(engine, second.household_id, cluster_enabled=False)

            result = _commit(engine)
            assert result.committed is True
            assert result.repointed_households == 2
            assert _config_for(engine, first.household_id)["cluster_enabled"] is True
            assert _config_for(engine, second.household_id)["cluster_enabled"] is False

            replay = _commit(
                engine, job_id=result.receipt.job_id, committed_model="Qwen/Different-Model"
            )
            assert replay.committed is False
            assert replay.receipt == result.receipt
            assert len(_apply_audits(engine)) == 1

            audit = _apply_audits(engine)[0]
            # Deterministic pick: the lexicographically first affected household.
            assert audit["household_id"] == min(first.household_id, second.household_id)
            assert audit["actor_user_id"] is None
            now = datetime(2026, 11, 21, 12, tzinfo=UTC)
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(
                    insert(models.audit_events).values(
                        id=str(uuid4()),
                        household_id=first.household_id,
                        actor_user_id=None,
                        action=audit["action"],
                        entity_type=models.AI_RUNTIME_APPLY_JOB_ENTITY,
                        entity_id=audit["entity_id"],
                        summary="Synthetic duplicate apply outcome",
                        created_at=now,
                    )
                )
        finally:
            engine.dispose()
    finally:
        if schema_created:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()

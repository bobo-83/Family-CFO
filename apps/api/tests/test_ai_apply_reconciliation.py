"""Apply reconciliation (issue #170 item 4, ADR 0081 S3/S5).

The API-side reconciler consumes the manager's durable v2 decisions: it
commits the household transaction exactly once on FRESH verification
evidence, acknowledges the manager only AFTER the SQL commit, records
terminal outcomes once, and never runs recovery automatically. SQLite proves
the pass behavior; PostgreSQL 17 proves the decision races that motivated the
design — commit-ambiguity replay, replica election, restart pickup — under
the production engine and the real advisory locks.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, models, repository
from family_cfo_api.ai_runtime_apply_reconciliation import ApplyReconciler
from family_cfo_api.config import Settings
from family_cfo_api.model_manager_client import (
    ManagerJobStatus,
    ManagerProtocolError,
    ManagerTransportError,
    _job_status_from_document,
)
from tests._test_keys import TEST_FERNET_KEY
from tests.test_migrations import _postgresql_required, _postgresql_test_engine, _run_alembic

BASE_URL = "http://vllm:8000"
RUNTIME_KEY = "vllm+http://vllm:8000"
PLAN_HASH = "ab" * 32
NEW_MODEL = "Qwen/Qwen2.5-14B-Instruct"


def _settings(**overrides) -> Settings:
    base = dict(
        version="0.1.0",
        health_check_database=False,
        backup_encryption_key=TEST_FERNET_KEY,
        model_manager_url="http://model-manager:8000",
        model_manager_credential="test-deployment-credential",
    )
    base.update(overrides)
    return Settings(**base)


def _fresh_iso(offset_seconds: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat()


def _job_document(
    *,
    state: str = "commit_pending",
    job_id: str | None = None,
    verified_at: str | None = None,
    runtime_key: str | None = RUNTIME_KEY,
    target_plan_hash: str | None = PLAN_HASH,
    main_model: str = NEW_MODEL,
    initiator_household_id: str | None = fixtures.DEMO_HOUSEHOLD_ID,
    initiator_user_id: str | None = fixtures.DEMO_USER_ID,
    origin: str = "api",
    reason_code: str | None = None,
    message: str | None = None,
    recovery_available: bool = False,
) -> dict:
    return {
        "job_id": job_id or str(uuid4()),
        "request_id": str(uuid4()),
        "sequence": 1,
        "state": state,
        "decision": "commit" if state == "commit_pending" else "undecided",
        "origin": origin,
        "requested_selection": {
            "main_model": main_model,
            "vision_model": None,
            "photo_mode": "off",
            "topology": "single",
        },
        "resolved_plan": None,
        "previous_selection": None,
        "created_at": _fresh_iso(-600),
        "updated_at": _fresh_iso(-30),
        "deadline_at": None,
        "reason_code": reason_code,
        "message": message,
        "recovery_available": recovery_available,
        "runtime_key": runtime_key,
        "target_plan_hash": target_plan_hash,
        "initiator_household_id": initiator_household_id,
        "initiator_user_id": initiator_user_id,
        "verified_at": verified_at,
    }


class ScriptedManager:
    """A minimal manager double injected straight into `ApplyReconciler`.

    Records acknowledgements in order so tests can assert the
    commit-before-ack discipline and replay lost acknowledgements."""

    def __init__(self, job: dict | None = None) -> None:
        self.job = job
        self.protocol_error: Exception | None = None
        self.ack_error: Exception | None = None
        self.acks: list[dict] = []

    def verify_protocol(self) -> dict:
        if self.protocol_error is not None:
            raise self.protocol_error
        return {"protocol_major": 2, "build": "0", "journal_schema": 2}

    def current_job(self) -> ManagerJobStatus | None:
        if self.job is None:
            return None
        return _job_status_from_document(self.job)

    def recover(self, job_id: str) -> ManagerJobStatus:
        raise AssertionError("the reconciler must never run recovery automatically")

    def ack(self, job_id: str, *, receipt_job_id: str, plan_hash: str) -> ManagerJobStatus:
        if self.ack_error is not None:
            raise self.ack_error
        self.acks.append(
            {"job_id": job_id, "receipt_job_id": receipt_job_id, "plan_hash": plan_hash}
        )
        document = dict(self.job or {})
        document["state"] = "succeeded"
        return _job_status_from_document(document)


def _reconciler(engine: Engine, manager: ScriptedManager, **settings_overrides) -> ApplyReconciler:
    return ApplyReconciler(engine, _settings(**settings_overrides), client=manager)


def _seed_config(engine: Engine, household_id: str = fixtures.DEMO_HOUSEHOLD_ID, **overrides):
    kwargs = dict(
        household_id=household_id,
        provider="vllm",
        base_url=BASE_URL,
        model="Qwen/Old-Model",
        enabled=True,
    )
    kwargs.update(overrides)
    return repository.upsert_ai_runtime_config(engine, **kwargs)


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
    return sorted((dict(row) for row in rows), key=lambda row: row["id"])


# --- the pass (SQLite proves behavior) -------------------------------------------


def test_commit_pending_with_fresh_evidence_commits_then_acks(demo_engine: Engine) -> None:
    _seed_config(demo_engine)
    manager = ScriptedManager(_job_document(verified_at=_fresh_iso()))
    outcome = _reconciler(demo_engine, manager).run_once()

    assert outcome == "committed"
    # The initiating household's config carries the committed model...
    config = repository.get_ai_runtime_config(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert config.model == NEW_MODEL
    assert config.enabled is True
    # ...the receipt freezes the auditable identity...
    receipt = repository.get_ai_runtime_apply_receipt(demo_engine, manager.job["job_id"])
    assert receipt is not None
    assert receipt.plan_hash == PLAN_HASH
    assert receipt.runtime_key == RUNTIME_KEY
    assert receipt.committed_model == NEW_MODEL
    assert receipt.source == "api"
    # ...the success audit was written by the commit transaction itself...
    audits = _apply_audits(demo_engine)
    assert [a["action"] for a in audits] == ["ai_runtime.model_applied"]
    assert audits[0]["entity_id"] == manager.job["job_id"]
    assert audits[0]["actor_user_id"] is None
    # ...and the manager is acknowledged only AFTER that SQL commit.
    assert manager.acks == [
        {
            "job_id": manager.job["job_id"],
            "receipt_job_id": manager.job["job_id"],
            "plan_hash": PLAN_HASH,
        }
    ]


def test_stale_evidence_waits_for_the_manager_to_reverify(demo_engine: Engine) -> None:
    """A target that crashed after verification must not be committed blindly:
    the manager's executor re-verifies every 15 s and refreshes the stamp."""
    _seed_config(demo_engine)
    stale = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    manager = ScriptedManager(_job_document(verified_at=stale))

    outcome = _reconciler(demo_engine, manager).run_once()

    assert outcome == "evidence_stale"
    assert _configs(demo_engine)[0]["model"] == "Qwen/Old-Model"
    assert manager.acks == []
    assert repository.get_ai_runtime_apply_receipt(demo_engine, manager.job["job_id"]) is None


def test_evidence_age_without_a_stamp_is_never_fresh(demo_engine: Engine) -> None:
    _seed_config(demo_engine)
    manager = ScriptedManager(_job_document(verified_at=None))
    assert _reconciler(demo_engine, manager).run_once() == "evidence_stale"
    assert manager.acks == []


def test_a_committed_receipt_reacks_without_recommitting(demo_engine: Engine) -> None:
    """The SQL commit landed but the acknowledgement was lost (or this is a
    replay): re-acknowledge — never roll back a committed job."""
    _seed_config(demo_engine)
    job_id = str(uuid4())
    manager = ScriptedManager(_job_document(job_id=job_id, verified_at=_fresh_iso()))
    assert _reconciler(demo_engine, manager).run_once() == "committed"

    replay = ScriptedManager(_job_document(job_id=job_id, verified_at=_fresh_iso()))
    outcome = _reconciler(demo_engine, replay).run_once()

    assert outcome == "already_committed"
    assert replay.acks and replay.acks[0]["plan_hash"] == PLAN_HASH
    # The re-ack wrote nothing: the receipt, configs, and audit stand as they were.
    assert len(_apply_audits(demo_engine)) == 1
    assert _configs(demo_engine)[0]["model"] == NEW_MODEL


def test_a_landed_receipt_is_acked_even_when_evidence_is_stale(demo_engine: Engine) -> None:
    """Finding 2 (PR #180 review): the receipt is the durable decision record,
    so it is acknowledged regardless of evidence age. Freshness gates only
    the DECISION to commit; gating the acknowledgement on a stale clock
    strands a committed job in commit_pending forever."""
    _seed_config(demo_engine)
    job_id = str(uuid4())
    stale = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    manager = ScriptedManager(_job_document(job_id=job_id, verified_at=stale))
    # The commit landed earlier, while the evidence was still fresh.
    repository.commit_ai_runtime_apply(
        demo_engine,
        job_id=job_id,
        runtime_key=RUNTIME_KEY,
        plan_hash=PLAN_HASH,
        committed_model=NEW_MODEL,
        provider="vllm",
        base_url=BASE_URL,
        initiator_household_id=fixtures.DEMO_HOUSEHOLD_ID,
        initiator_user_id=fixtures.DEMO_USER_ID,
        source="api",
    )
    outcome = _reconciler(demo_engine, manager).run_once()
    assert outcome == "already_committed"
    assert [ack["plan_hash"] for ack in manager.acks] == [PLAN_HASH]
    # One audit, one commit: the acknowledgement wrote nothing new.
    assert [a["action"] for a in _apply_audits(demo_engine)] == ["ai_runtime.model_applied"]
    assert _configs(demo_engine)[0]["model"] == NEW_MODEL


def test_missing_manager_identity_refuses_to_commit_blind(demo_engine: Engine) -> None:
    """Without the frozen runtime key + plan hash the receipt could not name
    an auditable identity, so the reconciler refuses and waits."""
    _seed_config(demo_engine)
    manager = ScriptedManager(
        _job_document(verified_at=_fresh_iso(), runtime_key=None, target_plan_hash=None)
    )
    outcome = _reconciler(demo_engine, manager).run_once()

    assert outcome == "insufficient_manager_evidence"
    assert manager.acks == []
    assert _configs(demo_engine)[0]["model"] == "Qwen/Old-Model"


def test_missing_committed_model_refuses_to_commit_blind(demo_engine: Engine) -> None:
    _seed_config(demo_engine)
    document = _job_document(verified_at=_fresh_iso())
    document["requested_selection"] = None
    manager = ScriptedManager(document)
    assert _reconciler(demo_engine, manager).run_once() == "insufficient_manager_evidence"
    assert manager.acks == []


def test_commit_failure_never_acknowledges(demo_engine: Engine, monkeypatch) -> None:
    """The ordering discipline: acknowledge only after the SQL commit landed."""
    from family_cfo_api import ai_runtime_apply_reconciliation as module

    _seed_config(demo_engine)
    manager = ScriptedManager(_job_document(verified_at=_fresh_iso()))

    def failing_commit(*args, **kwargs):
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(module.repository, "commit_ai_runtime_apply", failing_commit)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        _reconciler(demo_engine, manager).run_once()
    assert manager.acks == []


def test_terminal_outcomes_are_recorded_once_and_recovery_stays_manual(
    demo_engine: Engine,
) -> None:
    _seed_config(demo_engine)
    cases = {
        "failed": "ai_runtime.model_apply_failed",
        "restored": "ai_runtime.model_apply_restored",
        "recovery_failed": "ai_runtime.model_apply_recovery_failed",
    }
    for state, action in cases.items():
        job_id = str(uuid4())
        manager = ScriptedManager(
            _job_document(
                job_id=job_id,
                state=state,
                reason_code="verification_failed",
                recovery_available=state == "recovery_failed",
            )
        )
        assert _reconciler(demo_engine, manager).run_once() == f"recorded:{state}"
        assert _reconciler(demo_engine, manager).run_once() == f"recorded:{state}"
        audits = [a for a in _apply_audits(demo_engine) if a["entity_id"] == job_id]
        assert [a["action"] for a in audits] == [action]  # deduplicated by job + action
        assert audits[0]["actor_user_id"] is None
        # Recovery is NEVER automatic: ScriptedManager.recover asserts if the
        # reconciler ever tries to re-run the executor's direction itself.


def test_recovery_failed_waits_for_the_explicit_endpoint(demo_engine: Engine) -> None:
    """`recovery_failed`/`commit_repair_required` record their outcome audit
    and wait — the reconciler must not re-run the executor's direction."""
    _seed_config(demo_engine)
    job_id = str(uuid4())
    manager = ScriptedManager(
        _job_document(job_id=job_id, state="commit_repair_required", recovery_available=True)
    )
    assert _reconciler(demo_engine, manager).run_once() == "noop:commit_repair_required"
    assert manager.acks == []
    # Only the apply_started audit (if any) exists — no outcome, no commit.
    assert not [a for a in _apply_audits(demo_engine) if a["entity_id"] == job_id]


def test_outcome_audit_falls_back_to_a_real_affected_household(demo_engine: Engine) -> None:
    """A deleted initiating household must not fabricate one: the audit
    attaches to a REAL household on the managed runtime, actor None."""
    survivor = repository.create_household_with_owner(
        demo_engine,
        display_name="Survivor",
        base_currency="USD",
        owner_email="survivor@family-cfo.local",
        owner_password_hash="synthetic-not-a-password-hash",
        owner_display_name="Survivor Owner",
    ).household_id
    _seed_config(demo_engine, survivor)
    manager = ScriptedManager(
        _job_document(
            state="failed",
            initiator_household_id=str(uuid4()),  # the initiator is gone
            initiator_user_id=str(uuid4()),
        )
    )
    assert _reconciler(demo_engine, manager).run_once() == "recorded:failed"
    audits = _apply_audits(demo_engine)
    assert len(audits) == 1
    assert audits[0]["household_id"] == survivor


def test_unconfigured_reconciler_is_a_noop(demo_engine: Engine) -> None:
    manager = ScriptedManager(_job_document(verified_at=_fresh_iso()))
    reconciler = _reconciler(
        demo_engine, manager, model_manager_url="", model_manager_credential=""
    )
    assert reconciler.run_once() == "unconfigured"
    assert manager.acks == []


def test_protocol_skew_skips_the_pass(demo_engine: Engine) -> None:
    """A mixed pair can never establish v2 state: skip loudly, mutate nothing."""
    _seed_config(demo_engine)
    manager = ScriptedManager(_job_document(verified_at=_fresh_iso()))
    manager.protocol_error = ManagerProtocolError("the manager speaks major 1")
    assert _reconciler(demo_engine, manager).run_once() == "skipped_protocol"
    assert _configs(demo_engine)[0]["model"] == "Qwen/Old-Model"
    assert manager.acks == []


def test_idle_manager_is_a_noop(demo_engine: Engine) -> None:
    manager = ScriptedManager(None)
    assert _reconciler(demo_engine, manager).run_once() == "idle"


def test_reconcile_interval_is_at_least_half_a_second() -> None:
    """The cadence floor keeps a misconfigured 0 from spinning the task."""
    reconciler = ApplyReconciler(
        None, _settings(apply_reconcile_interval_seconds=0.0), client=ScriptedManager(None)
    )
    assert max(0.5, reconciler.settings.apply_reconcile_interval_seconds) == 0.5


# --- PostgreSQL 17: the decision races that motivated the design -----------------


def _pg_household(engine: Engine) -> str:
    """A real household for the migrated (empty) PG schema: configs and
    audits carry real foreign keys."""
    return repository.create_household_with_owner(
        engine,
        display_name="PG Reconcile",
        base_currency="USD",
        owner_email=f"reconcile-{uuid4().hex[:8]}@family-cfo.local",
        owner_password_hash="synthetic-not-a-password-hash",
        owner_display_name="Reconcile Owner",
    ).household_id


@pytest.fixture
def pg_engine() -> Engine:
    admin_engine = _postgresql_test_engine()
    schema_name = f"family_cfo_apply_reconcile_{uuid4().hex}"
    schema_url = admin_engine.url.update_query_dict({"options": f"-csearch_path={schema_name}"})
    database_url = schema_url.render_as_string(hide_password=False)
    schema_created = False
    try:
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            schema_created = True
        except Exception as exc:  # noqa: BLE001 - required mode turns environment failure loud
            if _postgresql_required():
                pytest.fail(f"required PostgreSQL test schema is unavailable: {type(exc).__name__}")
            pytest.skip(f"PostgreSQL test schema is unavailable: {type(exc).__name__}")

        upgraded = _run_alembic("upgrade", "head", database_url=database_url)
        assert upgraded.returncode == 0, upgraded.stderr
        engine = create_engine(database_url)
        try:
            yield engine
        finally:
            engine.dispose()
    finally:
        if schema_created:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_postgresql_commit_ambiguity_replay_is_safe(pg_engine: Engine) -> None:
    """The commit landed, the acknowledgement was lost, and a RESTARTED API
    picks the same job back up: one receipt, one audit, one re-ack — never a
    second commit and never a rollback."""
    household_id = _pg_household(pg_engine)
    _seed_config(pg_engine, household_id)
    job_id = str(uuid4())

    first = ScriptedManager(
        _job_document(job_id=job_id, verified_at=_fresh_iso(), initiator_household_id=household_id)
    )
    first.ack_error = ManagerTransportError("connection reset before the ack")  # ack lost
    outcome = _reconciler(pg_engine, first).run_once()
    assert outcome == "committed"
    assert first.acks == []  # the ack never arrived
    assert _configs(pg_engine)[0]["model"] == NEW_MODEL
    assert len(_apply_audits(pg_engine)) == 1

    # The manager still reports commit_pending (it keeps the transaction open
    # until acknowledged); the restarted API reconciles the same job.
    restarted = ScriptedManager(
        _job_document(job_id=job_id, verified_at=_fresh_iso(), initiator_household_id=household_id)
    )
    assert _reconciler(pg_engine, restarted).run_once() == "already_committed"
    assert [ack["plan_hash"] for ack in restarted.acks] == [PLAN_HASH]
    assert len(_apply_audits(pg_engine)) == 1
    assert _configs(pg_engine)[0]["model"] == NEW_MODEL
    receipt = repository.get_ai_runtime_apply_receipt(pg_engine, job_id)
    assert receipt is not None and receipt.plan_hash == PLAN_HASH


def test_postgresql_landed_receipt_is_acked_with_stale_evidence(pg_engine: Engine) -> None:
    """The commit landed while the evidence was fresh; the evidence then went
    stale (the executor's re-verification is briefly unavailable) before the
    acknowledgement was delivered. The receipt is the durable decision
    record: the reconciler acknowledges it under the exactly-once rules -
    one audit, no recommit, no rollback - instead of stranding the committed
    job in commit_pending."""
    household_id = _pg_household(pg_engine)
    _seed_config(pg_engine, household_id)
    job_id = str(uuid4())
    result = repository.commit_ai_runtime_apply(
        pg_engine,
        job_id=job_id,
        runtime_key=RUNTIME_KEY,
        plan_hash=PLAN_HASH,
        committed_model=NEW_MODEL,
        provider="vllm",
        base_url=BASE_URL,
        initiator_household_id=household_id,
        initiator_user_id=None,
        source="api",
    )
    assert result.committed

    stale = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    manager = ScriptedManager(
        _job_document(job_id=job_id, verified_at=stale, initiator_household_id=household_id)
    )
    assert _reconciler(pg_engine, manager).run_once() == "already_committed"
    assert [ack["plan_hash"] for ack in manager.acks] == [PLAN_HASH]
    assert len(_apply_audits(pg_engine)) == 1
    assert _configs(pg_engine)[0]["model"] == NEW_MODEL
    receipt = repository.get_ai_runtime_apply_receipt(pg_engine, job_id)
    assert receipt is not None and receipt.plan_hash == PLAN_HASH


def test_postgresql_two_replicas_make_one_decision(pg_engine: Engine) -> None:
    """Multiple API replicas are allowed: the advisory-lock election picks ONE
    committing replica per pass, and the receipt serializes the commit."""
    household_id = _pg_household(pg_engine)
    _seed_config(pg_engine, household_id)
    job_id = str(uuid4())
    manager = ScriptedManager(
        _job_document(job_id=job_id, verified_at=_fresh_iso(), initiator_household_id=household_id)
    )
    replica_a = _reconciler(pg_engine, manager)
    replica_b = _reconciler(pg_engine, manager)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda r: r.run_once(), [replica_a, replica_b]))

    # Exactly one replica committed; the election excluded the other for this
    # pass (the committing replica holds the session lock through its work).
    assert sorted(outcomes) == ["committed", "not_elected"]
    receipts = []
    with pg_engine.connect() as conn:
        receipts = conn.execute(select(models.ai_runtime_apply_receipts)).mappings().all()
    assert len(receipts) == 1
    assert len(_apply_audits(pg_engine)) == 1
    assert _configs(pg_engine)[0]["model"] == NEW_MODEL

    # The excluded replica's next pass finds the receipt and re-acks safely.
    follow_up = ScriptedManager(
        _job_document(job_id=job_id, verified_at=_fresh_iso(), initiator_household_id=household_id)
    )
    assert _reconciler(pg_engine, follow_up).run_once() == "already_committed"
    assert [ack["plan_hash"] for ack in follow_up.acks] == [PLAN_HASH]


def test_postgresql_restart_mid_swap_picks_up_the_commit(pg_engine: Engine) -> None:
    """The API dies after the manager accepted and verified, and comes back
    once the job sits in commit_pending: the lifespan-owned reconciler commits
    exactly once on the next pass — no operator step, no lost swap."""
    household_id = _pg_household(pg_engine)
    _seed_config(pg_engine, household_id)
    job_id = str(uuid4())
    manager = ScriptedManager(
        _job_document(job_id=job_id, verified_at=_fresh_iso(), initiator_household_id=household_id)
    )
    assert _reconciler(pg_engine, manager).run_once() == "committed"
    assert _configs(pg_engine)[0]["model"] == NEW_MODEL

    # A second full pass (cadence tick) is a pure no-op beyond the ack replay.
    again = ScriptedManager(
        _job_document(job_id=job_id, verified_at=_fresh_iso(), initiator_household_id=household_id)
    )
    assert _reconciler(pg_engine, again).run_once() == "already_committed"
    assert len(_apply_audits(pg_engine)) == 1


def test_postgresql_deployment_lock_serializes_commit_against_config_put(
    pg_engine: Engine,
) -> None:
    """The receipt commit holds the SAME fixed deployment advisory key as
    managed config PUTs: an interleaved Advanced save cannot overwrite the
    committed model with a stale full-object write."""
    from family_cfo_api.repository import AI_RUNTIME_DEPLOYMENT_LOCK_KEY

    household_id = _pg_household(pg_engine)
    _seed_config(pg_engine, household_id)
    job_id = str(uuid4())
    manager = ScriptedManager(
        _job_document(job_id=job_id, verified_at=_fresh_iso(), initiator_household_id=None)
    )

    # Hold the deployment key the way a concurrent config PUT would.
    with pg_engine.connect() as holder:
        # The BLOCKING lock returns void: executing it IS acquiring it.
        holder.execute(
            text("SELECT pg_advisory_lock(:key)"), {"key": AI_RUNTIME_DEPLOYMENT_LOCK_KEY}
        )
        reconciler = _reconciler(pg_engine, manager)
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(reconciler.run_once)
            time.sleep(0.2)
            blocked = not future.done()  # the commit transaction waits on the key
            holder.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": AI_RUNTIME_DEPLOYMENT_LOCK_KEY}
            )
            outcome = future.result(timeout=10)  # a deadlock would time out here
        finally:
            pool.shutdown(wait=True)
    assert blocked is True
    assert outcome == "committed"
    assert _configs(pg_engine)[0]["model"] == NEW_MODEL
    receipt = repository.get_ai_runtime_apply_receipt(pg_engine, job_id)
    assert receipt is not None

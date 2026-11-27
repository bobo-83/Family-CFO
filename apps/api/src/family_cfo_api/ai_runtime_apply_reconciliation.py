"""Apply reconciliation: the API side of the decision/receipt ordering
(issue #170, ADR 0081 S3/S5).

Owned by the application lifespan — not by a page or a request — this task
runs once at startup and every few seconds (matching the client poll cadence).
It consumes the manager's durable v2 decisions:

- `commit_pending` jobs commit the household transaction exactly once via
  the receipt (`repository.commit_ai_runtime_apply`) and only on
  verification evidence no older than 30 s (the manager's executor keeps the
  evidence fresh while it waits); the manager is acknowledged only AFTER the
  SQL commit, and a lost acknowledgement is replayed safely because the
  receipt disambiguates every ambiguous outcome (ADR 0078 discipline).
- terminal jobs record their outcome audit once (`model_apply_failed`,
  `model_apply_restored`, `model_apply_recovery_failed`), deduplicated by
  job + action. A successful job's `model_applied` audit was written by the
  commit transaction itself.

Multiple API replicas are allowed: a PostgreSQL advisory lock elects the
committing replica for each pass, and the receipt's uniqueness serializes the
actual commit. Network calls happen outside SQL transactions. Recovery is
NEVER automatic: `recovery_failed`/`commit_repair_required` wait for the
explicit retry-recovery endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Engine

from family_cfo_api import audit, models, repository
from family_cfo_api.config import Settings
from family_cfo_api.model_manager_client import (
    ManagerJobError,
    ManagerJobStatus,
    ManagerProtocolError,
    ManagerTransportError,
    ModelManagerClient,
)

logger = logging.getLogger(__name__)

# Commit only on verification evidence this fresh; older evidence waits for
# the manager's executor to re-verify (a target that crashed and restarted
# after verification must not be committed blindly).
EVIDENCE_FRESHNESS_SECONDS = 30.0

# Elects the committing replica among API replicas; the commit itself
# serializes on the (different) fixed deployment key inside the receipt
# transaction, so the election only avoids duplicate manager work.
_ELECTION_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"family-cfo-ai-runtime-reconcile-election").digest()[:8],
    "big",
    signed=True,  # PostgreSQL advisory keys are SIGNED bigint; unsigned overflows.
)

# Terminal outcomes and their deduplicated operational audits. `succeeded`
# needs none: the commit transaction already wrote `ai_runtime.model_applied`.
_OUTCOME_ACTIONS = {
    "failed": "ai_runtime.model_apply_failed",
    "restored": "ai_runtime.model_apply_restored",
    "recovery_failed": "ai_runtime.model_apply_recovery_failed",
}


class ApplyReconciler:
    """One reconciler per API process, owned by the lifespan."""

    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        client: ModelManagerClient | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self._injected_client = client

    async def run_forever(self) -> None:
        """Run once immediately, then every cadence — until cancelled."""
        interval = max(0.5, self.settings.apply_reconcile_interval_seconds)
        while True:
            try:
                self.run_once()
            except Exception:  # a reconciliation pass must never kill the task
                logger.exception("apply reconciliation pass failed")
            await asyncio.sleep(interval)

    def run_once(self) -> str:
        """One pass. Returns a short outcome label for tests and logs."""
        if (
            not self.settings.model_manager_url
            or not self.settings.model_manager_credential.strip()
        ):
            return "unconfigured"
        client = self._client_for()
        try:
            client.verify_protocol()
        except ManagerProtocolError as exc:
            logger.warning("apply reconciliation skipped: %s", exc)
            return "skipped_protocol"
        except ManagerTransportError as exc:
            logger.warning("apply reconciliation skipped: %s", exc)
            return "skipped_unreachable"
        with self._election() as elected:
            if not elected:
                return "not_elected"
            return self._reconcile_current_job(client)

    # --- the pass -----------------------------------------------------------------

    def _reconcile_current_job(self, client: ModelManagerClient) -> str:
        try:
            status = client.current_job()
        except (ManagerTransportError, ManagerJobError) as exc:
            logger.warning("apply reconciliation could not read the current job: %s", exc)
            return "unreachable"
        if status is None or not status.job_id:
            return "idle"
        if status.state == "commit_pending":
            return self._commit_pending(client, status)
        action = _OUTCOME_ACTIONS.get(status.state)
        if action is not None:
            self._record_outcome(status, action)
            return f"recorded:{status.state}"
        # staging/launching/verifying/restoring: executor work in flight.
        # succeeded: committed and acknowledged. recovery_failed and
        # commit_repair_required: recovery is EXPLICIT (retry-recovery), never
        # automatic.
        return f"noop:{status.state}"

    def _commit_pending(self, client: ModelManagerClient, status: ManagerJobStatus) -> str:
        assert status.job_id is not None
        # The receipt IS the durable decision record: read it BEFORE evidence
        # freshness. Freshness gates only the DECISION to commit (a target
        # that crashed after verification must not be committed blindly); a
        # receipt that already landed is acknowledged regardless of evidence
        # age, or a stale-evidence job whose commit had already landed would
        # strand in commit_pending forever.
        existing = repository.get_ai_runtime_apply_receipt(self.engine, status.job_id)
        if existing is not None:
            # The SQL commit landed but the acknowledgement was lost (or this
            # is a replay): re-acknowledge — never roll back a committed job.
            self._acknowledge(client, status, existing.plan_hash)
            return "already_committed"
        age = status.evidence_age_seconds
        if age is None or age > EVIDENCE_FRESHNESS_SECONDS:
            logger.info(
                "apply %s: verification evidence is stale (age=%ss); waiting for the "
                "manager to re-verify before committing",
                status.job_id,
                None if age is None else round(age, 1),
            )
            return "evidence_stale"
        if not status.runtime_key or not status.target_plan_hash:
            # The manager must name the runtime key and the frozen plan hash:
            # without them the receipt could not freeze an auditable identity.
            logger.error(
                "apply %s: manager did not report the runtime key/plan hash; "
                "refusing to commit blind",
                status.job_id,
            )
            return "insufficient_manager_evidence"
        committed_model = (status.requested_selection or {}).get("main_model") or ""
        if not committed_model:
            logger.error(
                "apply %s: manager did not report the committed model; refusing to commit blind",
                status.job_id,
            )
            return "insufficient_manager_evidence"
        result = repository.commit_ai_runtime_apply(
            self.engine,
            job_id=status.job_id,
            runtime_key=status.runtime_key,
            plan_hash=status.target_plan_hash,
            committed_model=committed_model,
            provider="vllm",
            base_url=self.settings.ai_default_base_url,
            initiator_household_id=status.initiator_household_id,
            initiator_user_id=status.initiator_user_id,
            source=status.origin if status.origin in models.AI_RUNTIME_APPLY_SOURCES else "api",
        )
        if result.committed:
            logger.info(
                "apply %s committed: model=%s repointed=%d households",
                status.job_id,
                committed_model,
                result.repointed_households,
            )
        self._acknowledge(client, status, result.receipt.plan_hash)
        return "committed" if result.committed else "already_committed"

    def _acknowledge(
        self, client: ModelManagerClient, status: ManagerJobStatus, plan_hash: str
    ) -> None:
        """Acknowledge AFTER the SQL commit; a dropped ack retries next pass."""
        assert status.job_id is not None
        try:
            client.ack(status.job_id, receipt_job_id=status.job_id, plan_hash=plan_hash)
        except (ManagerTransportError, ManagerJobError) as exc:
            logger.warning(
                "apply %s acknowledgement failed (%s); the receipt makes the retry safe",
                status.job_id,
                exc,
            )

    def _record_outcome(self, status: ManagerJobStatus, action: str) -> None:
        assert status.job_id is not None
        household_id = self._outcome_household(status)
        if household_id is None:
            logger.warning(
                "apply %s ended as %s with no existing household to attach the "
                "operational audit to (attribution stays in the manager journal)",
                status.job_id,
                status.state,
            )
            return
        _write_apply_audit_once(
            self.engine,
            household_id,
            None,
            action,
            status.job_id,
            f"Model apply {status.state}: job={status.job_id} "
            f"reason={status.reason_code or 'none'}",
        )

    def _outcome_household(self, status: ManagerJobStatus) -> str | None:
        """The operational outcome audit attaches to a REAL household with
        actor None (the scheduled-work convention) — never a fabricated
        end-user actor."""
        initiator = status.initiator_household_id
        if initiator and repository.get_household(self.engine, initiator) is not None:
            return initiator
        return repository.first_ai_runtime_household(
            self.engine, provider="vllm", base_url=self.settings.ai_default_base_url
        )

    # --- election -----------------------------------------------------------------

    @contextmanager
    def _election(self) -> Iterator[bool]:
        """Elect the committing replica with a session-level advisory lock.

        SQLite (tests) has no advisory locks and one replica by construction:
        always elected."""
        if self.engine.dialect.name != "postgresql":
            yield True
            return
        with self.engine.connect() as conn:
            elected = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": _ELECTION_LOCK_KEY},
                ).scalar_one()
            )
            try:
                yield elected
            finally:
                if elected:
                    conn.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": _ELECTION_LOCK_KEY},
                    )

    def _client_for(self) -> ModelManagerClient:
        if self._injected_client is not None:
            return self._injected_client
        return ModelManagerClient(
            self.settings.model_manager_url,
            self.settings.model_manager_credential,
            apply_timeout_seconds=self.settings.model_manager_apply_timeout_seconds,
        )


def _write_apply_audit_once(
    engine: Engine,
    household_id: str,
    actor_user_id: str | None,
    action: str,
    job_id: str,
    summary: str,
) -> None:
    """One audit row per job + action (the 0099 partial unique index)."""
    from sqlalchemy.exc import IntegrityError

    try:
        audit.write_audit(
            engine,
            household_id,
            actor_user_id,
            action,
            models.AI_RUNTIME_APPLY_JOB_ENTITY,
            job_id,
            summary,
        )
    except IntegrityError:
        logger.info("apply audit %s for job %s already recorded", action, job_id)

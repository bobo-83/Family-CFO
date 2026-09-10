from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from functools import partial

import anyio
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import (
    audit,
    backup_processing,
    backup_recovery,
    backup_storage,
    banksync,
    repository,
    rights,
    smb_backup,
)
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_engine, require_right
from family_cfo_api.schemas import (
    BackupCapacityObservation,
    BackupConfig,
    BackupConfigUpdateRequest,
    BackupDestinationCheckRequest,
    BackupDestinationCheckResponse,
    BackupDestinationRecoveryStatus,
    BackupEncryptionKey,
    BackupJob,
    BackupJobListResponse,
    BackupRecoveryStatus,
    BackupRetentionPolicy,
    ErrorResponse,
    RemoteBackup,
    RemoteBackupListResponse,
    RemoteRestoreRequest,
)

router = APIRouter(tags=["Backups"])
logger = logging.getLogger(__name__)


def _restore_boundary(
    engine: Engine, household_id: str, snapshot_at: datetime | None, lead: str
) -> tuple[int | None, str]:
    """#62: measure the audit gap a restore is about to create, and phrase it.

    A restore replaces the WHOLE database, `audit_events` included, so every event
    recorded after the snapshot was taken is destroyed by it. We keep that wholesale
    replace (option 2 in #62) and state the gap instead of trying to carry rows
    across it: the count is taken here, BEFORE the replace, and written afterwards.

    Returns (discarded_count, summary). The count is None when the snapshot's own
    timestamp is unknown — the gap is then real but unmeasurable, and the summary
    says so rather than implying zero.
    """
    if snapshot_at is None:
        return None, (
            f"{lead} — the snapshot's date is unknown, so the audit events recorded "
            "since it was taken were discarded uncounted"
        )
    discarded = repository.count_audit_events_since(engine, household_id, snapshot_at)
    # SQLite hands back naive datetimes, Postgres tz-aware ones — both are UTC.
    aware = snapshot_at if snapshot_at.tzinfo else snapshot_at.replace(tzinfo=UTC)
    taken = aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return discarded, (
        f"{lead} — {discarded} audit event(s) recorded between the {taken} snapshot "
        "and this restore were discarded with it"
    )


def _actor_after_restore(engine: Engine, user_id: str, summary: str) -> tuple[str | None, str]:
    """#68: who the restore audit row can credit, now that the restore has happened.

    The row is written AFTER the database is replaced so it survives the restore it
    describes (#62) — but `audit_events.actor_user_id` is a foreign key to
    `users.id`, and a member who joined after the snapshot was taken has no row in
    it. Naming them fails the constraint and returns 500 for a restore that
    succeeded. So: drop the attribution, keep the record, and say in the summary
    why the actor is empty. The record is the part that matters; the attribution is
    the part the restore made unknowable.

    Checked, not caught: an `IntegrityError` rescue around the write would also
    swallow the genuine failures the #62 row exists to make loud.

    Returns (actor_user_id, summary) — the summary unchanged when the user is there.
    """
    if repository.user_exists(engine, user_id):
        return user_id, summary
    return None, (
        f"{summary}; restored by a member whose account is not present in this "
        "snapshot, so this row has no actor"
    )


def _to_schema(record: repository.BackupJobRecord) -> BackupJob:
    return BackupJob(
        id=record.id,
        status=record.status,
        size_bytes=record.size_bytes,
        error_message=record.error_message,
        started_at=record.started_at,
        completed_at=record.completed_at,
        pruned_at=record.pruned_at,
        created_at=record.created_at,
        remote_status=record.remote_status,
        remote_error=record.remote_error,
        app_version=record.app_version,
    )


def _policy_schema(policy, *, target_oldest_at: datetime | None) -> BackupRetentionPolicy:
    return BackupRetentionPolicy(
        mode=policy.mode.value,
        keep_all_days=policy.keep_all_days,
        daily_until_days=policy.daily_until_days,
        weekly_until_days=policy.weekly_until_days,
        target_oldest_at=target_oldest_at,
    )


def _policy_target(policy, as_of: datetime) -> datetime | None:
    return (
        as_of - timedelta(days=policy.weekly_until_days)
        if policy.weekly_until_days is not None
        else None
    )


def _capacity_schema(observation) -> BackupCapacityObservation:
    return BackupCapacityObservation(
        status=observation.status,
        total_bytes=observation.total_bytes,
        available_bytes=observation.available_bytes,
        reserve_bytes=observation.reserve_bytes,
        estimated_next_backup_bytes=observation.estimated_next_backup_bytes,
        can_accept_estimated_backup=observation.can_accept_estimated_backup,
        as_of=observation.as_of,
        reason_code=observation.reason_code,
        reason=observation.reason,
    )


def _destination_status_schema(
    snapshot: backup_recovery.DestinationRecoverySnapshot,
) -> BackupDestinationRecoveryStatus:
    return BackupDestinationRecoveryStatus(
        destination=snapshot.destination,
        configured=snapshot.configured,
        status=snapshot.status,
        coverage_status=snapshot.coverage_status,
        policy=_policy_schema(snapshot.policy, target_oldest_at=snapshot.target_oldest_at),
        retention_review_required=snapshot.retention_review_required,
        retention_activated_at=snapshot.retention_activated_at,
        pending_prune_count=snapshot.pending_prune_count,
        pending_prune_bytes=snapshot.pending_prune_bytes,
        visible_archive_count=snapshot.visible_archive_count,
        readable_archive_count=snapshot.readable_archive_count,
        probe_status=snapshot.probe_status,
        probed_archive_count=snapshot.probed_archive_count,
        oldest_readable_at=snapshot.oldest_readable_at,
        newest_readable_at=snapshot.newest_readable_at,
        oldest_timestamp_source=snapshot.oldest_timestamp_source,
        metadata_mismatch_count=snapshot.metadata_mismatch_count,
        protected_anomaly_count=snapshot.protected_anomaly_count,
        compatibility_unknown_count=snapshot.compatibility_unknown_count,
        known_incompatible_count=snapshot.known_incompatible_count,
        capacity=_capacity_schema(snapshot.capacity),
        reason_codes=list(snapshot.reason_codes),
        reason=snapshot.reason,
        as_of=snapshot.as_of,
        verification_scope=snapshot.verification_scope,
    )


def _recovery_status_schema(
    snapshot: backup_recovery.BackupRecoverySnapshot,
) -> BackupRecoveryStatus:
    return BackupRecoveryStatus(
        as_of=snapshot.as_of,
        overall_status=snapshot.overall_status,
        overall_oldest_readable_at=snapshot.overall_oldest_readable_at,
        overall_newest_readable_at=snapshot.overall_newest_readable_at,
        local=_destination_status_schema(snapshot.local),
        offbox=_destination_status_schema(snapshot.offbox),
        verification_scope=snapshot.verification_scope,
    )


def _load_backup_config(engine: Engine, settings: Settings) -> BackupConfig:
    as_of = datetime.now(UTC)
    stored = repository.get_backup_settings(engine, settings=settings, as_of=as_of)
    local_pending, offbox_pending = backup_recovery.calculate_pending_prunes(
        engine, settings, stored, as_of=as_of
    )
    jobs = repository.list_backup_jobs(engine)
    latest = next((job for job in jobs if job.status in ("completed", "failed")), None)
    shared_max = (
        stored.local_max_bytes if stored.local_max_bytes == stored.offbox_max_bytes else None
    )
    return BackupConfig(
        frequency=stored.frequency,
        smb_host=stored.smb_host,
        smb_share=stored.smb_share,
        smb_folder=stored.smb_folder,
        smb_username=stored.smb_username,
        smb_domain=stored.smb_domain,
        has_password=bool(stored.smb_password_encrypted),
        max_bytes=shared_max,
        local_retention=_policy_schema(
            stored.local_retention,
            target_oldest_at=_policy_target(stored.local_retention, as_of),
        ),
        offbox_retention=_policy_schema(
            stored.offbox_retention,
            target_oldest_at=_policy_target(stored.offbox_retention, as_of),
        ),
        local_max_bytes=stored.local_max_bytes,
        offbox_max_bytes=stored.offbox_max_bytes,
        local_min_free_bytes=stored.local_min_free_bytes,
        offbox_min_free_bytes=stored.offbox_min_free_bytes,
        legacy_conflict_detected=stored.legacy_conflict_detected,
        retention_review_required=stored.retention_review_required,
        retention_activated_at=stored.retention_activated_at,
        updated_at=stored.updated_at,
        local_pending_prune_count=local_pending.count,
        local_pending_prune_bytes=local_pending.size_bytes,
        offbox_pending_prune_count=offbox_pending.count,
        offbox_pending_prune_bytes=offbox_pending.size_bytes,
        latest=_to_schema(latest) if latest else None,
    )


def _changed_config_groups(
    before: repository.BackupSettingsRecord,
    after: repository.BackupSettingsRecord,
) -> list[str]:
    groups: list[str] = []
    if before.frequency != after.frequency:
        groups.append("cadence")
    if any(
        getattr(before, name) != getattr(after, name)
        for name in (
            "smb_host",
            "smb_share",
            "smb_folder",
            "smb_username",
            "smb_password_encrypted",
            "smb_domain",
        )
    ):
        groups.append("Synology destination")
    if before.local_retention != after.local_retention:
        groups.append("local retention")
    if before.offbox_retention != after.offbox_retention:
        groups.append("off-box retention")
    if any(
        getattr(before, name) != getattr(after, name)
        for name in (
            "local_max_bytes",
            "offbox_max_bytes",
            "local_min_free_bytes",
            "offbox_min_free_bytes",
        )
    ):
        groups.append("caps and reserves")
    if (
        before.retention_review_required != after.retention_review_required
        or before.retention_activated_at != after.retention_activated_at
    ):
        groups.append("retention activation")
    return groups


def _remote_capacity_response(
    engine: Engine,
    settings: Settings,
    target: smb_backup.SmbTarget,
    *,
    reserve_bytes: int,
    as_of: datetime | None = None,
):
    when = as_of or datetime.now(UTC)
    local = backup_storage.collect_local_inventory(engine, settings.backup_dir, as_of=when)
    estimate = backup_storage.estimate_next_backup_bytes(local) if local.available else None
    return backup_processing.query_remote_capacity_observation(
        target,
        reserve_bytes=reserve_bytes,
        estimate=estimate,
        as_of=when,
    )


@router.get(
    "/backups",
    operation_id="listBackups",
    response_model=BackupJobListResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="List backup jobs",
)
async def list_backups(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> BackupJobListResponse:
    records = repository.list_backup_jobs(engine)
    return BackupJobListResponse(backups=[_to_schema(record) for record in records])


# #181: minimum gap between on-demand backups per household — whole-box work
# (pg_dump + SMB push) that a stuck client or loop must not hammer.
_BACKUP_COOLDOWN_SECONDS = 60
_last_manual_backup: float | None = None
_manual_backup_cooldown_lock = threading.Lock()


def reset_backup_cooldown_for_tests() -> None:
    global _last_manual_backup
    with _manual_backup_cooldown_lock:
        _last_manual_backup = None


def _reserve_backup_cooldown() -> tuple[int | None, float | None]:
    import time

    global _last_manual_backup
    now = time.monotonic()
    with _manual_backup_cooldown_lock:
        if _last_manual_backup is not None and now - _last_manual_backup < _BACKUP_COOLDOWN_SECONDS:
            retry = int(_BACKUP_COOLDOWN_SECONDS - (now - _last_manual_backup)) + 1
            return retry, None
        _last_manual_backup = now
        return None, now


def _release_backup_cooldown(reservation: float | None) -> None:
    global _last_manual_backup
    if reservation is None:
        return
    with _manual_backup_cooldown_lock:
        if _last_manual_backup == reservation:
            _last_manual_backup = None


async def _run_sync(function, /, *args, **kwargs):
    """The worker thread—not the request waiter—owns any mutation lease."""
    return await anyio.to_thread.run_sync(
        partial(function, *args, **kwargs), abandon_on_cancel=False
    )


@router.post(
    "/backups",
    operation_id="createBackup",
    response_model=BackupJob,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {"description": "A backup operation is already in progress", "model": ErrorResponse},
        429: {"description": "A backup ran moments ago (cooldown)", "model": ErrorResponse},
    },
    summary="Create an on-demand encrypted backup",
)
async def create_backup(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupJob:
    # The anti-hammer rule is box-global; correctness exclusion is the
    # cross-process BackupOperationLock inside the synchronous lifecycle.
    retry_after, reservation = _reserve_backup_cooldown()
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail=(
                f"A backup just ran — the next one can start in {retry_after}s. "
                "Your data is already saved."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    def run() -> str:
        config = backup_processing.build_backup_execution_config(engine, settings)
        return backup_processing.run_backup_once(engine, config)

    try:
        backup_job_id = await _run_sync(run)
    except backup_processing.BackupOperationBusyError as exc:
        _release_backup_cooldown(reservation)
        raise HTTPException(
            status_code=409, detail="A backup operation is already in progress"
        ) from exc
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.created",
        "backup_job",
        backup_job_id,
        "Backup requested",
    )
    record = repository.get_backup_job(engine, backup_job_id)
    assert record is not None
    logger.info("backup requested backup_id=%s status=%s", record.id, record.status)
    return _to_schema(record)


# #75: this MUST stay declared above `POST /backups/{backup_id}/restore`. Starlette
# matches in declaration order, so with the parameterised route first this one is
# unreachable — every call lands there as backup_id="remote" and gets 404 "Backup not
# found". It stayed dead because it is the box-rebuild path: the restore you only
# reach for once the box is gone, which is far too late to find out.
@router.post(
    "/backups/remote/restore",
    operation_id="restoreRemoteBackup",
    response_model=BackupDestinationCheckResponse,
    responses={
        400: {"description": "Backup could not be restored", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Backup file not found on the share", "model": ErrorResponse},
        409: {
            "description": "Backup is incompatible or another backup operation is in progress",
            "model": ErrorResponse,
        },
    },
    summary="Restore from a backup file on the off-box share (destructive)",
)
async def restore_remote_backup(
    payload: RemoteRestoreRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupDestinationCheckResponse:
    config = backup_processing.build_backup_execution_config(engine, settings)
    target = config.smb_target
    if target is None:
        raise HTTPException(status_code=400, detail="No Synology backup destination is configured")
    # Guard against path traversal — only a bare filename from the share.
    filename = os.path.basename(payload.filename)
    if filename != payload.filename or not filename.endswith(".enc"):
        raise HTTPException(status_code=400, detail="Invalid backup filename")

    # Hold the global mutation lease across share selection/read and restore so
    # maintenance or explicit deletion cannot invalidate the selected source.
    try:
        discarded, restore_summary = await _run_sync(
            backup_processing.restore_remote_backup,
            engine,
            filename,
            config,
            before_restore=lambda snapshot_at: _restore_boundary(
                engine,
                session.household_id,
                snapshot_at,
                f"Restored from {filename}",
            ),
        )
    except smb_backup.SmbStorageError as exc:
        raise HTTPException(status_code=404, detail="Backup file not found on the share") from exc
    except backup_processing.BackupCompatibilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except backup_processing.BackupOperationBusyError as exc:
        raise HTTPException(
            status_code=409, detail="A backup operation is already in progress"
        ) from exc
    except (ValueError, backup_processing.BackupConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # #68: same as the on-box path — the acting member may not exist in the
    # database this archive just became.
    actor_id, restore_summary = _actor_after_restore(engine, session.user_id, restore_summary)
    audit.write_audit(
        engine,
        session.household_id,
        actor_id,
        "backup.restored_remote",
        "backup_file",
        os.path.splitext(filename)[0][:36],
        restore_summary,
    )
    logger.info(
        "backup restored from share filename=%s discarded_audit_events=%s", filename, discarded
    )
    capacity = await _run_sync(
        _remote_capacity_response,
        engine,
        settings,
        target,
        reserve_bytes=config.offbox_min_free_bytes,
    )
    return BackupDestinationCheckResponse(
        writable=True, reason=None, capacity=_capacity_schema(capacity)
    )


@router.post(
    "/backups/{backup_id}/restore",
    operation_id="restoreBackup",
    response_model=BackupJob,
    responses={
        400: {"description": "Backup is not restorable", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Backup not found", "model": ErrorResponse},
        409: {
            "description": "Backup is from a newer app version than this box",
            "model": ErrorResponse,
        },
    },
    summary="Restore the database and documents from a completed backup (destructive)",
)
async def restore_backup(
    backup_id: str,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupJob:
    record = repository.get_backup_job(engine, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Backup not found")

    # #62: count the audit events this restore is about to destroy BEFORE it runs.
    # `started_at` is the honest bound — the database dump is taken at the top of
    # the job, so anything logged from that moment on is absent from the snapshot.
    discarded, restore_summary = _restore_boundary(
        engine,
        session.household_id,
        record.started_at or record.created_at,
        "Backup restore executed",
    )

    try:
        config = backup_processing.build_backup_execution_config(engine, settings)
        await _run_sync(backup_processing.restore_backup, engine, backup_id, config)
    except backup_processing.BackupCompatibilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except backup_processing.BackupOperationBusyError as exc:
        raise HTTPException(
            status_code=409, detail="A backup operation is already in progress"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("backup restored backup_id=%s discarded_audit_events=%s", backup_id, discarded)
    updated = repository.get_backup_job(engine, backup_id)
    assert updated is not None
    # Written AFTER the replace, deliberately: a row written before it would be
    # wiped by the very restore it describes (#62). Which is also why the actor
    # has to be re-checked against the restored database (#68).
    actor_id, restore_summary = _actor_after_restore(engine, session.user_id, restore_summary)
    audit.write_audit(
        engine,
        session.household_id,
        actor_id,
        "backup.restored",
        "backup_job",
        backup_id,
        restore_summary,
    )
    return _to_schema(updated)


@router.get(
    "/backups/config",
    operation_id="getBackupConfig",
    response_model=BackupConfig,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Get the box-global backup configuration and pending retention preview",
)
async def get_backup_config(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupConfig:
    return await _run_sync(_load_backup_config, engine, settings)


@router.put(
    "/backups/config",
    operation_id="updateBackupConfig",
    response_model=BackupConfig,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {
            "description": "Configuration changed; reload and reconcile the draft",
            "model": ErrorResponse,
        },
        422: {"description": "Invalid backup configuration", "model": ErrorResponse},
    },
    summary="Update the box-global backup configuration",
)
async def update_backup_config(
    payload: BackupConfigUpdateRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupConfig:
    before = repository.get_backup_settings(engine, settings=settings)
    supplied = payload.model_fields_set
    patch: dict[str, object] = {}
    for name in (
        "frequency",
        "smb_host",
        "smb_share",
        "smb_folder",
        "smb_username",
        "smb_domain",
        "local_min_free_bytes",
        "offbox_min_free_bytes",
    ):
        if name in supplied:
            patch[name] = getattr(payload, name)

    if "smb_password" in supplied and payload.smb_password is not None:
        patch["smb_password_encrypted"] = (
            banksync.encrypt_credential(settings, payload.smb_password)
            if payload.smb_password
            else ""
        )

    new_cap_fields = {"local_max_bytes", "offbox_max_bytes"} & supplied
    if new_cap_fields:
        for name in new_cap_fields:
            patch[name] = getattr(payload, name)
    elif "max_bytes" in supplied:
        patch["local_max_bytes"] = payload.max_bytes
        patch["offbox_max_bytes"] = payload.max_bytes

    for destination in ("local", "offbox"):
        field = f"{destination}_retention"
        if field not in supplied:
            continue
        policy = getattr(payload, field)
        assert policy is not None
        patch.update(
            {
                f"{destination}_retention_mode": policy.mode,
                f"{destination}_keep_all_days": policy.keep_all_days,
                f"{destination}_daily_until_days": policy.daily_until_days,
                f"{destination}_weekly_until_days": policy.weekly_until_days,
            }
        )

    try:
        if payload.confirm_retention_policy:
            assert payload.expected_updated_at is not None
            after = repository.update_and_activate_backup_settings(
                engine,
                patch,
                expected_updated_at=payload.expected_updated_at,
            )
        else:
            after = repository.update_backup_settings(
                engine,
                patch,
                expected_updated_at=payload.expected_updated_at,
            )
    except repository.BackupSettingsConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Backup configuration changed. Reload it and reconcile your draft.",
        ) from exc
    except repository.BackupSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    changed = _changed_config_groups(before, after)
    summary = (
        "Backup configuration changed: " + ", ".join(changed)
        if changed
        else "Backup configuration reviewed with no value changes"
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.config_updated",
        "backup_settings",
        "global",
        summary,
    )
    return await _run_sync(_load_backup_config, engine, settings)


@router.get(
    "/backups/status",
    operation_id="getBackupRecoveryStatus",
    response_model=BackupRecoveryStatus,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Get qualified box-global backup recovery-candidate status",
)
async def get_backup_recovery_status(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupRecoveryStatus:
    snapshot = await _run_sync(backup_recovery.build_backup_recovery_snapshot, engine, settings)
    return _recovery_status_schema(snapshot)


@router.post(
    "/backups/destination-check",
    operation_id="checkBackupDestination",
    response_model=BackupDestinationCheckResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Test the Synology SMB connection can be written to",
)
async def check_backup_destination(
    payload: BackupDestinationCheckRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupDestinationCheckResponse:
    def check() -> BackupDestinationCheckResponse:
        as_of = datetime.now(UTC)
        stored = repository.get_backup_settings(engine, settings=settings, as_of=as_of)
        password = payload.smb_password
        if password is None and stored.smb_password_encrypted:
            try:
                password = banksync.decrypt_credential(settings, stored.smb_password_encrypted)
            except Exception:  # noqa: BLE001 - never expose credential failures
                password = None
        if not password:
            reason = "Enter the Synology password to test the connection."
            capacity = backup_storage.capacity_observation(
                total_bytes=None,
                available_bytes=None,
                reserve_bytes=stored.offbox_min_free_bytes,
                estimated_next_backup_bytes=None,
                as_of=as_of,
                unavailable=True,
                reason_code="credentials_required",
                reason=reason,
            )
            return BackupDestinationCheckResponse(
                writable=False, reason=reason, capacity=_capacity_schema(capacity)
            )
        target = smb_backup.SmbTarget(
            host=payload.smb_host,
            share=payload.smb_share,
            folder=payload.smb_folder,
            username=payload.smb_username,
            password=password,
            domain=payload.smb_domain,
            io_timeout_seconds=settings.backup_io_timeout_seconds,
        )
        writable, reason = smb_backup.verify(target)
        if writable:
            capacity = _remote_capacity_response(
                engine,
                settings,
                target,
                reserve_bytes=stored.offbox_min_free_bytes,
                as_of=as_of,
            )
        else:
            capacity = backup_storage.capacity_observation(
                total_bytes=None,
                available_bytes=None,
                reserve_bytes=stored.offbox_min_free_bytes,
                estimated_next_backup_bytes=None,
                as_of=as_of,
                unavailable=True,
                reason_code="destination_unwritable",
                reason=reason,
            )
        return BackupDestinationCheckResponse(
            writable=writable, reason=reason, capacity=_capacity_schema(capacity)
        )

    return await _run_sync(check)


@router.get(
    "/backups/remote",
    operation_id="listRemoteBackups",
    response_model=RemoteBackupListResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="List the .enc backups on the configured off-box share",
)
async def list_remote_backups(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> RemoteBackupListResponse:
    def load() -> RemoteBackupListResponse:
        as_of = datetime.now(UTC)
        stored = repository.get_backup_settings(engine, settings=settings, as_of=as_of)
        target, target_error = backup_recovery.resolve_smb_target(stored, settings)
        if target_error == "destination_not_configured":
            return RemoteBackupListResponse(
                backups=[],
                status="not_configured",
                as_of=as_of,
                reason="No off-box backup destination is configured.",
            )
        if target is None:
            return RemoteBackupListResponse(
                backups=[],
                status="unavailable",
                as_of=as_of,
                reason="The stored Synology credential could not be opened.",
            )
        try:
            inventory = smb_backup.list_inventory(target)
        except smb_backup.SmbInventoryError as exc:
            return RemoteBackupListResponse(
                backups=[], status="unavailable", as_of=as_of, reason=exc.reason
            )
        return RemoteBackupListResponse(
            backups=[
                RemoteBackup(
                    filename=item.filename,
                    size_bytes=item.size_bytes,
                    modified_at=item.modified_at,
                    app_version=item.app_version,
                )
                for item in inventory.items
                if item.readable_candidate
            ],
            status="available",
            as_of=as_of,
            reason=None,
        )

    return await _run_sync(load)


@router.get(
    "/backups/encryption-key",
    operation_id="getBackupEncryptionKey",
    response_model=BackupEncryptionKey,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Reveal the backup encryption key (owner only) so it can be stored safely",
)
async def get_backup_encryption_key(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupEncryptionKey:
    key = settings.backup_encryption_key
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.key_revealed",
        "household",
        session.household_id,
        "Backup encryption key revealed",
    )
    return BackupEncryptionKey(configured=bool(key), key=key)


@router.delete(
    "/backups/{backup_id}",
    operation_id="deleteBackup",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Backup not found", "model": ErrorResponse},
        409: {"description": "A backup operation is already in progress", "model": ErrorResponse},
    },
    summary="Delete an on-box backup",
)
async def delete_backup(
    backup_id: str,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    record = repository.get_backup_job(engine, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    config = backup_processing.build_backup_execution_config(engine, settings)
    try:
        await _run_sync(backup_processing.delete_local_backup, engine, backup_id, config)
    except backup_processing.BackupOperationBusyError as exc:
        raise HTTPException(
            status_code=409, detail="A backup operation is already in progress"
        ) from exc
    except (ValueError, backup_processing.UnsafeBackupPathError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.deleted",
        "backup_job",
        backup_id,
        "Deleted an on-box backup",
    )
    return Response(status_code=204)


@router.post(
    "/backups/remote/delete",
    operation_id="deleteRemoteBackup",
    response_model=BackupDestinationCheckResponse,
    responses={
        400: {"description": "No destination / invalid filename", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {"description": "A backup operation is already in progress", "model": ErrorResponse},
    },
    summary="Delete a backup file from the Synology share",
)
async def delete_remote_backup(
    payload: RemoteRestoreRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupDestinationCheckResponse:
    config = backup_processing.build_backup_execution_config(engine, settings)
    target = config.smb_target
    if target is None:
        raise HTTPException(status_code=400, detail="No Synology backup destination is configured")
    filename = os.path.basename(payload.filename)
    if filename != payload.filename or not filename.endswith(".enc"):
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    try:
        await _run_sync(backup_processing.delete_remote_backup, engine, filename, config)
    except backup_processing.BackupOperationBusyError as exc:
        raise HTTPException(
            status_code=409, detail="A backup operation is already in progress"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        reason = smb_backup._friendly(exc)
        capacity = backup_storage.capacity_observation(
            total_bytes=None,
            available_bytes=None,
            reserve_bytes=config.offbox_min_free_bytes,
            estimated_next_backup_bytes=None,
            as_of=datetime.now(UTC),
            unavailable=True,
            reason_code="destination_unwritable",
            reason=reason,
        )
        return BackupDestinationCheckResponse(
            writable=False, reason=reason, capacity=_capacity_schema(capacity)
        )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.deleted_remote",
        "backup_file",
        os.path.splitext(filename)[0][:36],
        f"Deleted {filename} from Synology",
    )
    capacity = await _run_sync(
        _remote_capacity_response,
        engine,
        settings,
        target,
        reserve_bytes=config.offbox_min_free_bytes,
    )
    return BackupDestinationCheckResponse(
        writable=True, reason=None, capacity=_capacity_schema(capacity)
    )

"""Read-only backup recovery-window derivation for the box-global API.

This module composes WI-4 inventory, probe, capacity, and pure retention seams.
It deliberately performs no reconciliation, journaling, pruning, decryption of
archives, or restore testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal

from sqlalchemy.engine import Engine

from family_cfo_api import backup_processing, banksync, repository, smb_backup
from family_cfo_api.backup_retention import (
    BackupInventoryItem,
    BackupTimestampSource,
    RetentionAction,
    RetentionMode,
    RetentionPlan,
    RetentionPolicy,
    RetentionReason,
    plan_retention,
)
from family_cfo_api.backup_storage import (
    CapacityObservation,
    capacity_observation,
    collect_local_inventory,
    estimate_next_backup_bytes,
    query_local_capacity,
)
from family_cfo_api.config import Settings

DestinationStatus = Literal[
    "not_configured", "empty", "healthy", "constrained", "degraded", "unavailable"
]
CoverageStatus = Literal[
    "not_applicable", "empty", "building", "met", "incomplete", "shortened", "unknown"
]
ProbeStatus = Literal["complete", "partial", "unavailable"]
OverallStatus = Literal["empty", "healthy", "constrained", "degraded", "unavailable"]

_PROTECTING_METADATA_CODES = frozenset(
    {"missing_storage_path", "missing_file", "unsafe_storage_path", "size_mismatch"}
)
_NON_PROTECTING_CODES = frozenset({"compatibility_unknown"})
_SHORTENING_ACTIONS = frozenset({"pruned", "explicit_deleted", "capacity_blocked"})
_SAFE_REASONS = {
    "destination_not_configured": "No off-box backup destination is configured.",
    "credentials_unavailable": "The stored Synology credential could not be opened.",
    "inventory_unavailable": "The Synology inventory is unavailable.",
    "local_inventory_unavailable": "The local backup inventory is unavailable.",
    "probe_partial": "Only part of the backup inventory could be read-probed.",
    "probe_unavailable": "Backup readability could not be established.",
    "retention_review_required": "The retention policy requires administrator review.",
    "remote_timestamp_fallback": "Some recovery dates use the Synology modified time.",
    "metadata_mismatch": "Backup metadata does not match visible storage.",
    "protected_anomaly": "Protected backup evidence requires review.",
    "known_incompatible": "A backup from a newer app version is protected.",
    "capacity_unknown": "Capacity could not be measured; backups will still be attempted.",
    "capacity_unavailable": "Destination capacity is unavailable.",
    "capacity_insufficient": "Storage limits cannot accept the estimated next backup.",
    "logical_cap_unsatisfied": "The newest backup alone exceeds the configured size limit.",
    "coverage_shortened": "Storage limits or deletion shortened the configured recovery window.",
    "coverage_incomplete": "Backup history does not yet reach the configured recovery target.",
    "retention_event_history_incomplete": "Recovery history is incomplete.",
    "unresolved_inventory_failure": "The latest inventory check failed.",
    "unresolved_prune_failure": "A retention deletion has not completed.",
    "coverage_unknown": "The configured recovery-window coverage cannot be determined.",
}
_REASON_ORDER = tuple(_SAFE_REASONS)


@dataclass(frozen=True, slots=True)
class PendingPruneSummary:
    count: int | None
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class DestinationRecoverySnapshot:
    destination: Literal["local", "offbox"]
    configured: bool
    status: DestinationStatus
    coverage_status: CoverageStatus
    policy: RetentionPolicy
    target_oldest_at: datetime | None
    retention_review_required: bool
    retention_activated_at: datetime | None
    pending_prune_count: int | None
    pending_prune_bytes: int | None
    visible_archive_count: int
    readable_archive_count: int | None
    probe_status: ProbeStatus
    probed_archive_count: int
    oldest_readable_at: datetime | None
    newest_readable_at: datetime | None
    oldest_timestamp_source: Literal["job_started_at", "remote_modified_at"] | None
    metadata_mismatch_count: int
    protected_anomaly_count: int
    compatibility_unknown_count: int
    known_incompatible_count: int
    capacity: CapacityObservation
    reason_codes: tuple[str, ...]
    reason: str | None
    as_of: datetime
    verification_scope: Literal["inventory_read_probe"] = "inventory_read_probe"


@dataclass(frozen=True, slots=True)
class BackupRecoverySnapshot:
    as_of: datetime
    overall_status: OverallStatus
    overall_oldest_readable_at: datetime | None
    overall_newest_readable_at: datetime | None
    local: DestinationRecoverySnapshot
    offbox: DestinationRecoverySnapshot
    verification_scope: Literal["inventory_read_probe"] = "inventory_read_probe"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(UTC)


def _configured(record: repository.BackupSettingsRecord) -> bool:
    return bool(
        record.smb_host
        and record.smb_share
        and record.smb_username
        and record.smb_password_encrypted
    )


def resolve_smb_target(
    record: repository.BackupSettingsRecord, settings: Settings
) -> tuple[smb_backup.SmbTarget | None, str | None]:
    if not _configured(record):
        return None, "destination_not_configured"
    assert record.smb_host is not None
    assert record.smb_share is not None
    assert record.smb_username is not None
    assert record.smb_password_encrypted is not None
    try:
        password = banksync.decrypt_credential(settings, record.smb_password_encrypted)
    except Exception:  # noqa: BLE001 - secret/cipher failures are intentionally redacted
        return None, "credentials_unavailable"
    return (
        smb_backup.SmbTarget(
            host=record.smb_host,
            share=record.smb_share,
            folder=record.smb_folder,
            username=record.smb_username,
            password=password,
            domain=record.smb_domain,
            io_timeout_seconds=settings.backup_io_timeout_seconds,
        ),
        None,
    )


def _plan(
    inventory: tuple[BackupInventoryItem, ...],
    *,
    policy: RetentionPolicy,
    max_bytes: int | None,
    as_of: datetime,
) -> RetentionPlan:
    return plan_retention(
        as_of=as_of,
        inventory=inventory,
        policy=policy,
        max_bytes=max_bytes,
    )


def _pending(plan: RetentionPlan, items: tuple[BackupInventoryItem, ...]) -> PendingPruneSummary:
    by_key = {item.archive_key: item for item in items}
    selected = [
        decision for decision in plan.decisions if decision.action is RetentionAction.DELETE
    ]
    return PendingPruneSummary(
        len(selected), sum(by_key[decision.archive_key].size_bytes for decision in selected)
    )


def calculate_pending_prunes(
    engine: Engine,
    settings: Settings,
    stored: repository.BackupSettingsRecord,
    *,
    as_of: datetime,
) -> tuple[PendingPruneSummary, PendingPruneSummary]:
    """Calculate pending decisions without applying or recording them."""
    when = _utc(as_of)
    local = collect_local_inventory(engine, settings.backup_dir, as_of=when)
    if local.available:
        local_items = local.items
        local_pending = _pending(
            _plan(
                local_items,
                policy=stored.local_retention,
                max_bytes=stored.local_max_bytes,
                as_of=when,
            ),
            local_items,
        )
    else:
        local_pending = PendingPruneSummary(None, None)

    target, target_error = resolve_smb_target(stored, settings)
    if target_error == "destination_not_configured":
        offbox_pending = PendingPruneSummary(0, 0)
    elif target is None:
        offbox_pending = PendingPruneSummary(None, None)
    else:
        try:
            remote = smb_backup.list_inventory(target)
        except smb_backup.SmbInventoryError:
            offbox_pending = PendingPruneSummary(None, None)
        else:
            remote_items = backup_processing.remote_inventory_items(engine, remote, as_of=when)
            offbox_pending = _pending(
                _plan(
                    remote_items,
                    policy=stored.offbox_retention,
                    max_bytes=stored.offbox_max_bytes,
                    as_of=when,
                ),
                remote_items,
            )
    return local_pending, offbox_pending


def _qualified(
    plan: RetentionPlan, items: tuple[BackupInventoryItem, ...]
) -> tuple[BackupInventoryItem, ...]:
    decision_by_key = {decision.archive_key: decision for decision in plan.decisions}
    qualified = [
        item
        for item in items
        if decision_by_key[item.archive_key].reason is not RetentionReason.PROTECTED_ANOMALY
    ]
    qualified.sort(key=lambda item: item.archive_key)
    qualified.sort(key=lambda item: item.taken_at, reverse=True)
    return tuple(qualified)


def _diagnostics(
    items: tuple[BackupInventoryItem, ...], protected_storage_count: int
) -> tuple[int, int, int, int]:
    metadata = 0
    protected = protected_storage_count
    unknown = 0
    incompatible = 0
    for item in items:
        codes = set(item.anomaly_codes)
        if codes & _PROTECTING_METADATA_CODES:
            metadata += 1
        if "compatibility_unknown" in codes:
            unknown += 1
        if "future_version" in codes:
            incompatible += 1
        if codes - _NON_PROTECTING_CODES or not (
            item.managed
            and item.present
            and item.readable
            and item.compatible
            and item.size_bytes > 0
        ):
            protected += 1
    return metadata, protected, unknown, incompatible


def _target_interval(
    policy: RetentionPolicy, as_of: datetime
) -> tuple[datetime | None, datetime | None, datetime | None]:
    if policy.mode is RetentionMode.KEEP_ALL:
        return None, None, None
    assert policy.keep_all_days is not None
    assert policy.daily_until_days is not None
    assert policy.weekly_until_days is not None
    cutoff = as_of - timedelta(days=policy.weekly_until_days)
    if policy.daily_until_days < policy.weekly_until_days:
        bucket_start = datetime.combine(
            cutoff.date() - timedelta(days=cutoff.weekday()), time.min, tzinfo=UTC
        )
        bucket_end = bucket_start + timedelta(days=7)
        return cutoff, bucket_start, bucket_end
    if policy.keep_all_days < policy.daily_until_days:
        bucket_start = datetime.combine(cutoff.date(), time.min, tzinfo=UTC)
        bucket_end = bucket_start + timedelta(days=1)
        return cutoff, bucket_start, bucket_end
    return cutoff, cutoff, cutoff


def _events(
    engine: Engine,
    *,
    generation: str,
    policy: RetentionPolicy,
    retention_activated_at: datetime | None,
    as_of: datetime,
) -> tuple[tuple[repository.BackupRetentionEventRecord, ...], bool]:
    _, bucket_start, _ = _target_interval(policy, as_of)
    since = bucket_start if bucket_start is not None else retention_activated_at
    found = repository.list_backup_retention_events_for_status(
        engine, destination_generation=generation, since=since, limit=501
    )
    return tuple(found[:500]), len(found) > 500


def _latest_inventory_failed(events: tuple[repository.BackupRetentionEventRecord, ...]) -> bool:
    latest = next(
        (event for event in events if event.action in ("inventory_failed", "inventory_succeeded")),
        None,
    )
    return latest is not None and latest.action == "inventory_failed"


def _has_unresolved_prune_failure(
    events: tuple[repository.BackupRetentionEventRecord, ...],
) -> bool:
    latest_by_archive: dict[str, repository.BackupRetentionEventRecord] = {}
    for event in events:
        if event.archive_key is not None and event.archive_key not in latest_by_archive:
            latest_by_archive[event.archive_key] = event
    return any(event.action == "prune_failed" for event in latest_by_archive.values())


def _event_in_interval(
    event: repository.BackupRetentionEventRecord,
    start: datetime,
    end: datetime,
) -> bool:
    timestamp = event.archive_taken_at
    if timestamp is None and event.action == "capacity_blocked":
        timestamp = event.occurred_at
    return timestamp is not None and (
        timestamp == start if end == start else start <= timestamp < end
    )


def _coverage(
    *,
    policy: RetentionPolicy,
    activated_at: datetime | None,
    review_required: bool,
    qualified: tuple[BackupInventoryItem, ...],
    probe_complete: bool,
    protected_count: int,
    events: tuple[repository.BackupRetentionEventRecord, ...],
    events_incomplete: bool,
    plan: RetentionPlan,
    as_of: datetime,
) -> CoverageStatus:
    if policy.mode is RetentionMode.KEEP_ALL:
        return "not_applicable"
    cutoff, bucket_start, bucket_end = _target_interval(policy, as_of)
    assert cutoff is not None and bucket_start is not None and bucket_end is not None
    # The target bucket is intersected with the policy horizon. Pre-cutoff
    # archives are not retained merely to make status look satisfied.
    target_start = max(cutoff, bucket_start)
    missing_causality = any(
        event.action in {"pruned", "explicit_deleted"} and event.archive_taken_at is None
        for event in events
    )
    if (
        review_required
        or not probe_complete
        or protected_count
        or events_incomplete
        or missing_causality
    ):
        return "unknown"
    if not qualified:
        return "empty"

    in_target = [
        item
        for item in qualified
        if (
            item.taken_at == target_start
            if bucket_end == target_start
            else target_start <= item.taken_at < bucket_end
        )
    ]
    target_keys = {item.archive_key for item in in_target}
    cap_removes_target = any(
        decision.archive_key in target_keys
        and decision.action is RetentionAction.DELETE
        and decision.reason is RetentionReason.CAPACITY_LIMIT
        for decision in plan.decisions
    )
    if cap_removes_target:
        return "shortened"
    if in_target and any(
        newer.taken_at > candidate.taken_at for candidate in in_target for newer in qualified
    ):
        return "met"

    shortening_event = any(
        event.action in _SHORTENING_ACTIONS
        and _event_in_interval(event, target_start, bucket_end)
        for event in events
    )
    if shortening_event:
        return "shortened"

    outer_days = policy.weekly_until_days or 0
    young = activated_at is not None and activated_at > as_of - timedelta(days=outer_days)
    opportunity = any(target_start <= item.taken_at < bucket_end for item in qualified) or any(
        _event_in_interval(event, target_start, bucket_end) for event in events
    )
    if young and not opportunity:
        return "building"
    return "incomplete"


def _logical_cap_unsatisfied(
    qualified: tuple[BackupInventoryItem, ...], max_bytes: int | None, plan: RetentionPlan
) -> bool:
    if max_bytes is None or not qualified:
        return False
    decision_by_key = {decision.archive_key: decision for decision in plan.decisions}
    kept = [
        item
        for item in qualified
        if decision_by_key[item.archive_key].action is RetentionAction.KEEP
    ]
    return sum(item.size_bytes for item in kept) > max_bytes


def _ordered_reasons(codes: set[str]) -> tuple[str, ...]:
    ordered = [code for code in _REASON_ORDER if code in codes]
    ordered.extend(sorted(codes - set(ordered)))
    return tuple(ordered)


def _status_and_reasons(
    *,
    configured: bool,
    inventory_available: bool,
    qualified: tuple[BackupInventoryItem, ...],
    protected_count: int,
    metadata_count: int,
    incompatible_count: int,
    probe_status: ProbeStatus,
    coverage: CoverageStatus,
    review_required: bool,
    timestamp_fallback: bool,
    capacity: CapacityObservation,
    logical_cap_unsatisfied: bool,
    inventory_failed: bool,
    prune_failed: bool,
    events_incomplete: bool,
    unavailable_code: str,
) -> tuple[DestinationStatus, tuple[str, ...], str | None]:
    codes: set[str] = set()
    if not configured:
        codes.add("destination_not_configured")
        status: DestinationStatus = "not_configured"
    elif not inventory_available:
        codes.add(unavailable_code)
        status = "unavailable"
    elif not qualified and protected_count == 0 and probe_status == "complete":
        status = "empty"
    else:
        if capacity.status == "insufficient":
            codes.add("capacity_insufficient")
        elif capacity.status in ("unknown", "unavailable"):
            codes.add(
                "capacity_unknown" if capacity.status == "unknown" else "capacity_unavailable"
            )
        if logical_cap_unsatisfied:
            codes.add("logical_cap_unsatisfied")
        if coverage == "shortened":
            codes.add("coverage_shortened")
        constrained = (
            capacity.status == "insufficient" or logical_cap_unsatisfied or coverage == "shortened"
        )
        if review_required:
            codes.add("retention_review_required")
        if timestamp_fallback:
            codes.add("remote_timestamp_fallback")
        if metadata_count:
            codes.add("metadata_mismatch")
        if protected_count:
            codes.add("protected_anomaly")
        if incompatible_count:
            codes.add("known_incompatible")
        if probe_status == "partial":
            codes.add("probe_partial")
        elif probe_status == "unavailable":
            codes.add("probe_unavailable")
        if inventory_failed:
            codes.add("unresolved_inventory_failure")
        if prune_failed:
            codes.add("unresolved_prune_failure")
        if events_incomplete:
            codes.add("retention_event_history_incomplete")
        if coverage == "unknown":
            codes.add("coverage_unknown")
        elif coverage == "incomplete":
            codes.add("coverage_incomplete")
        degraded = bool(codes)
        if constrained:
            status = "constrained"
        elif degraded:
            status = "degraded"
        else:
            status = "healthy"
    ordered = _ordered_reasons(codes)
    return status, ordered, _SAFE_REASONS.get(ordered[0]) if ordered else None


def _local_snapshot(
    engine: Engine,
    settings: Settings,
    stored: repository.BackupSettingsRecord,
    *,
    as_of: datetime,
) -> tuple[DestinationRecoverySnapshot, int | None]:
    inventory = collect_local_inventory(engine, settings.backup_dir, as_of=as_of)
    estimate = estimate_next_backup_bytes(inventory) if inventory.available else None
    capacity = query_local_capacity(
        settings.backup_dir,
        reserve_bytes=stored.local_min_free_bytes,
        estimated_next_backup_bytes=estimate,
        as_of=as_of,
    )
    items = inventory.items
    plan = _plan(
        items,
        policy=stored.local_retention,
        max_bytes=stored.local_max_bytes,
        as_of=as_of,
    )
    pending = _pending(plan, items) if inventory.available else PendingPruneSummary(None, None)
    qualified = _qualified(plan, items) if inventory.available else ()
    metadata, protected, unknown, incompatible = _diagnostics(
        items, len(inventory.protected_entries)
    )
    events, events_incomplete = _events(
        engine,
        generation=stored.local_destination_generation,
        policy=stored.local_retention,
        retention_activated_at=stored.retention_activated_at,
        as_of=as_of,
    )
    inventory_failed = _latest_inventory_failed(events)
    prune_failed = _has_unresolved_prune_failure(events)
    coverage = _coverage(
        policy=stored.local_retention,
        activated_at=stored.retention_activated_at,
        review_required=stored.retention_review_required,
        qualified=qualified,
        probe_complete=inventory.available,
        protected_count=protected,
        events=events,
        events_incomplete=events_incomplete,
        plan=plan,
        as_of=as_of,
    )
    status, reasons, reason = _status_and_reasons(
        configured=True,
        inventory_available=inventory.available,
        qualified=qualified,
        protected_count=protected,
        metadata_count=metadata,
        incompatible_count=incompatible,
        probe_status="complete" if inventory.available else "unavailable",
        coverage=coverage,
        review_required=stored.retention_review_required,
        timestamp_fallback=False,
        capacity=capacity,
        logical_cap_unsatisfied=_logical_cap_unsatisfied(qualified, stored.local_max_bytes, plan),
        inventory_failed=inventory_failed,
        prune_failed=prune_failed,
        events_incomplete=events_incomplete,
        unavailable_code="local_inventory_unavailable",
    )
    oldest = qualified[-1] if qualified else None
    newest = qualified[0] if qualified else None
    visible = sum(1 for entry in inventory.entries if entry.item.present)
    target_oldest, _, _ = _target_interval(stored.local_retention, as_of)
    return (
        DestinationRecoverySnapshot(
            destination="local",
            configured=True,
            status=status,
            coverage_status=coverage,
            policy=stored.local_retention,
            target_oldest_at=target_oldest,
            retention_review_required=stored.retention_review_required,
            retention_activated_at=stored.retention_activated_at,
            pending_prune_count=pending.count,
            pending_prune_bytes=pending.size_bytes,
            visible_archive_count=visible,
            readable_archive_count=len(qualified) if inventory.available else None,
            probe_status="complete" if inventory.available else "unavailable",
            probed_archive_count=visible if inventory.available else 0,
            oldest_readable_at=oldest.taken_at if oldest else None,
            newest_readable_at=newest.taken_at if newest else None,
            oldest_timestamp_source=(oldest.timestamp_source.value if oldest else None),
            metadata_mismatch_count=metadata,
            protected_anomaly_count=protected,
            compatibility_unknown_count=unknown,
            known_incompatible_count=incompatible,
            capacity=capacity,
            reason_codes=reasons,
            reason=reason,
            as_of=as_of,
        ),
        estimate,
    )


def _unavailable_capacity(
    *, as_of: datetime, reserve: int, estimate: int | None, code: str
) -> CapacityObservation:
    return capacity_observation(
        total_bytes=None,
        available_bytes=None,
        reserve_bytes=reserve,
        estimated_next_backup_bytes=estimate,
        as_of=as_of,
        unavailable=True,
        reason_code=code,
        reason=_SAFE_REASONS.get(code),
    )


def _offbox_snapshot(
    engine: Engine,
    settings: Settings,
    stored: repository.BackupSettingsRecord,
    *,
    estimate: int | None,
    as_of: datetime,
) -> DestinationRecoverySnapshot:
    configured = _configured(stored)
    target, target_error = resolve_smb_target(stored, settings)
    target_oldest, _, _ = _target_interval(stored.offbox_retention, as_of)
    if not configured:
        capacity = _unavailable_capacity(
            as_of=as_of,
            reserve=stored.offbox_min_free_bytes,
            estimate=estimate,
            code="destination_not_configured",
        )
        status, reasons, reason = _status_and_reasons(
            configured=False,
            inventory_available=False,
            qualified=(),
            protected_count=0,
            metadata_count=0,
            incompatible_count=0,
            probe_status="unavailable",
            coverage="unknown",
            review_required=stored.retention_review_required,
            timestamp_fallback=False,
            capacity=capacity,
            logical_cap_unsatisfied=False,
            inventory_failed=False,
            prune_failed=False,
            events_incomplete=False,
            unavailable_code="inventory_unavailable",
        )
        return DestinationRecoverySnapshot(
            "offbox",
            False,
            status,
            "unknown",
            stored.offbox_retention,
            target_oldest,
            stored.retention_review_required,
            stored.retention_activated_at,
            0,
            0,
            0,
            None,
            "unavailable",
            0,
            None,
            None,
            None,
            0,
            0,
            0,
            0,
            capacity,
            reasons,
            reason,
            as_of,
        )
    if target is None:
        capacity = _unavailable_capacity(
            as_of=as_of,
            reserve=stored.offbox_min_free_bytes,
            estimate=estimate,
            code=target_error or "credentials_unavailable",
        )
        status, reasons, reason = _status_and_reasons(
            configured=True,
            inventory_available=False,
            qualified=(),
            protected_count=0,
            metadata_count=0,
            incompatible_count=0,
            probe_status="unavailable",
            coverage="unknown",
            review_required=stored.retention_review_required,
            timestamp_fallback=False,
            capacity=capacity,
            logical_cap_unsatisfied=False,
            inventory_failed=False,
            prune_failed=False,
            events_incomplete=False,
            unavailable_code=target_error or "inventory_unavailable",
        )
        return DestinationRecoverySnapshot(
            "offbox",
            True,
            status,
            "unknown",
            stored.offbox_retention,
            target_oldest,
            stored.retention_review_required,
            stored.retention_activated_at,
            None,
            None,
            0,
            None,
            "unavailable",
            0,
            None,
            None,
            None,
            0,
            0,
            0,
            0,
            capacity,
            reasons,
            reason,
            as_of,
        )

    try:
        raw = smb_backup.list_inventory(target)
    except smb_backup.SmbInventoryError:
        capacity = backup_processing.query_remote_capacity_observation(
            target,
            reserve_bytes=stored.offbox_min_free_bytes,
            estimate=estimate,
            as_of=as_of,
        )
        status, reasons, reason = _status_and_reasons(
            configured=True,
            inventory_available=False,
            qualified=(),
            protected_count=0,
            metadata_count=0,
            incompatible_count=0,
            probe_status="unavailable",
            coverage="unknown",
            review_required=stored.retention_review_required,
            timestamp_fallback=False,
            capacity=capacity,
            logical_cap_unsatisfied=False,
            inventory_failed=True,
            prune_failed=False,
            events_incomplete=False,
            unavailable_code="inventory_unavailable",
        )
        return DestinationRecoverySnapshot(
            "offbox",
            True,
            status,
            "unknown",
            stored.offbox_retention,
            target_oldest,
            stored.retention_review_required,
            stored.retention_activated_at,
            None,
            None,
            0,
            None,
            "unavailable",
            0,
            None,
            None,
            None,
            0,
            0,
            0,
            0,
            capacity,
            reasons,
            reason,
            as_of,
        )

    items = backup_processing.remote_inventory_items(engine, raw, as_of=as_of)
    plan = _plan(
        items,
        policy=stored.offbox_retention,
        max_bytes=stored.offbox_max_bytes,
        as_of=as_of,
    )
    pending = _pending(plan, items)
    qualified = _qualified(plan, items)
    item_by_key = {item.archive_key: item for item in qualified}
    raw_by_key = {item.filename: item for item in raw.items}
    ordered_raw = tuple(raw_by_key[item.archive_key] for item in qualified)
    probe = smb_backup.probe_inventory(
        target, smb_backup.SmbInventory(ordered_raw, raw.protected_entries)
    )
    readable_keys = frozenset(probe.readable_filenames)
    read_qualified = tuple(item for item in qualified if item.archive_key in readable_keys)
    oldest = item_by_key.get(probe.oldest_readable.filename) if probe.oldest_readable else None
    newest = item_by_key.get(probe.newest_readable.filename) if probe.newest_readable else None
    metadata, protected, unknown, incompatible = _diagnostics(items, len(raw.protected_entries))
    events, events_incomplete = _events(
        engine,
        generation=stored.offbox_destination_generation,
        policy=stored.offbox_retention,
        retention_activated_at=stored.retention_activated_at,
        as_of=as_of,
    )
    inventory_failed = _latest_inventory_failed(events)
    prune_failed = _has_unresolved_prune_failure(events)
    probe_complete = probe.status == "complete" and oldest is not None and newest is not None
    if not qualified and probe.status == "complete":
        probe_complete = True
    coverage = _coverage(
        policy=stored.offbox_retention,
        activated_at=stored.retention_activated_at,
        review_required=stored.retention_review_required,
        qualified=read_qualified,
        probe_complete=probe_complete,
        protected_count=protected,
        events=events,
        events_incomplete=events_incomplete,
        plan=plan,
        as_of=as_of,
    )
    capacity = backup_processing.query_remote_capacity_observation(
        target,
        reserve_bytes=stored.offbox_min_free_bytes,
        estimate=estimate,
        as_of=as_of,
    )
    timestamp_fallback = any(
        item.timestamp_source is BackupTimestampSource.REMOTE_MODIFIED_AT for item in qualified
    )
    status, reasons, reason = _status_and_reasons(
        configured=True,
        inventory_available=True,
        qualified=read_qualified,
        protected_count=protected,
        metadata_count=metadata,
        incompatible_count=incompatible,
        probe_status=probe.status,
        coverage=coverage,
        review_required=stored.retention_review_required,
        timestamp_fallback=timestamp_fallback,
        capacity=capacity,
        logical_cap_unsatisfied=_logical_cap_unsatisfied(qualified, stored.offbox_max_bytes, plan),
        inventory_failed=inventory_failed,
        prune_failed=prune_failed,
        events_incomplete=events_incomplete,
        unavailable_code="inventory_unavailable",
    )
    return DestinationRecoverySnapshot(
        destination="offbox",
        configured=True,
        status=status,
        coverage_status=coverage,
        policy=stored.offbox_retention,
        target_oldest_at=target_oldest,
        retention_review_required=stored.retention_review_required,
        retention_activated_at=stored.retention_activated_at,
        pending_prune_count=pending.count,
        pending_prune_bytes=pending.size_bytes,
        visible_archive_count=len(raw.items),
        readable_archive_count=probe.readable_archive_count,
        probe_status=probe.status,
        probed_archive_count=probe.probed_archive_count,
        oldest_readable_at=oldest.taken_at if oldest else None,
        newest_readable_at=newest.taken_at if newest else None,
        oldest_timestamp_source=(oldest.timestamp_source.value if oldest else None),
        metadata_mismatch_count=metadata,
        protected_anomaly_count=protected,
        compatibility_unknown_count=unknown,
        known_incompatible_count=incompatible,
        capacity=capacity,
        reason_codes=reasons,
        reason=reason,
        as_of=as_of,
    )


def build_backup_recovery_snapshot(
    engine: Engine,
    settings: Settings,
    *,
    as_of: datetime | None = None,
) -> BackupRecoverySnapshot:
    """Return one qualified, immutable recovery snapshot without mutation."""
    when = _utc(as_of or datetime.now(UTC))
    stored = repository.get_backup_settings(engine, settings=settings, as_of=when)
    local, estimate = _local_snapshot(engine, settings, stored, as_of=when)
    offbox = _offbox_snapshot(engine, settings, stored, estimate=estimate, as_of=when)
    configured = [local] + ([offbox] if offbox.configured else [])
    inventoried = [item for item in configured if item.status != "unavailable"]
    if not inventoried:
        overall: OverallStatus = "unavailable"
    elif all(item.status == "empty" for item in configured):
        overall = "empty"
    elif all(item.status == "healthy" for item in configured):
        overall = "healthy"
    elif any(item.status == "constrained" for item in configured):
        overall = "constrained"
    else:
        overall = "degraded"
    oldest_values = [
        item.oldest_readable_at for item in configured if item.oldest_readable_at is not None
    ]
    newest_values = [
        item.newest_readable_at for item in configured if item.newest_readable_at is not None
    ]
    return BackupRecoverySnapshot(
        as_of=when,
        overall_status=overall,
        overall_oldest_readable_at=min(oldest_values) if oldest_values else None,
        overall_newest_readable_at=max(newest_values) if newest_values else None,
        local=local,
        offbox=offbox,
    )

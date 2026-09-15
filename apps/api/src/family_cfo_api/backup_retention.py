"""Pure deterministic backup-retention planning.

The planner in this module deliberately has no filesystem, database, SMB,
logging, or audit dependencies.  Callers are responsible for constructing a
strict inventory and for applying (or merely reporting) its decisions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class RetentionMode(StrEnum):
    TIERED = "tiered"
    KEEP_ALL = "keep_all"


class BackupDestination(StrEnum):
    LOCAL = "local"
    OFFBOX = "offbox"


class BackupTimestampSource(StrEnum):
    JOB_STARTED_AT = "job_started_at"
    REMOTE_MODIFIED_AT = "remote_modified_at"


class BackupInventoryStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    PENDING = "pending"
    RUNNING = "running"
    PRUNED = "pruned"


class RetentionAction(StrEnum):
    KEEP = "keep"
    DELETE = "delete"


class RetentionReason(StrEnum):
    NEWEST = "newest"
    RECENT = "recent"
    DAILY_BUCKET = "daily_bucket"
    WEEKLY_BUCKET = "weekly_bucket"
    EXPIRED = "expired"
    BUCKET_SUPERSEDED = "bucket_superseded"
    CAPACITY_LIMIT = "capacity_limit"
    PROTECTED_ANOMALY = "protected_anomaly"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    mode: RetentionMode
    keep_all_days: int | None = None
    daily_until_days: int | None = None
    weekly_until_days: int | None = None

    def __post_init__(self) -> None:
        try:
            mode = RetentionMode(self.mode)
        except ValueError as exc:
            raise ValueError(f"unsupported retention mode: {self.mode!r}") from exc
        object.__setattr__(self, "mode", mode)

        horizons = (self.keep_all_days, self.daily_until_days, self.weekly_until_days)
        if mode is RetentionMode.KEEP_ALL:
            if any(value is not None for value in horizons):
                raise ValueError("keep_all policy requires null horizons")
            return

        if any(not _is_plain_int(value) for value in horizons):
            raise ValueError("tiered policy requires three integer horizons")
        keep_all, daily, weekly = horizons
        assert keep_all is not None and daily is not None and weekly is not None
        if not 1 <= keep_all <= daily <= weekly <= 3650:
            raise ValueError(
                "tiered horizons must satisfy "
                "1 <= keep_all_days <= daily_until_days <= weekly_until_days <= 3650"
            )


@dataclass(frozen=True, slots=True)
class BackupInventoryItem:
    destination: BackupDestination
    archive_key: str
    taken_at: datetime
    timestamp_source: BackupTimestampSource
    size_bytes: int
    job_id: str | None = None
    status: BackupInventoryStatus = BackupInventoryStatus.COMPLETED
    managed: bool = True
    present: bool = True
    readable: bool = True
    compatible: bool = True
    anomaly_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "destination", BackupDestination(self.destination))
            object.__setattr__(
                self, "timestamp_source", BackupTimestampSource(self.timestamp_source)
            )
            object.__setattr__(self, "status", BackupInventoryStatus(self.status))
        except ValueError as exc:
            raise ValueError("invalid backup inventory enum value") from exc
        if not isinstance(self.archive_key, str):
            raise TypeError("archive_key must be a string")
        if not self.archive_key:
            raise ValueError("archive_key must not be empty")
        _require_utc(self.taken_at, "taken_at")
        if not _is_plain_int(self.size_bytes) or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        if self.job_id is not None and not isinstance(self.job_id, str):
            raise TypeError("job_id must be a string or null")
        if self.job_id == "":
            raise ValueError("job_id must not be empty")
        for name in ("managed", "present", "readable", "compatible"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean")
        if isinstance(self.anomaly_codes, (str, bytes)):
            raise TypeError("anomaly_codes must be a collection of codes")
        try:
            codes = tuple(self.anomaly_codes)
        except TypeError as exc:
            raise TypeError("anomaly_codes must be a collection of codes") from exc
        if any(not isinstance(code, str) or not code for code in codes):
            raise ValueError("anomaly codes must be non-empty strings")
        object.__setattr__(self, "anomaly_codes", codes)


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    archive_key: str
    action: RetentionAction
    reason: RetentionReason
    bucket_key: str | None = None


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    decisions: tuple[RetentionDecision, ...]
    as_of: datetime
    policy: RetentionPolicy
    max_bytes: int | None


# This is the sole anomaly code that the accepted design explicitly says may
# remain eligible.  Unknown codes fail safe: preserve the evidence.
_NON_PROTECTING_ANOMALY_CODES = frozenset({"compatibility_unknown"})


def plan_retention(
    *,
    as_of: datetime,
    inventory: Iterable[BackupInventoryItem],
    policy: RetentionPolicy,
    max_bytes: int | None = None,
) -> RetentionPlan:
    """Return deterministic keep/delete decisions for one destination.

    Decisions are returned in stable ``taken_at DESC, archive_key ASC`` order.
    The optional logical cap is applied only to otherwise-kept eligible managed
    bytes, oldest first, after time-bucket selection.  Protected evidence never
    enters cap accounting and the newest eligible archive is never removed.
    """

    _require_utc(as_of, "as_of")
    if max_bytes is not None and (not _is_plain_int(max_bytes) or max_bytes <= 0):
        raise ValueError("max_bytes must be null or a positive integer")
    if not isinstance(policy, RetentionPolicy):
        raise TypeError("policy must be a RetentionPolicy")

    items = tuple(inventory)
    for item in items:
        if not isinstance(item, BackupInventoryItem):
            raise TypeError("inventory must contain BackupInventoryItem values")

    destinations = {item.destination for item in items}
    if len(destinations) > 1:
        raise ValueError("one retention plan may contain only one destination")
    identities = [(item.destination, item.archive_key) for item in items]
    if len(set(identities)) != len(identities):
        raise ValueError("inventory archive keys must be unique within a destination")

    # Two stable passes express taken_at DESC / archive_key ASC without
    # converting datetimes to lossy platform timestamps.
    ordered = sorted(items, key=lambda item: item.archive_key)
    ordered.sort(key=lambda item: item.taken_at, reverse=True)
    eligible = [item for item in ordered if _is_eligible(item, as_of)]
    newest = eligible[0] if eligible else None

    decisions_by_key: dict[str, RetentionDecision] = {}
    daily_winners: set[str] = set()
    weekly_winners: set[str] = set()

    for item in ordered:
        if not _is_eligible(item, as_of):
            decisions_by_key[item.archive_key] = RetentionDecision(
                item.archive_key,
                RetentionAction.KEEP,
                RetentionReason.PROTECTED_ANOMALY,
            )
            continue
        decisions_by_key[item.archive_key] = _time_decision(
            item,
            as_of=as_of,
            policy=policy,
            daily_winners=daily_winners,
            weekly_winners=weekly_winners,
        )

    if newest is not None:
        decisions_by_key[newest.archive_key] = RetentionDecision(
            newest.archive_key,
            RetentionAction.KEEP,
            RetentionReason.NEWEST,
        )

    if max_bytes is not None:
        kept_eligible = [
            item
            for item in eligible
            if decisions_by_key[item.archive_key].action is RetentionAction.KEEP
        ]
        retained_bytes = sum(item.size_bytes for item in kept_eligible)
        for item in reversed(kept_eligible):
            if retained_bytes <= max_bytes:
                break
            if item is newest:
                continue
            decisions_by_key[item.archive_key] = RetentionDecision(
                item.archive_key,
                RetentionAction.DELETE,
                RetentionReason.CAPACITY_LIMIT,
            )
            retained_bytes -= item.size_bytes

    return RetentionPlan(
        decisions=tuple(decisions_by_key[item.archive_key] for item in ordered),
        as_of=as_of,
        policy=policy,
        max_bytes=max_bytes,
    )


def _time_decision(
    item: BackupInventoryItem,
    *,
    as_of: datetime,
    policy: RetentionPolicy,
    daily_winners: set[str],
    weekly_winners: set[str],
) -> RetentionDecision:
    if policy.mode is RetentionMode.KEEP_ALL:
        return RetentionDecision(
            item.archive_key,
            RetentionAction.KEEP,
            RetentionReason.RECENT,
        )

    assert policy.keep_all_days is not None
    assert policy.daily_until_days is not None
    assert policy.weekly_until_days is not None
    recent_cutoff = as_of - timedelta(days=policy.keep_all_days)
    daily_cutoff = as_of - timedelta(days=policy.daily_until_days)
    weekly_cutoff = as_of - timedelta(days=policy.weekly_until_days)

    if item.taken_at >= recent_cutoff:
        return RetentionDecision(
            item.archive_key,
            RetentionAction.KEEP,
            RetentionReason.RECENT,
        )
    if item.taken_at >= daily_cutoff:
        bucket_key = item.taken_at.date().isoformat()
        if bucket_key not in daily_winners:
            daily_winners.add(bucket_key)
            return RetentionDecision(
                item.archive_key,
                RetentionAction.KEEP,
                RetentionReason.DAILY_BUCKET,
                bucket_key,
            )
        return RetentionDecision(
            item.archive_key,
            RetentionAction.DELETE,
            RetentionReason.BUCKET_SUPERSEDED,
            bucket_key,
        )
    if item.taken_at >= weekly_cutoff:
        monday = item.taken_at.date() - timedelta(days=item.taken_at.weekday())
        bucket_key = monday.isoformat()
        if bucket_key not in weekly_winners:
            weekly_winners.add(bucket_key)
            return RetentionDecision(
                item.archive_key,
                RetentionAction.KEEP,
                RetentionReason.WEEKLY_BUCKET,
                bucket_key,
            )
        return RetentionDecision(
            item.archive_key,
            RetentionAction.DELETE,
            RetentionReason.BUCKET_SUPERSEDED,
            bucket_key,
        )
    return RetentionDecision(
        item.archive_key,
        RetentionAction.DELETE,
        RetentionReason.EXPIRED,
    )


def _is_eligible(item: BackupInventoryItem, as_of: datetime) -> bool:
    if item.taken_at > as_of:
        return False
    if item.status is not BackupInventoryStatus.COMPLETED:
        return False
    if not (item.managed and item.present and item.readable and item.compatible):
        return False
    if item.size_bytes == 0:
        return False
    return not (set(item.anomaly_codes) - _NON_PROTECTING_ANOMALY_CODES)


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)

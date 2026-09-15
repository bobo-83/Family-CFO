from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from family_cfo_api.backup_retention import (
    BackupDestination,
    BackupInventoryItem,
    BackupInventoryStatus,
    BackupTimestampSource,
    RetentionAction,
    RetentionMode,
    RetentionPolicy,
    RetentionReason,
    plan_retention,
)

AS_OF = datetime(2026, 9, 10, 12, tzinfo=UTC)
TIERED = RetentionPolicy(
    mode=RetentionMode.TIERED,
    keep_all_days=3,
    daily_until_days=14,
    weekly_until_days=90,
)


def item(
    key: str,
    *,
    age: timedelta = timedelta(0),
    taken_at: datetime | None = None,
    size: int = 10,
    destination: BackupDestination = BackupDestination.LOCAL,
    source: BackupTimestampSource = BackupTimestampSource.JOB_STARTED_AT,
    status: BackupInventoryStatus = BackupInventoryStatus.COMPLETED,
    managed: bool = True,
    present: bool = True,
    readable: bool = True,
    compatible: bool = True,
    anomalies: tuple[str, ...] = (),
    job_id: str | None = "job",
) -> BackupInventoryItem:
    return BackupInventoryItem(
        destination=destination,
        archive_key=key,
        taken_at=taken_at if taken_at is not None else AS_OF - age,
        timestamp_source=source,
        size_bytes=size,
        job_id=job_id,
        status=status,
        managed=managed,
        present=present,
        readable=readable,
        compatible=compatible,
        anomaly_codes=anomalies,
    )


def decisions(*items: BackupInventoryItem, max_bytes: int | None = None):
    plan = plan_retention(
        as_of=AS_OF,
        inventory=items,
        policy=TIERED,
        max_bytes=max_bytes,
    )
    return {decision.archive_key: decision for decision in plan.decisions}


def test_policy_modes_and_bounds_are_validated() -> None:
    assert RetentionPolicy(mode="keep_all").mode is RetentionMode.KEEP_ALL
    assert RetentionPolicy(mode="tiered", keep_all_days=1, daily_until_days=1, weekly_until_days=1)
    assert RetentionPolicy(
        mode="tiered", keep_all_days=3650, daily_until_days=3650, weekly_until_days=3650
    )

    invalid = [
        dict(mode="keep_all", keep_all_days=1),
        dict(mode="tiered", keep_all_days=None, daily_until_days=2, weekly_until_days=3),
        dict(mode="tiered", keep_all_days=0, daily_until_days=2, weekly_until_days=3),
        dict(mode="tiered", keep_all_days=2, daily_until_days=1, weekly_until_days=3),
        dict(mode="tiered", keep_all_days=1, daily_until_days=3, weekly_until_days=2),
        dict(mode="tiered", keep_all_days=1, daily_until_days=2, weekly_until_days=3651),
        dict(mode="tiered", keep_all_days=True, daily_until_days=2, weekly_until_days=3),
        dict(mode="unknown"),
    ]
    for values in invalid:
        with pytest.raises(ValueError):
            RetentionPolicy(**values)


def test_models_are_immutable_and_normalize_enums_and_anomaly_tuple() -> None:
    inventory_item = BackupInventoryItem(
        destination="offbox",
        archive_key="a",
        taken_at=AS_OF,
        timestamp_source="remote_modified_at",
        size_bytes=1,
        status="completed",
        anomaly_codes=["compatibility_unknown"],  # type: ignore[arg-type]
    )
    assert inventory_item.destination is BackupDestination.OFFBOX
    assert inventory_item.timestamp_source is BackupTimestampSource.REMOTE_MODIFIED_AT
    assert inventory_item.anomaly_codes == ("compatibility_unknown",)
    with pytest.raises(FrozenInstanceError):
        inventory_item.size_bytes = 2  # type: ignore[misc]


def test_input_validation_rejects_invalid_time_size_identity_cap_and_mixed_destination() -> None:
    with pytest.raises(ValueError, match="taken_at"):
        item("naive", taken_at=AS_OF.replace(tzinfo=None))
    with pytest.raises(ValueError, match="taken_at"):
        item("offset", taken_at=AS_OF.astimezone(timezone(timedelta(hours=-4))))
    with pytest.raises(ValueError, match="size_bytes"):
        item("negative", size=-1)
    with pytest.raises(ValueError, match="archive_key"):
        item("")
    with pytest.raises(TypeError, match="archive_key"):
        item(1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="job_id"):
        item("a", job_id="")
    with pytest.raises(TypeError, match="job_id"):
        item("a", job_id=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="readable"):
        item("a", readable=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="anomaly"):
        item("a", anomalies=("",))
    with pytest.raises(TypeError, match="anomaly_codes"):
        item("a", anomalies="clock_skew")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="enum"):
        BackupInventoryItem(
            destination="elsewhere",  # type: ignore[arg-type]
            archive_key="a",
            taken_at=AS_OF,
            timestamp_source=BackupTimestampSource.JOB_STARTED_AT,
            size_bytes=1,
        )
    with pytest.raises(ValueError, match="as_of"):
        plan_retention(as_of=AS_OF.replace(tzinfo=None), inventory=(), policy=TIERED)
    for cap in (0, -1, True):
        with pytest.raises(ValueError, match="max_bytes"):
            plan_retention(as_of=AS_OF, inventory=(), policy=TIERED, max_bytes=cap)
    with pytest.raises(TypeError, match="policy"):
        plan_retention(as_of=AS_OF, inventory=(), policy="tiered")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="inventory"):
        plan_retention(as_of=AS_OF, inventory=(object(),), policy=TIERED)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="one destination"):
        plan_retention(
            as_of=AS_OF,
            inventory=(item("a"), item("b", destination=BackupDestination.OFFBOX)),
            policy=TIERED,
        )
    with pytest.raises(ValueError, match="unique"):
        plan_retention(as_of=AS_OF, inventory=(item("a"), item("a")), policy=TIERED)


def test_empty_inventory_has_an_exact_immutable_snapshot() -> None:
    plan = plan_retention(as_of=AS_OF, inventory=(), policy=TIERED, max_bytes=100)
    assert plan.decisions == ()
    assert plan.as_of is AS_OF
    assert plan.policy is TIERED
    assert plan.max_bytes == 100
    with pytest.raises(FrozenInstanceError):
        plan.max_bytes = None  # type: ignore[misc]


def test_exact_recent_daily_weekly_and_expired_boundaries() -> None:
    result = decisions(
        item("newest", age=timedelta(hours=1)),
        item("recent-edge", age=timedelta(days=3)),
        item("daily-start", age=timedelta(days=3, microseconds=1)),
        item("daily-edge", age=timedelta(days=14)),
        item("weekly-start", age=timedelta(days=14, microseconds=1)),
        item("outer-edge", age=timedelta(days=90)),
        item("expired", age=timedelta(days=90, microseconds=1)),
    )
    assert (result["newest"].action, result["newest"].reason) == (
        RetentionAction.KEEP,
        RetentionReason.NEWEST,
    )
    assert result["recent-edge"].reason is RetentionReason.RECENT
    assert result["daily-start"].reason is RetentionReason.DAILY_BUCKET
    assert result["daily-edge"].reason is RetentionReason.DAILY_BUCKET
    assert result["weekly-start"].reason is RetentionReason.WEEKLY_BUCKET
    assert result["outer-edge"].reason is RetentionReason.WEEKLY_BUCKET
    assert (result["expired"].action, result["expired"].reason) == (
        RetentionAction.DELETE,
        RetentionReason.EXPIRED,
    )


def test_newest_occupies_its_bucket_instead_of_allowing_a_second_winner() -> None:
    policy = RetentionPolicy(mode="tiered", keep_all_days=1, daily_until_days=3, weekly_until_days=7)
    newest = item("a", age=timedelta(days=2, hours=1))
    same_day = item("b", age=timedelta(days=2, hours=2))
    plan = plan_retention(as_of=AS_OF, inventory=(same_day, newest), policy=policy)
    result = {decision.archive_key: decision for decision in plan.decisions}
    assert result["a"].reason is RetentionReason.NEWEST
    assert result["b"].reason is RetentionReason.BUCKET_SUPERSEDED


def test_daily_bucket_uses_utc_date_and_newest_item() -> None:
    result = decisions(
        item("newest", age=timedelta(hours=1)),
        item("day-new", taken_at=datetime(2026, 9, 5, 23, 59, tzinfo=UTC)),
        item("day-old", taken_at=datetime(2026, 9, 5, 0, 1, tzinfo=UTC)),
        item("prior-day", taken_at=datetime(2026, 9, 4, 23, 59, tzinfo=UTC)),
    )
    assert (result["day-new"].reason, result["day-new"].bucket_key) == (
        RetentionReason.DAILY_BUCKET,
        "2026-09-05",
    )
    assert (result["day-old"].action, result["day-old"].reason) == (
        RetentionAction.DELETE,
        RetentionReason.BUCKET_SUPERSEDED,
    )
    assert result["prior-day"].bucket_key == "2026-09-04"


def test_weekly_bucket_is_iso_monday_utc_and_keeps_newest() -> None:
    result = decisions(
        item("newest", age=timedelta(hours=1)),
        item("sun-new", taken_at=datetime(2026, 8, 23, 23, 59, tzinfo=UTC)),
        item("mon-old", taken_at=datetime(2026, 8, 17, 0, 0, tzinfo=UTC)),
        item("prior-sun", taken_at=datetime(2026, 8, 16, 23, 59, tzinfo=UTC)),
    )
    assert (result["sun-new"].reason, result["sun-new"].bucket_key) == (
        RetentionReason.WEEKLY_BUCKET,
        "2026-08-17",
    )
    assert result["mon-old"].reason is RetentionReason.BUCKET_SUPERSEDED
    assert result["prior-sun"].bucket_key == "2026-08-10"


def test_equal_timestamps_use_archive_key_ascending_for_newest_and_bucket_winner() -> None:
    same_time = AS_OF - timedelta(days=5)
    plan = plan_retention(
        as_of=AS_OF,
        inventory=(item("z", taken_at=same_time), item("a", taken_at=same_time)),
        policy=TIERED,
    )
    assert [decision.archive_key for decision in plan.decisions] == ["a", "z"]
    assert plan.decisions[0].reason is RetentionReason.NEWEST
    assert plan.decisions[1].reason is RetentionReason.BUCKET_SUPERSEDED


def test_equal_adjacent_horizons_disable_intermediate_bands() -> None:
    no_daily = RetentionPolicy(
        mode="tiered", keep_all_days=3, daily_until_days=3, weekly_until_days=10
    )
    no_weekly = RetentionPolicy(
        mode="tiered", keep_all_days=3, daily_until_days=10, weekly_until_days=10
    )
    plan = plan_retention(
        as_of=AS_OF,
        inventory=(item("new", age=timedelta(hours=1)), item("old", age=timedelta(days=4))),
        policy=no_daily,
    )
    assert plan.decisions[-1].reason is RetentionReason.WEEKLY_BUCKET
    plan = plan_retention(
        as_of=AS_OF,
        inventory=(item("new", age=timedelta(hours=1)), item("old", age=timedelta(days=10))),
        policy=no_weekly,
    )
    assert plan.decisions[-1].reason is RetentionReason.DAILY_BUCKET


@pytest.mark.parametrize(
    "spacing",
    [
        timedelta(minutes=15),
        timedelta(hours=1),
        timedelta(hours=6),
        timedelta(days=1),
        timedelta(days=7),
    ],
)
def test_policy_is_cadence_independent(spacing: timedelta) -> None:
    inventory = tuple(item(str(index), age=spacing * index) for index in range(20))
    plan = plan_retention(as_of=AS_OF, inventory=inventory, policy=TIERED)
    assert len(plan.decisions) == len(inventory)
    assert plan.decisions[0].reason is RetentionReason.NEWEST
    if spacing <= timedelta(hours=1):
        assert all(decision.action is RetentionAction.KEEP for decision in plan.decisions)


def test_sparse_history_does_not_borrow_or_synthesize_buckets() -> None:
    plan = plan_retention(
        as_of=AS_OF,
        inventory=(item("new", age=timedelta(hours=1)), item("weekly", age=timedelta(days=40))),
        policy=TIERED,
    )
    assert len(plan.decisions) == 2
    assert plan.decisions[1].reason is RetentionReason.WEEKLY_BUCKET


def test_keep_all_disables_time_pruning_but_not_cap_pruning() -> None:
    policy = RetentionPolicy(mode="keep_all")
    inventory = (item("new", age=timedelta(days=100), size=10), item("old", age=timedelta(days=200), size=10))
    no_cap = plan_retention(as_of=AS_OF, inventory=inventory, policy=policy)
    assert [(d.action, d.reason) for d in no_cap.decisions] == [
        (RetentionAction.KEEP, RetentionReason.NEWEST),
        (RetentionAction.KEEP, RetentionReason.RECENT),
    ]
    capped = plan_retention(as_of=AS_OF, inventory=inventory, policy=policy, max_bytes=10)
    assert capped.decisions[1].reason is RetentionReason.CAPACITY_LIMIT


def test_future_item_is_protected_and_does_not_become_newest() -> None:
    result = decisions(
        item("future", taken_at=AS_OF + timedelta(microseconds=1), anomalies=("clock_skew",)),
        item("eligible", age=timedelta(days=100)),
    )
    assert (result["future"].action, result["future"].reason) == (
        RetentionAction.KEEP,
        RetentionReason.PROTECTED_ANOMALY,
    )
    assert result["eligible"].reason is RetentionReason.NEWEST


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"status": BackupInventoryStatus.FAILED}, ()),
        ({"status": BackupInventoryStatus.PENDING}, ()),
        ({"status": BackupInventoryStatus.RUNNING}, ()),
        ({"status": BackupInventoryStatus.PRUNED}, ()),
        ({"managed": False}, ()),
        ({"present": False}, ()),
        ({"readable": False}, ()),
        ({"compatible": False}, ()),
        ({"size": 0}, ()),
        ({"anomalies": ("size_mismatch",)}, ("size_mismatch",)),
        ({"anomalies": ("orphan",)}, ("orphan",)),
        ({"anomalies": ("partial",)}, ("partial",)),
        ({"anomalies": ("some_future_anomaly",)}, ("some_future_anomaly",)),
    ],
)
def test_ineligible_statuses_and_anomalies_are_protected(
    overrides: dict[str, object], expected_code: tuple[str, ...]
) -> None:
    anomaly = item("anomaly", age=timedelta(days=200), **overrides)  # type: ignore[arg-type]
    result = decisions(anomaly)
    assert anomaly.anomaly_codes == expected_code
    assert (result["anomaly"].action, result["anomaly"].reason) == (
        RetentionAction.KEEP,
        RetentionReason.PROTECTED_ANOMALY,
    )


def test_remote_archive_without_job_is_independently_eligible() -> None:
    remote = item(
        "remote",
        age=timedelta(days=100),
        destination=BackupDestination.OFFBOX,
        source=BackupTimestampSource.REMOTE_MODIFIED_AT,
        job_id=None,
    )
    plan = plan_retention(as_of=AS_OF, inventory=(remote,), policy=TIERED)
    assert plan.decisions[0].reason is RetentionReason.NEWEST


def test_compatibility_unknown_is_the_only_non_protecting_anomaly_code() -> None:
    unknown = item("old", age=timedelta(days=100), anomalies=("compatibility_unknown",))
    plan = plan_retention(as_of=AS_OF, inventory=(item("new"), unknown), policy=TIERED)
    assert plan.decisions[-1].reason is RetentionReason.EXPIRED


def test_newest_is_kept_when_expired_and_when_larger_than_cap() -> None:
    plan = plan_retention(
        as_of=AS_OF,
        inventory=(item("only", age=timedelta(days=100), size=101),),
        policy=TIERED,
        max_bytes=100,
    )
    assert (plan.decisions[0].action, plan.decisions[0].reason) == (
        RetentionAction.KEEP,
        RetentionReason.NEWEST,
    )


def test_cap_runs_after_bucket_selection_and_removes_oldest_kept_first() -> None:
    inventory = (
        item("new", age=timedelta(hours=1), size=10),
        item("daily", age=timedelta(days=5), size=10),
        item("weekly", age=timedelta(days=20), size=10),
        item("already-superseded", age=timedelta(days=20, hours=1), size=1000),
    )
    result = decisions(*inventory, max_bytes=20)
    assert result["weekly"].reason is RetentionReason.CAPACITY_LIMIT
    assert result["daily"].action is RetentionAction.KEEP
    assert result["already-superseded"].reason is RetentionReason.BUCKET_SUPERSEDED


def test_cap_uses_reverse_stable_order_for_equal_time_removal() -> None:
    same_time = AS_OF - timedelta(hours=1)
    plan = plan_retention(
        as_of=AS_OF,
        inventory=(item("a", taken_at=same_time, size=10), item("z", taken_at=same_time, size=10)),
        policy=RetentionPolicy(mode="keep_all"),
        max_bytes=10,
    )
    assert [(d.archive_key, d.reason) for d in plan.decisions] == [
        ("a", RetentionReason.NEWEST),
        ("z", RetentionReason.CAPACITY_LIMIT),
    ]


def test_protected_bytes_are_excluded_from_logical_cap() -> None:
    inventory = (
        item("new", size=10),
        item("old", age=timedelta(hours=1), size=10),
        item("protected", age=timedelta(hours=2), size=10_000, readable=False),
    )
    result = decisions(*inventory, max_bytes=20)
    assert result["old"].action is RetentionAction.KEEP
    assert result["protected"].reason is RetentionReason.PROTECTED_ANOMALY


def test_cap_removes_all_possible_non_newest_items_when_still_unsatisfied() -> None:
    result = decisions(
        item("new", size=100),
        item("old", age=timedelta(hours=1), size=10),
        max_bytes=50,
    )
    assert result["new"].reason is RetentionReason.NEWEST
    assert result["old"].reason is RetentionReason.CAPACITY_LIMIT


def test_repeated_plan_is_identical_and_does_not_mutate_inventory() -> None:
    inventory = (
        item("new", size=10),
        item("old", age=timedelta(days=20), size=10),
        item("anomaly", age=timedelta(days=100), size=10, readable=False),
    )
    before = tuple(inventory)
    first = plan_retention(as_of=AS_OF, inventory=inventory, policy=TIERED, max_bytes=10)
    second = plan_retention(as_of=AS_OF, inventory=inventory, policy=TIERED, max_bytes=10)
    assert first == second
    assert inventory == before


def test_local_and_offbox_inputs_produce_destination_independent_decisions() -> None:
    local = (item("new"), item("old", age=timedelta(days=100)))
    remote = tuple(
        item(
            archive.archive_key,
            taken_at=archive.taken_at,
            size=archive.size_bytes,
            destination=BackupDestination.OFFBOX,
            source=BackupTimestampSource.REMOTE_MODIFIED_AT,
            job_id=None,
        )
        for archive in local
    )
    local_plan = plan_retention(as_of=AS_OF, inventory=local, policy=TIERED)
    remote_plan = plan_retention(as_of=AS_OF, inventory=remote, policy=TIERED)
    assert local_plan.decisions == remote_plan.decisions

"""The durable executor state machine (ADR 0081 S3): phase transitions,
persisted deadlines, crash-point recovery, commit discipline, restore paths,
fault injection, and the recovery scan."""

from __future__ import annotations

import dataclasses
import fcntl
import os
import signal
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from family_cfo_model_manager import deployment, executor as executor_module
from family_cfo_model_manager import runtime_check
from family_cfo_model_manager.contracts import (
    JobDecision,
    JobOrigin,
    JobState,
    PhotoMode,
    ReasonCode,
    Topology,
)
from family_cfo_model_manager.journal import JournalError
from conftest import (
    DualRunner,
    FakeCompose,
    MAIN,
    REQUEST_ID,
    VISION,
    make_deps,
    previous_runtime_services,
    verify_ok_http,
    write_previous_runtime_env,
)

PREVIOUS_MAIN = "Qwen/Qwen2.5-7B-Instruct"


def make_engine(tmp_path, *, with_previous=False, baseline="target", **deps_overrides):
    """Engine deps over a synthetic project.

    baseline="target": a running runtime matching the requested plan (a
    re-apply with a baseline). baseline="previous": a 7B+vision runtime the
    job can roll back to. baseline="empty": a genuine first deployment with
    nothing running and no runtime keys."""
    if baseline == "empty":
        compose = FakeCompose(services=())
    elif with_previous or baseline == "previous":
        write_previous_runtime_env(tmp_path, main_model=PREVIOUS_MAIN, vision_model=VISION)
        compose = FakeCompose(
            services=previous_runtime_services(main_model=PREVIOUS_MAIN, vision_model=VISION)
        )
    else:
        write_previous_runtime_env(tmp_path, main_model=MAIN, vision_model=None)
        compose = FakeCompose(
            services=previous_runtime_services(main_model=MAIN, vision_model=None)
        )
    overrides = {"runner": DualRunner(), **deps_overrides}
    deps = make_deps(
        tmp_path,
        compose=compose,
        http=verify_ok_http(),
        **overrides,
    )
    return deps


def admit_job(deps, request_id=REQUEST_ID, main_model=MAIN, vision_model=None):
    request = executor_module.AdmissionRequest(
        request_id=request_id,
        main_model=main_model,
        vision_model=vision_model,
        photo_mode=PhotoMode.AUTO,
        topology=Topology.AUTO,
        cluster_consent=False,
        origin=JobOrigin.API,
        runtime_key="vllm+http://api:8000/v1",
    )
    fd = os.open(deps.project_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return executor_module.admit(deps, request, lock_fd=fd)
    finally:
        os.close(fd)


def write_record(deps, record):
    with deps.journal.locked():
        deps.journal.write_job_record(record)


def read_record(deps, job_id):
    return deps.journal.read_job_record(job_id)


def past(timestamp_seconds_ago=1.0):
    return datetime.now(UTC) - timedelta(seconds=timestamp_seconds_ago)


# --- the full happy path ---------------------------------------------------------------


def test_accepted_job_runs_to_succeeded_through_every_phase(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id

    # The API acknowledges while the executor waits in commit_pending.
    def ack_during_wait(seconds):
        assert seconds == executor_module.COMMIT_REVERIFY_SECONDS
        current = read_record(deps, job_id)
        assert current.state is JobState.COMMIT_PENDING
        assert current.decision is JobDecision.COMMIT
        write_record(
            deps, dataclasses.replace(current, config_receipt_id=job_id)
        )

    object.__setattr__(deps, "sleep", ack_during_wait)
    exit_code = executor_module.run_job(deps, job_id)
    assert exit_code == 0

    final = read_record(deps, job_id)
    assert final.state is JobState.SUCCEEDED
    assert final.decision is JobDecision.COMMIT
    assert final.config_receipt_id == job_id
    assert final.phase_deadline_at is None
    assert deps.journal.read_current().job_id is None

    # The frozen runtime values were installed.
    from family_cfo_model_manager.deployment import EnvFile

    env = EnvFile.read(Path(deps.project_dir) / ".env")
    assert env.value("VLLM_MODEL") == MAIN
    assert env.value("VLLM_MAX_MODEL_LEN") == "8192"
    assert env.value("FAMILY_CFO_AI_VISION_ENABLED") == "false"

    # Launch recreated ONLY the managed services; finalization recreated only
    # api/worker with no deps.
    up_services = [call[0] for call in deps.compose.up_calls]
    assert ("vllm", "vllm-vision") in up_services
    assert ("api", "worker") in up_services
    finalize_call = next(call for call in deps.compose.up_calls if call[0] == ("api", "worker"))
    assert finalize_call[2] is True  # no_deps

    # Verification evidence was written and is fresh.
    evidence = deps.journal.read_verification_evidence(job_id)
    assert evidence is not None
    assert evidence["main_served_name"] == MAIN
    assert "text" in evidence["probes"] and "tool" in evidence["probes"]

    # The executor set the durable commit decision itself, with evidence.
    assert final.target_plan_hash is not None


def test_verification_evidence_is_written_at_commit_pending(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    # Stop after the verify phase writes commit_pending: the sleep callback
    # never acks, so instead drive the phases directly to the commit point.
    captured = {}

    real_commit_pending = executor_module._phase_commit_pending

    def stop_at_commit_pending(deps_, record_, state):
        captured["record"] = deps_.journal.read_job_record(record_.job_id)
        captured["evidence"] = deps_.journal.read_verification_evidence(record_.job_id)
        raise KeyboardInterrupt()

    object.__setattr__(
        executor_module, "_phase_commit_pending", stop_at_commit_pending
    )
    try:
        with pytest.raises(KeyboardInterrupt):
            executor_module.run_job(deps, record.job_id)
    finally:
        object.__setattr__(executor_module, "_phase_commit_pending", real_commit_pending)

    current = captured["record"]
    assert current.state is JobState.COMMIT_PENDING
    assert current.decision is JobDecision.COMMIT  # set BY the executor
    evidence = captured["evidence"]
    assert evidence is not None
    verified_at = datetime.fromisoformat(evidence["verified_at"])
    assert (datetime.now(UTC) - verified_at).total_seconds() < 30


# --- persisted deadlines -----------------------------------------------------------------


def test_staging_deadline_is_never_renewed_on_restart(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    # Simulate a restart long after the phase deadline expired.
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, record.job_id), phase_deadline_at=past(3600)
        ),
    )
    exit_code = executor_module.run_job(deps, record.job_id)
    assert exit_code == 1
    failed = read_record(deps, record.job_id)
    assert failed.state is JobState.FAILED
    assert failed.failure_code is ReasonCode.INSUFFICIENT_DISK
    assert "staging deadline expired" in failed.safe_message
    assert deps.journal.read_current().job_id is None
    # The deadline was NOT re-stamped by the restart.
    assert failed.phase_deadline_at < datetime.now(UTC)


def test_launch_deadline_expiry_restores_after_mutation(tmp_path):
    deps = make_engine(tmp_path, with_previous=True)
    record = admit_job(deps, main_model=MAIN, vision_model=None)
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, record.job_id),
            state=JobState.LAUNCHING,
            decision=JobDecision.UNDECIDED,
            phase_deadline_at=past(3600),
        ),
    )
    exit_code = executor_module.run_job(deps, record.job_id)
    assert exit_code == 0
    restored = read_record(deps, record.job_id)
    assert restored.state is JobState.RESTORED
    # The previous runtime's values are back in the env file.
    from family_cfo_model_manager.deployment import EnvFile

    env = EnvFile.read(Path(deps.project_dir) / ".env")
    assert env.value("VLLM_MODEL") == PREVIOUS_MAIN


# --- crash-point recovery ----------------------------------------------------------------


def test_crash_in_verifying_with_undecided_decision_restores(tmp_path):
    deps = make_engine(tmp_path, with_previous=True)
    record = admit_job(deps, main_model=MAIN, vision_model=None)
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, record.job_id),
            state=JobState.VERIFYING,
            decision=JobDecision.UNDECIDED,
            phase_deadline_at=datetime.now(UTC) + timedelta(seconds=600),
        ),
    )
    exit_code = executor_module.run_job(deps, record.job_id)
    assert exit_code == 0
    restored = read_record(deps, record.job_id)
    assert restored.state is JobState.RESTORED
    assert restored.decision is JobDecision.RESTORE  # the safe direction
    assert deps.journal.read_current().job_id is None


def test_crash_in_staging_resumes_and_completes(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.STAGING,
            phase_deadline_at=datetime.now(UTC) + timedelta(seconds=600),
        ),
    )

    def ack_during_wait(seconds):
        current = read_record(deps, job_id)
        write_record(deps, dataclasses.replace(current, config_receipt_id=job_id))

    object.__setattr__(deps, "sleep", ack_during_wait)
    assert executor_module.run_job(deps, job_id) == 0
    assert read_record(deps, job_id).state is JobState.SUCCEEDED


def test_terminal_records_do_not_resume(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    for terminal in (JobState.SUCCEEDED, JobState.FAILED, JobState.RESTORED):
        write_record(
            deps,
            dataclasses.replace(
                read_record(deps, record.job_id),
                state=terminal,
                phase_deadline_at=None,
            ),
        )
        assert executor_module.run_job(deps, record.job_id) == 0


# --- commit discipline --------------------------------------------------------------------


def test_commit_pending_finalizes_on_receipt(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.COMMIT_PENDING,
            decision=JobDecision.COMMIT,
            config_receipt_id=job_id,
            phase_deadline_at=None,
        ),
    )
    assert executor_module.run_job(deps, job_id) == 0
    final = read_record(deps, job_id)
    assert final.state is JobState.SUCCEEDED
    assert deps.journal.read_current().job_id is None
    # Finalization recreated api/worker without dependencies.
    finalize_call = next(
        call for call in deps.compose.up_calls if call[0] == ("api", "worker")
    )
    assert finalize_call[2] is True


def test_commit_pending_never_rolls_back_on_failure(tmp_path):
    deps = make_engine(tmp_path, with_previous=True)
    record = admit_job(deps, main_model=MAIN, vision_model=None)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.COMMIT_PENDING,
            decision=JobDecision.COMMIT,
            config_receipt_id=job_id,
        ),
    )
    # The routing check will fail: the api service does not route to the
    # committed model. The executor must go FORWARD (repair), never back.
    deps.compose.services = previous_runtime_services(
        main_model=PREVIOUS_MAIN, vision_model=VISION
    )
    exit_code = executor_module.run_job(deps, job_id)
    assert exit_code == 1
    current = read_record(deps, job_id)
    assert current.state is JobState.COMMIT_REPAIR_REQUIRED
    assert current.decision is JobDecision.COMMIT  # monotonic
    assert "routing" in current.safe_message or "chat routing" in current.safe_message


def test_commit_pending_keeps_evidence_fresh_while_waiting(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.COMMIT_PENDING,
            decision=JobDecision.COMMIT,
            phase_deadline_at=None,
        ),
    )
    # One re-verify, then the ack lands; the loop must notice it.
    def ack_after_first_reverify(seconds):
        assert executor_module.COMMIT_REVERIFY_SECONDS == 15.0
        current = read_record(deps, job_id)
        write_record(deps, dataclasses.replace(current, config_receipt_id=job_id))

    object.__setattr__(deps, "sleep", ack_after_first_reverify)
    assert executor_module.run_job(deps, job_id) == 0
    assert read_record(deps, job_id).state is JobState.SUCCEEDED


def test_journal_lock_is_released_while_waiting_for_the_ack(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.COMMIT_PENDING,
            decision=JobDecision.COMMIT,
            phase_deadline_at=None,
        ),
    )
    lock_path = deps.journal.lock_path

    def lock_must_be_free_during_sleep(seconds):
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # must not block
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        current = read_record(deps, job_id)
        write_record(deps, dataclasses.replace(current, config_receipt_id=job_id))

    object.__setattr__(deps, "sleep", lock_must_be_free_during_sleep)
    assert executor_module.run_job(deps, job_id) == 0


def test_forward_repair_reruns_finalization_only(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.COMMIT_REPAIR_REQUIRED,
            decision=JobDecision.COMMIT,
            phase_deadline_at=None,
        ),
    )
    # api routes to the committed model now: the repair succeeds forward.
    deps.compose.services = previous_runtime_services(main_model=MAIN, vision_model=None)
    assert executor_module.run_job(deps, job_id) == 0
    final = read_record(deps, job_id)
    assert final.state is JobState.SUCCEEDED


def test_forward_repair_never_rolls_back_a_restore_decision(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.COMMIT_REPAIR_REQUIRED,
            decision=JobDecision.RESTORE,
            phase_deadline_at=None,
        ),
    )
    # A repair-required record without a commit decision cannot finalize.
    assert executor_module.run_job(deps, job_id) == 1
    assert read_record(deps, job_id).state is JobState.COMMIT_REPAIR_REQUIRED


# --- restore paths --------------------------------------------------------------------------


def test_empty_baseline_restore_fails_with_no_previous_runtime(tmp_path):
    deps = make_engine(tmp_path, baseline="empty")
    record = admit_job(deps)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.RESTORING,
            decision=JobDecision.RESTORE,
            phase_deadline_at=datetime.now(UTC) + timedelta(seconds=600),
        ),
    )
    assert executor_module.run_job(deps, job_id) == 1
    failed = read_record(deps, job_id)
    assert failed.state is JobState.FAILED
    assert failed.failure_code is ReasonCode.NO_PREVIOUS_RUNTIME
    assert deps.journal.read_current().job_id is None
    # The candidate-owned services were stopped and no runtime keys remain.
    assert deps.compose.stop_calls == [("vllm", "vllm-vision")]
    from family_cfo_model_manager.deployment import EnvFile

    env = EnvFile.read(Path(deps.project_dir) / ".env")
    assert env.value("VLLM_MODEL") is None


def test_restore_failure_marks_recovery_failed_and_stays_recoverable(tmp_path):
    deps = make_engine(tmp_path, with_previous=True)
    record = admit_job(deps, main_model=MAIN, vision_model=None)
    job_id = record.job_id

    class BrokenRestoreCompose(FakeCompose):
        def up(self, project_dir, files, services, *, scale, no_deps=False):
            if "vllm" in services:  # the restore recreation
                raise deployment.DeploymentError("docker up failed")
            return super().up(project_dir, files, services, scale=scale, no_deps=no_deps)

    object.__setattr__(deps, "compose", BrokenRestoreCompose(services=deps.compose.services))
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.RESTORING,
            decision=JobDecision.RESTORE,
            phase_deadline_at=datetime.now(UTC) + timedelta(seconds=600),
        ),
    )
    exit_code = executor_module.run_job(deps, job_id)
    assert exit_code == 1
    current = read_record(deps, job_id)
    assert current.state is JobState.RECOVERY_FAILED
    assert current.state.recoverable
    assert "docker up failed" in current.safe_message
    # The pointer is retained so recovery can find the job.
    assert deps.journal.read_current().job_id == job_id


def test_restored_runtime_is_reverified_with_the_full_sequence(tmp_path):
    deps = make_engine(tmp_path, with_previous=True)
    record = admit_job(deps, main_model=MAIN, vision_model=None)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.RESTORING,
            decision=JobDecision.RESTORE,
            phase_deadline_at=datetime.now(UTC) + timedelta(seconds=600),
        ),
    )
    assert executor_module.run_job(deps, job_id) == 0
    restored = read_record(deps, job_id)
    assert restored.state is JobState.RESTORED
    evidence = deps.journal.read_verification_evidence(job_id)
    assert evidence["main_served_name"] == PREVIOUS_MAIN
    assert evidence["photo_mode"] == "separate"


# --- fault injection -------------------------------------------------------------------------


def test_journal_write_failure_stops_without_mutation(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    real_write = deps.journal.write_job_record

    def failing_write(record_):
        raise JournalError(ReasonCode.JOURNAL_UNAVAILABLE, "disk full")

    object.__setattr__(deps.journal, "write_job_record", failing_write)
    exit_code = executor_module.run_job(deps, job_id)
    object.__setattr__(deps.journal, "write_job_record", real_write)
    assert exit_code == 3
    diagnostics_path = (
        Path(deps.project_dir) / ".runtime-swap" / "v2" / "jobs" / job_id / "diagnostics.log"
    )
    assert diagnostics_path.exists()
    assert "journal_failure" in diagnostics_path.read_text()


def test_launch_failure_restores_the_previous_runtime(tmp_path):
    deps = make_engine(tmp_path, with_previous=True)
    record = admit_job(deps, main_model=MAIN, vision_model=None)
    job_id = record.job_id

    class FlakyCompose(FakeCompose):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.up_count = 0

        def up(self, project_dir, files, services, *, scale, no_deps=False):
            self.up_count += 1
            if self.up_count == 1:  # the launch call (from ACCEPTED)
                raise deployment.DeploymentError("compose up exploded")
            return super().up(project_dir, files, services, scale=scale, no_deps=no_deps)

    object.__setattr__(deps, "compose", FlakyCompose(services=deps.compose.services))
    # From ACCEPTED the executor stages, launches (fails), and restores.
    assert executor_module.run_job(deps, job_id) == 0
    restored = read_record(deps, job_id)
    assert restored.state is JobState.RESTORED
    from family_cfo_model_manager.deployment import EnvFile

    env = EnvFile.read(Path(deps.project_dir) / ".env")
    assert env.value("VLLM_MODEL") == PREVIOUS_MAIN


def test_staging_container_failure_is_terminal_before_any_mutation(tmp_path):
    class FailingStagingRunner(DualRunner):
        def run(self, spec, timeout):
            labels = dict(spec.labels or ())
            if labels.get("family.cfo.apply") == "staging":
                return runtime_check.ContainerResult(
                    exit_code=1, stdout="", stderr="download failed"
                )
            return super().run(spec, timeout)

    deps = make_engine(tmp_path, runner=FailingStagingRunner())
    record = admit_job(deps)
    env_before = (Path(deps.project_dir) / ".env").read_bytes()
    exit_code = executor_module.run_job(deps, record.job_id)
    assert exit_code == 1
    failed = read_record(deps, record.job_id)
    assert failed.state is JobState.FAILED
    assert failed.failure_code is ReasonCode.PREFLIGHT_UNAVAILABLE
    assert "staging could not prepare the model cache" in failed.safe_message
    assert (Path(deps.project_dir) / ".env").read_bytes() == env_before
    assert deps.compose.up_calls == []


# --- interruption ---------------------------------------------------------------------------


def test_interrupted_restore_before_commit_restores_and_marks_restore(tmp_path):
    deps = make_engine(tmp_path, with_previous=True)
    record = admit_job(deps, main_model=MAIN, vision_model=None)
    current = read_record(deps, record.job_id)
    exit_code = executor_module._interrupted_restore(deps, current)
    assert exit_code == 0
    restored = read_record(deps, record.job_id)
    assert restored.state is JobState.RESTORED
    assert restored.decision is JobDecision.RESTORE


def test_interrupted_after_commit_detaches_without_rollback(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id), decision=JobDecision.COMMIT
        ),
    )
    current = read_record(deps, job_id)
    assert executor_module._interrupted_restore(deps, current) == 0
    unchanged = read_record(deps, job_id)
    assert unchanged.state is JobState.ACCEPTED
    assert unchanged.decision is JobDecision.COMMIT


def test_sigterm_before_commit_stops_child_work_and_restores(tmp_path):
    deps = make_engine(tmp_path, with_previous=True)
    record = admit_job(deps, main_model=MAIN, vision_model=None)
    job_id = record.job_id

    proceed = threading.Event()

    class BlockingRunner(DualRunner):
        def run(self, spec, timeout):
            labels = dict(spec.labels or ())
            if labels.get("family.cfo.apply") == "staging":
                proceed.wait(5)  # the signal must land here
                return runtime_check.ContainerResult(exit_code=0, stdout="{}", stderr="")
            return super().run(spec, timeout)

    object.__setattr__(deps, "runner", BlockingRunner())

    def send_sigterm():
        os.kill(os.getpid(), signal.SIGTERM)

    timer = threading.Timer(0.3, send_sigterm)
    timer.start()
    try:
        exit_code = executor_module.run_job(deps, job_id)
    finally:
        timer.join(timeout=2)
        proceed.set()
    assert exit_code == 0
    restored = read_record(deps, job_id)
    assert restored.state is JobState.RESTORED
    assert restored.decision is JobDecision.RESTORE


# --- the recovery scan ------------------------------------------------------------------------


def test_scan_resumes_an_unfinished_job_with_a_free_lock(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    spawned = []
    object.__setattr__(
        deps,
        "spawn",
        lambda deps_, job_id, fd, **kw: spawned.append((job_id, os.dup(fd))) or Mock(pid=1),
    )
    report = executor_module.scan_and_recover(deps)
    assert report.resumed == (record.job_id,)
    assert report.held_by_owner == ()
    assert report.awaiting_attention == ()
    assert spawned and spawned[0][0] == record.job_id
    for _job, fd in spawned:
        os.close(fd)


def test_scan_never_supersedes_a_lock_holding_owner(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    fd = os.open(deps.project_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        spawned = []
        object.__setattr__(deps, "spawn", lambda d, j, f, **kw: spawned.append(j) or Mock(pid=1))
        report = executor_module.scan_and_recover(deps)
        assert report.held_by_owner == (record.job_id,)
        assert report.resumed == ()
        assert spawned == []
    finally:
        os.close(fd)


def test_scan_routes_recoverable_states_to_attention(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    for state in (JobState.RECOVERY_FAILED, JobState.COMMIT_REPAIR_REQUIRED):
        write_record(
            deps,
            dataclasses.replace(read_record(deps, record.job_id), state=state),
        )
        report = executor_module.scan_and_recover(deps)
        assert report.awaiting_attention == (record.job_id,)
        assert report.resumed == ()


def test_scan_transitions_undecided_launching_to_restore_before_resuming(tmp_path):
    deps = make_engine(tmp_path, with_previous=True)
    record = admit_job(deps, main_model=MAIN, vision_model=None)
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, record.job_id),
            state=JobState.LAUNCHING,
            decision=JobDecision.UNDECIDED,
        ),
    )
    spawned = []
    object.__setattr__(deps, "spawn", lambda d, j, f, **kw: spawned.append(j) or Mock(pid=1))
    report = executor_module.scan_and_recover(deps)
    assert report.resumed == (record.job_id,)
    current = read_record(deps, record.job_id)
    assert current.state is JobState.RESTORING
    assert current.decision is JobDecision.RESTORE


def test_scan_ignores_terminal_records(tmp_path):
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, record.job_id), state=JobState.SUCCEEDED
        ),
    )
    report = executor_module.scan_and_recover(deps)
    assert report.resumed == report.held_by_owner == report.awaiting_attention == ()


def test_scan_spawn_failure_propagates(tmp_path):
    deps = make_engine(tmp_path)
    admit_job(deps)

    def failing_spawn(deps_, job_id, fd, **kw):
        raise RuntimeError("cannot fork")  # the scan's finally closes the fd

    object.__setattr__(deps, "spawn", failing_spawn)
    with pytest.raises(executor_module.ExecutorRefusal) as exc_info:
        executor_module.scan_and_recover(deps)
    assert exc_info.value.reason_code is ReasonCode.EXECUTOR_START_FAILED

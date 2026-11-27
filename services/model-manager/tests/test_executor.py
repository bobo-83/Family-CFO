"""The durable executor state machine (ADR 0081 S3): phase transitions,
persisted deadlines, crash-point recovery, commit discipline, restore paths,
fault injection, and the recovery scan."""

from __future__ import annotations

import dataclasses
import fcntl
import http.server
import json
import os
import signal
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from family_cfo_model_manager import deployment, executor as executor_module
from family_cfo_model_manager import runtime_check
from family_cfo_model_manager import verification as verification_module
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
    FakeHttp,
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


# --- the runtime HTTP adapter contract (PR #180 review finding 1) -----------------------


class _ShapeServer(http.server.BaseHTTPRequestHandler):
    """Canned endpoint shapes: 200 + empty body (vLLM's /health), 200 + JSON,
    200 + non-JSON text, 404, and an empty-body POST."""

    def do_GET(self):
        if self.path.endswith("/empty"):
            self.send_response(200)
            self.end_headers()
        elif self.path.endswith("/json"):
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.endswith("/text"):
            body = b"service ready"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        # Consume the request body BEFORE responding: closing the socket
        # with unread request data sends RST, which the client can observe
        # instead of the response - a spurious transport failure (0, None).
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args):
        pass


def _shape_http_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _ShapeServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    # Deterministic readiness: the kernel completes the handshake into the
    # listen backlog before serve_forever() polls accept(), so a client call
    # issued immediately can race the server thread's first poll. Gate on a
    # real round trip so the assertions below are served responses, never a
    # connect/reset artifact masquerading as (0, None).
    deadline = time.monotonic() + 10.0
    while True:
        try:
            with urllib.request.urlopen(f"{base}/empty", timeout=1.0) as response:
                if response.status == 200:
                    break
        except urllib.error.HTTPError:
            break  # the server answers; that is all readiness requires
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
    return server, base


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_runtime_http_reports_known_statuses_with_unusable_bodies():
    """PR #180 review finding 1 (the reviewer's reproduction against a local
    HTTP server): vLLM's /health answers 200 with an EMPTY body. The adapter
    must return the KNOWN status with body=None - collapsing that into
    (0, None) made a healthy deployment fail baseline verification as
    previous_runtime_not_ready."""
    server, base = _shape_http_server()
    try:
        http = executor_module.UrllibRuntimeHttp()
        assert http.get_json(f"{base}/empty", 5.0) == (200, None)
        assert http.get_json(f"{base}/json", 5.0) == (200, {"ok": True})
        # A non-JSON body on a 2xx is still a known status, not transport loss.
        assert http.get_json(f"{base}/text", 5.0) == (200, None)
        assert http.get_json(f"{base}/missing", 5.0) == (404, None)
        assert http.post_json(f"{base}/empty", {}, 5.0) == (200, None)
    finally:
        server.shutdown()
        server.server_close()


def test_runtime_http_transport_failures_stay_zero():
    server, _base = _shape_http_server()
    try:
        http = executor_module.UrllibRuntimeHttp()
        port = _closed_port()  # nothing listens there: a genuine transport error
        assert http.get_json(f"http://127.0.0.1:{port}/empty", 5.0) == (0, None)
        assert http.post_json(f"http://127.0.0.1:{port}/empty", {}, 5.0) == (0, None)
    finally:
        server.shutdown()
        server.server_close()


def test_baseline_accepts_a_healthy_empty_body_health(tmp_path):
    """The reviewer's reproduction, end to end: a healthy runtime whose
    /health answers 200 with an empty body is adoptable as a rollback
    baseline - not a previous_runtime_not_ready refusal."""
    ok = verify_ok_http()

    def handler(method, url, payload):
        if url.endswith("/health"):
            return (200, None)  # empty body; the status is the signal
        return ok.handler(method, url, payload)

    deps = make_engine(tmp_path)
    object.__setattr__(deps, "http", FakeHttp(handler=handler))
    record = admit_job(deps)  # raised ExecutorRefusal before the fix
    assert read_record(deps, record.job_id).state is JobState.ACCEPTED


def test_baseline_still_refuses_a_genuine_transport_failure(tmp_path):
    ok = verify_ok_http()

    def handler(method, url, payload):
        if url.endswith("/health"):
            return (0, None)  # real transport failure, not an empty body
        return ok.handler(method, url, payload)

    deps = make_engine(tmp_path)
    object.__setattr__(deps, "http", FakeHttp(handler=handler))
    with pytest.raises(executor_module.ExecutorRefusal) as excinfo:
        admit_job(deps)
    assert excinfo.value.reason_code is ReasonCode.PREVIOUS_RUNTIME_NOT_READY


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
    # The running runtime serves a different main model, so the FRESH
    # candidate verification finalization now runs first (finding 1: never
    # finalize a dead or wrong candidate) fails - and either way the
    # executor must go FORWARD (repair), never back.
    deps.compose.services = previous_runtime_services(
        main_model=PREVIOUS_MAIN, vision_model=VISION
    )
    exit_code = executor_module.run_job(deps, job_id)
    assert exit_code == 1
    current = read_record(deps, job_id)
    assert current.state is JobState.COMMIT_REPAIR_REQUIRED
    assert current.decision is JobDecision.COMMIT  # monotonic
    assert "serves a different main model" in current.safe_message


def test_commit_pending_never_rolls_back_on_routing_failure(tmp_path):
    """The candidate itself verifies, but application routing was never
    repointed: the routing check still guards finalization."""
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
    # The candidate serves the committed model (verification passes), but
    # the api service still routes to the previous model.
    services = list(previous_runtime_services(main_model=MAIN, vision_model=None))
    api = next(s for s in services if s.name == "api")
    stale_api = dataclasses.replace(
        api, env={**api.env, "FAMILY_CFO_AI_MODEL": PREVIOUS_MAIN}
    )
    deps.compose.services = tuple(
        stale_api if s.name == "api" else s for s in services
    )
    ok = verify_ok_http()

    def handler(method, url, payload):
        if url.endswith("/api/v1/health"):
            return (200, {"status": "ok"})
        return ok.handler(method, url, payload)

    object.__setattr__(deps, "http", FakeHttp(handler=handler))
    exit_code = executor_module.run_job(deps, job_id)
    assert exit_code == 1
    current = read_record(deps, job_id)
    assert current.state is JobState.COMMIT_REPAIR_REQUIRED
    assert current.decision is JobDecision.COMMIT  # monotonic
    assert "chat routing" in current.safe_message


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
            config_receipt_id=job_id,
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


def test_commit_pending_deadline_expiry_lands_in_forward_repair(tmp_path):
    """Finding 2 (PR #180 review): a candidate that dies while the API is
    unavailable must not strand commit_pending forever. The persisted phase
    deadline bounds the wait; expiring WITHOUT a receipt lands in
    commit_repair_required - the durable commit decision is never rolled
    back."""
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.COMMIT_PENDING,
            decision=JobDecision.COMMIT,
            phase_deadline_at=past(60.0),  # expired a minute ago
        ),
    )
    sleeps: list[float] = []
    object.__setattr__(deps, "sleep", sleeps.append)
    assert executor_module.run_job(deps, job_id) == 1
    current = read_record(deps, job_id)
    assert current.state is JobState.COMMIT_REPAIR_REQUIRED
    assert current.decision is JobDecision.COMMIT  # monotonic: never rolled back
    assert "deadline expired" in current.safe_message
    assert sleeps == []  # exhausted before any further re-verify cadence


def test_a_late_receipt_still_finalizes_after_deadline_expiry(tmp_path):
    """The receipt is checked before the deadline: a commit that landed late
    is finalized, not bounced to repair."""
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
            phase_deadline_at=past(60.0),
        ),
    )
    assert executor_module.run_job(deps, job_id) == 0
    assert read_record(deps, job_id).state is JobState.SUCCEEDED


def test_forward_repair_without_a_receipt_reawaits_the_ack(tmp_path):
    """Finalizing without a receipt would report succeeded while the
    household settings were never written (finding 2). Without a receipt,
    forward repair re-enters the commit wait with a fresh bounded window so
    the reconciler can still execute the durable commit decision."""
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.COMMIT_REPAIR_REQUIRED,
            decision=JobDecision.COMMIT,
            phase_deadline_at=past(60.0),
        ),
    )
    observed_states: list[JobState] = []
    observed_deadline: list[datetime] = []

    def ack_during_wait(seconds):
        current = read_record(deps, job_id)
        observed_states.append(current.state)
        observed_deadline.append(current.phase_deadline_at)
        write_record(deps, dataclasses.replace(current, config_receipt_id=job_id))

    object.__setattr__(deps, "sleep", ack_during_wait)
    assert executor_module.run_job(deps, job_id) == 0
    assert read_record(deps, job_id).state is JobState.SUCCEEDED
    # The wait ran as commit_pending on a FRESH bounded deadline: an explicit
    # retry is a new attempt, not a restart, and never inherits a spent one.
    assert observed_states == [JobState.COMMIT_PENDING]
    assert observed_deadline[0] is not None
    assert observed_deadline[0] > datetime.now(UTC)


# --- replay verification + forward-repair relaunch (PR #180 review findings 1+2) ---------


def _admit_in_repair_state(deps, job_id):
    """A job whose commit window expired without a receipt: the durable
    commit decision stands, the job waits for explicit forward repair."""
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.COMMIT_REPAIR_REQUIRED,
            decision=JobDecision.COMMIT,
            phase_deadline_at=past(60.0),
        ),
    )


def test_replay_with_a_dead_candidate_is_never_succeeded(tmp_path):
    """Finding 1: the replay path (receipt landed, original ack lost) must
    re-verify the frozen candidate BEFORE reporting success. A candidate
    that died after the SQL commit lands in commit_repair_required - the
    receipt stays authoritative for the SQL side, but the job state reflects
    reality and recovery stays available."""
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
    ok = verify_ok_http()
    probes = {"count": 0}

    def handler(method, url, payload):
        if url.endswith("/health"):
            probes["count"] += 1
            return (0, None)  # the candidate is DOWN
        return ok.handler(method, url, payload)

    object.__setattr__(deps, "http", FakeHttp(handler=handler))
    assert executor_module.run_job(deps, job_id) == 1
    current = read_record(deps, job_id)
    assert current.state is not JobState.SUCCEEDED
    assert current.state is JobState.COMMIT_REPAIR_REQUIRED
    assert current.decision is JobDecision.COMMIT  # monotonic: never rolled back
    assert current.state.recoverable  # recovery available
    assert deps.journal.read_current().job_id == job_id  # ownership held for repair
    # The failure came from FRESH runtime verification, not just the routing
    # check: the candidate's health endpoint was actually probed.
    assert probes["count"] > 0


def test_replay_with_a_live_candidate_finalizes(tmp_path):
    """Finding 1, the healthy side: fresh verification passes, so the replay
    path finalizes to succeeded exactly as before."""
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
    # Fresh evidence was persisted by the pre-finalization verification.
    evidence = deps.journal.read_verification_evidence(job_id)
    assert evidence is not None
    assert evidence["main_served_name"] == MAIN


def test_forward_repair_relaunches_the_frozen_candidate(tmp_path):
    """Finding 2: a candidate that stays down before the SQL commit cannot
    be recovered by re-probing - forward repair relaunches the frozen
    candidate from the journal's durable target/plan record (never re-plans),
    re-verifies it, and re-enters the commit wait."""
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    _admit_in_repair_state(deps, job_id)
    # The frozen plan's revision is "8b5e6c1f". Point the metadata source at
    # a DIFFERENT revision: if repair re-planned, the relaunch would install
    # the new revision; the journal-frozen relaunch must keep the old one.
    from family_cfo_model_manager.contracts import Evidence
    from family_cfo_model_manager.planning import ModelFacts

    class DriftedMetadata:
        def facts_for(self, repo_id):
            return ModelFacts(
                repo_id=repo_id,
                revision="deadbeef",
                architecture="Qwen2ForCausalLM",
                resident_weight_bytes=8_000_000_000,
                multimodal=False,
                model_context_limit=32768,
                kv_bytes_per_token=1_000_000,
                evidence=Evidence.VERIFIED,
            )

    object.__setattr__(deps, "metadata", DriftedMetadata())
    # The candidate is DOWN until the relaunch recreates it: verification
    # probes fail until the managed services have been (re)launched.
    ok = verify_ok_http()

    def handler(method, url, payload):
        if not any(
            call[0] == ("vllm", "vllm-vision") for call in deps.compose.up_calls
        ):
            return (0, None)  # down before the relaunch
        return ok.handler(method, url, payload)

    object.__setattr__(deps, "http", FakeHttp(handler=handler))

    # The reconciler acks during the re-entered commit wait.
    def ack_during_wait(seconds):
        current = read_record(deps, job_id)
        if current.state is JobState.COMMIT_PENDING:
            write_record(
                deps, dataclasses.replace(current, config_receipt_id=job_id)
            )

    object.__setattr__(deps, "sleep", ack_during_wait)
    assert executor_module.run_job(deps, job_id) == 0
    final = read_record(deps, job_id)
    assert final.state is JobState.SUCCEEDED
    assert final.decision is JobDecision.COMMIT
    # The relaunch drove the SAME compose launch path with the frozen plan:
    # managed services recreated (scale from the plan) and the journal's
    # frozen revision installed - not the drifted metadata's revision.
    relaunch_calls = [
        call for call in deps.compose.up_calls if call[0] == ("vllm", "vllm-vision")
    ]
    assert len(relaunch_calls) == 1
    from family_cfo_model_manager.deployment import EnvFile

    env = EnvFile.read(Path(deps.project_dir) / ".env")
    assert env.value("VLLM_MODEL") == MAIN
    assert env.value("VLLM_REVISION") == "8b5e6c1f"  # frozen, not "deadbeef"


def test_forward_repair_relaunch_failure_stays_bounded(tmp_path):
    """Finding 2: when the relaunch keeps failing, exactly ONE relaunch
    attempt happens per repair invocation and the job stays in
    commit_repair_required - never a false success, never a rollback, never
    an unbounded relaunch loop."""
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    _admit_in_repair_state(deps, job_id)

    class BrokenRelaunchCompose(FakeCompose):
        def up(self, project_dir, files, services, *, scale, no_deps=False):
            if "vllm" in services:  # every relaunch attempt fails
                # The attempt happened (recorded) even though compose failed.
                self.up_calls.append((services, dict(scale), no_deps))
                raise deployment.DeploymentError("docker up failed")
            return super().up(project_dir, files, services, scale=scale, no_deps=no_deps)

    object.__setattr__(deps, "compose", BrokenRelaunchCompose(services=deps.compose.services))
    sleeps: list[float] = []
    object.__setattr__(deps, "sleep", sleeps.append)
    assert executor_module.run_job(deps, job_id) == 1
    current = read_record(deps, job_id)
    assert current.state is JobState.COMMIT_REPAIR_REQUIRED
    assert current.decision is JobDecision.COMMIT  # never rolled back
    assert "relaunch failed" in current.safe_message
    assert current.state.recoverable
    # One bounded attempt - no relaunch loop.
    assert len(deps.compose.up_calls) == 1
    # No household settings were touched: no receipt exists and the executor
    # never finalized.
    assert current.config_receipt_id is None
    assert not any(call[0] == ("api", "worker") for call in deps.compose.up_calls)


def test_forward_repair_verification_failure_stays_bounded(tmp_path):
    """Finding 2: a relaunched candidate that still fails verification is
    bounded too - one relaunch, one bounded verification, then the durable
    forward-repair state with safe diagnostics."""
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    _admit_in_repair_state(deps, job_id)
    ok = verify_ok_http()

    def handler(method, url, payload):
        if url.endswith("/health"):
            return (0, None)  # the relaunched candidate never comes up
        return ok.handler(method, url, payload)

    object.__setattr__(deps, "http", FakeHttp(handler=handler))
    sleeps: list[float] = []
    object.__setattr__(deps, "sleep", sleeps.append)
    assert executor_module.run_job(deps, job_id) == 1
    current = read_record(deps, job_id)
    assert current.state is JobState.COMMIT_REPAIR_REQUIRED
    assert current.decision is JobDecision.COMMIT
    assert current.state.recoverable
    relaunch_calls = [
        call for call in deps.compose.up_calls if call[0] == ("vllm", "vllm-vision")
    ]
    assert len(relaunch_calls) == 1  # bounded: no loop
    assert not any(call[0] == ("api", "worker") for call in deps.compose.up_calls)


# --- finalization readiness (PR #180 review finding 3) ----------------------------------


def _admit_at_commit_pending_with_receipt(deps, job_id, *, deadline_at):
    write_record(
        deps,
        dataclasses.replace(
            read_record(deps, job_id),
            state=JobState.COMMIT_PENDING,
            decision=JobDecision.COMMIT,
            config_receipt_id=job_id,
            phase_deadline_at=deadline_at,
        ),
    )


def test_finalization_waits_for_a_slow_api_start(tmp_path):
    """Finding 3 (PR #180 review): the recreated api opens its socket only
    after its PostgreSQL wait and migrations (docker/entrypoint-api.sh), so a
    refused connection in that window is a normal restart. Finalization polls
    within the phase budget instead of failing on the first probe."""
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    _admit_at_commit_pending_with_receipt(
        deps, job_id, deadline_at=datetime.now(UTC) + timedelta(seconds=600)
    )
    ok = verify_ok_http()
    attempts = {"health": 0}

    def handler(method, url, payload):
        if url.endswith("/api/v1/health"):
            attempts["health"] += 1
            if attempts["health"] < 3:
                return (0, None)  # refused during the restart window
        return ok.handler(method, url, payload)

    object.__setattr__(deps, "http", FakeHttp(handler=handler))
    assert executor_module.run_job(deps, job_id) == 0
    assert read_record(deps, job_id).state is JobState.SUCCEEDED
    assert attempts["health"] == 3


class _AdvancingClock:
    """Injectable monotonic clock; simulated time only moves when something
    advances it - sleeps, or a handler modeling a slow request."""


    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_finalization_readiness_wait_is_clipped_to_the_phase_budget(tmp_path):
    """Attempts stop when the phase budget is spent; the job lands in the
    documented forward-repair state, never a false success."""
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    _admit_at_commit_pending_with_receipt(
        deps, job_id, deadline_at=datetime.now(UTC) + timedelta(seconds=8)
    )
    ok = verify_ok_http()
    calls = {"health": 0}
    clock = _AdvancingClock()

    def handler(method, url, payload):
        if url.endswith("/api/v1/health"):
            calls["health"] += 1
            return (0, None)  # the api never becomes ready
        return ok.handler(method, url, payload)

    object.__setattr__(deps, "http", FakeHttp(handler=handler))
    object.__setattr__(deps, "clock", clock)
    sleeps: list[float] = []

    def advance_on_sleep(seconds):
        clock.advance(seconds)  # simulated time passes while the loop sleeps
        sleeps.append(seconds)

    object.__setattr__(deps, "sleep", advance_on_sleep)
    assert executor_module.run_job(deps, job_id) == 1
    current = read_record(deps, job_id)
    assert current.state is JobState.COMMIT_REPAIR_REQUIRED
    # One attempt (timeout clipped to the 8 s budget) + one gap-fitting
    # attempt; then the budget is spent and NO further request begins
    # (remaining <= 0). The sleeps are clipped to what remains: a full 5 s
    # gap, then exactly what was left of the budget.
    assert calls["health"] == 2
    assert sleeps[0] == verification_module.ATTEMPT_GAP_SECONDS
    assert 0 < sleeps[1] < verification_module.ATTEMPT_GAP_SECONDS
    assert sum(sleeps) <= 8.0


def test_finalization_slow_probe_cannot_exceed_the_phase_budget(tmp_path):
    """Finding 3: a probe that consumes its whole timeout must not push the
    readiness wait past the phase deadline - time INSIDE the request counts
    against the budget, and no request may begin when nothing remains."""
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    _admit_at_commit_pending_with_receipt(
        deps, job_id, deadline_at=datetime.now(UTC) + timedelta(seconds=8)
    )
    ok = verify_ok_http()
    calls = {"health": 0}
    clock = _AdvancingClock()

    def handler(method, url, payload):
        if url.endswith("/api/v1/health"):
            calls["health"] += 1
            if calls["health"] == 1:
                # One full-timeout probe: the request consumes the ENTIRE
                # remaining 8 s budget before it returns a failure.
                clock.advance(8.0)
            return (0, None)
        return ok.handler(method, url, payload)

    object.__setattr__(deps, "http", FakeHttp(handler=handler))
    object.__setattr__(deps, "clock", clock)
    sleeps: list[float] = []
    object.__setattr__(deps, "sleep", sleeps.append)
    assert executor_module.run_job(deps, job_id) == 1
    current = read_record(deps, job_id)
    assert current.state is JobState.COMMIT_REPAIR_REQUIRED
    assert clock.now <= 8.0  # total stays within the phase budget
    # The over-budget probe was the LAST request: no further sleep and no
    # further request ever begin. (The old loop slept 5 s and started
    # another request past the 8 s deadline.)
    assert calls["health"] == 1
    assert sleeps == []


def test_finalization_fallback_horizon_is_bounded_without_budget(tmp_path):
    """A late receipt leaves no phase budget: at most MAX_ATTEMPTS complete
    attempts on the bounded fallback horizon, then the documented failure
    state - never an unbounded loop and never a false success."""
    deps = make_engine(tmp_path)
    record = admit_job(deps)
    job_id = record.job_id
    _admit_at_commit_pending_with_receipt(deps, job_id, deadline_at=past(60.0))
    ok = verify_ok_http()
    calls = {"health": 0}

    def handler(method, url, payload):
        if url.endswith("/api/v1/health"):
            calls["health"] += 1
            return (0, None)  # the api never becomes ready
        return ok.handler(method, url, payload)

    object.__setattr__(deps, "http", FakeHttp(handler=handler))
    sleeps: list[float] = []
    object.__setattr__(deps, "sleep", sleeps.append)
    assert executor_module.run_job(deps, job_id) == 1
    current = read_record(deps, job_id)
    assert current.state is JobState.COMMIT_REPAIR_REQUIRED
    assert calls["health"] == verification_module.MAX_ATTEMPTS
    assert sleeps == [verification_module.ATTEMPT_GAP_SECONDS] * (
        verification_module.MAX_ATTEMPTS - 1
    )


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

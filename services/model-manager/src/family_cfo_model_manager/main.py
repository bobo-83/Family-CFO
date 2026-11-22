"""Model-manager sidecar: protocol major 2 (ADR 0081, ADR 0013).

The ONE privileged service in the stack: it mounts the Docker socket and the
project directory. v2 adds durable, journaled apply jobs with decision/ack/
recover semantics; every v2 MUTATOR requires the deployment-scoped internal
credential (env-provided; never stored in job state or journals). Legacy
`POST /swap` returns an upgrade-required error and performs NO mutation; the
read-only `/status`, `/logs`, `/metrics` remain, with `/status` projecting the
new lifecycle into the coarse AiSwapStatus enum so shipped clients keep
decoding it. Internal network only; the API (owner-gated) is the only caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from family_cfo_model_manager import executor as executor_module
from family_cfo_model_manager.contracts import (
    PROTOCOL_MAJOR,
    JobDecision,
    JobOrigin,
    JobState,
    PhotoMode,
    ReasonCode,
    Topology,
    project_legacy_state,
)
from family_cfo_model_manager.journal import Journal, JournalError
from family_cfo_model_manager.manager_state import build_manager_deps, job_status_of

PROJECT_DIR = "/project"
LOG_TAIL_CHARS = 4000
CREDENTIAL_ENV = "FAMILY_CFO_MANAGER_CREDENTIAL"
CREDENTIAL_HEADER = "x-manager-credential"

# org/name with a conservative charset — the only shape ever accepted.
_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


class ManagerError(Exception):
    """A structured error with a stable code (the v2 envelope)."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


app = FastAPI(title="Family CFO Model Manager")


@app.exception_handler(ManagerError)
async def _manager_error_handler(_request: Request, exc: ManagerError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(JournalError)
async def _journal_error_handler(_request: Request, exc: JournalError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": {"code": exc.code.value, "message": exc.detail}},
    )


# --- v2 request/response models -------------------------------------------------


class V2JobRequest(BaseModel):
    request_id: str = Field(min_length=36, max_length=36)
    main_model: str
    vision_model: str | None = None
    photo_mode: PhotoMode = PhotoMode.AUTO
    topology: Topology = Topology.AUTO
    cluster_consent: bool = False
    origin: JobOrigin
    runtime_key: str = Field(min_length=1, max_length=200)
    initiator_household_id: str | None = None
    initiator_user_id: str | None = None


class DecisionBody(BaseModel):
    decision: JobDecision


class AckBody(BaseModel):
    job_id: str
    receipt_job_id: str
    plan_hash: str


class SwapStatus(BaseModel):
    """The legacy coarse projection (shipped clients decode this shape)."""

    state: Literal["idle", "running", "succeeded", "failed", "unavailable"]
    main_model: str | None = None
    vision_model: str | None = None
    log_tail: str = ""


# --- deps -----------------------------------------------------------------------


def _journal() -> Journal:
    return Journal(os.path.join(PROJECT_DIR, ".runtime-swap", "v2"))


def _deps() -> executor_module.ExecutorDeps:
    return build_manager_deps(PROJECT_DIR, journal=_journal())


def _require_credential(request: Request) -> None:
    """Every v2 mutator requires the deployment-scoped internal credential.

    An unconfigured credential fails closed: nothing can mutate."""
    expected = os.environ.get(CREDENTIAL_ENV, "").strip()
    provided = request.headers.get(CREDENTIAL_HEADER, "").strip()
    if not expected or not provided or not secrets.compare_digest(provided, expected):
        raise ManagerError(401, "unauthorized", "a valid internal credential is required")


def _admission_request(payload: V2JobRequest) -> executor_module.AdmissionRequest:
    from family_cfo_model_manager.adapters import validate_repo_id

    _validate_request_id(payload.request_id)
    try:
        validate_repo_id(payload.main_model)
        if payload.vision_model is not None:
            validate_repo_id(payload.vision_model)
    except Exception as exc:
        raise ManagerError(422, "invalid_model_id", str(exc)) from exc
    if payload.origin is JobOrigin.CLI and (
        payload.initiator_household_id or payload.initiator_user_id
    ):
        raise ManagerError(
            422,
            "invalid_model_id",
            "CLI-origin jobs carry no household identity",
        )
    return executor_module.AdmissionRequest(
        request_id=payload.request_id,
        main_model=payload.main_model,
        vision_model=payload.vision_model,
        photo_mode=payload.photo_mode,
        topology=payload.topology,
        cluster_consent=payload.cluster_consent,
        origin=payload.origin,
        runtime_key=payload.runtime_key,
        initiator_household_id=payload.initiator_household_id,
        initiator_user_id=payload.initiator_user_id,
    )


def _validate_request_id(value: str) -> None:
    import uuid

    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise ManagerError(422, "invalid_model_id", "request_id must be a UUID") from exc


# --- legacy surface --------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# M50: read-only container log access. Allowlisted service names only.
_LOG_SERVICES = {"vllm", "vllm-vision"}


@app.get("/logs")
def logs(service: str, tail: int = 30) -> dict:
    if service not in _LOG_SERVICES:
        raise HTTPException(status_code=422, detail="unknown service")
    tail = max(1, min(tail, 200))
    try:
        result = subprocess.run(
            ["docker", "compose", "logs", "--no-color", "--tail", str(tail), service],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=503, detail="log read timed out") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=503, detail="log read failed")
    return {"lines": result.stdout[-LOG_TAIL_CHARS:]}


def _container_stats() -> list[tuple[str, int, int]]:
    """(service, up, restart_count) per compose container."""
    listing = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=com.docker.compose.project=family-cfo",
            "--format",
            "{{.ID}}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if listing.returncode != 0:
        raise RuntimeError("docker ps failed")
    ids = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
    if not ids:
        return []
    inspect = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "com.docker.compose.service"}}'
            "|{{.State.Running}}|{{.RestartCount}}",
            *ids,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if inspect.returncode != 0:
        raise RuntimeError("docker inspect failed")
    stats: list[tuple[str, int, int]] = []
    for line in inspect.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3 or not parts[0]:
            continue
        stats.append((parts[0], 1 if parts[1] == "true" else 0, int(parts[2] or 0)))
    return stats


@app.get("/metrics")
def metrics() -> Response:
    try:
        stats = _container_stats()
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        raise HTTPException(status_code=503, detail="container stats unavailable") from exc
    lines = [
        "# HELP family_cfo_container_up Whether the compose container is running.",
        "# TYPE family_cfo_container_up gauge",
    ]
    for service, up, _restarts in sorted(stats):
        lines.append(f'family_cfo_container_up{{service="{service}"}} {up}')
    lines += [
        "# HELP family_cfo_container_restarts Docker restart count per container.",
        "# TYPE family_cfo_container_restarts counter",
    ]
    for service, _up, restarts in sorted(stats):
        lines.append(f'family_cfo_container_restarts{{service="{service}"}} {restarts}')
    # Operator visibility for the apply transaction (ADR 0081 S7).
    lines += _apply_metrics()
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


def _apply_metrics() -> list[str]:
    lines = [
        "# HELP family_cfo_apply_state The current apply job state (1 for active).",
        "# TYPE family_cfo_apply_state gauge",
        "# HELP family_cfo_apply_blocked Whether a new apply is blocked.",
        "# TYPE family_cfo_apply_blocked gauge",
    ]
    try:
        record = job_status_of(_journal())
    except JournalError:
        lines.append('family_cfo_apply_state{state="corrupt",origin="none"} 1')
        lines.append("family_cfo_apply_blocked 1")
        return lines
    if record is None:
        lines.append('family_cfo_apply_state{state="idle",origin="none"} 1')
        lines.append("family_cfo_apply_blocked 0")
        return lines
    state = record.state.value
    durable = _journal().read_job_record(record.job_id)
    origin = durable.origin.value if durable is not None else "none"
    lines.append(f'family_cfo_apply_state{{state="{state}",origin="{origin}"}} 1')
    blocked = 0 if record.state.terminal and record.state is not JobState.COMMIT_REPAIR_REQUIRED else 1
    lines.append(f"family_cfo_apply_blocked {blocked}")
    return lines


@app.get("/status", response_model=SwapStatus)
def status() -> SwapStatus:
    """Legacy read-only projection (never commits, launches, or acknowledges)."""
    try:
        record = job_status_of(_journal())
    except JournalError as exc:
        # Fail closed, never reset to idle: operator-visible corruption.
        raise HTTPException(status_code=503, detail=exc.code.value) from exc
    if record is None:
        return SwapStatus(state="idle")
    legacy = project_legacy_state(record.state)
    return SwapStatus(
        state=legacy.value,
        main_model=record.requested_selection.main_model
        if record.requested_selection
        else None,
        vision_model=record.requested_selection.vision_model
        if record.requested_selection
        else None,
        log_tail=_diagnostic_tail(record.job_id),
    )


def _diagnostic_tail(job_id: str) -> str:
    path = Path(PROJECT_DIR) / ".runtime-swap" / "v2" / "jobs" / job_id / "diagnostics.log"
    try:
        content = path.read_text()
    except OSError:
        return ""
    return content[-1000:]


@app.post("/swap")
def swap(_payload: dict) -> JSONResponse:
    """Legacy mutation surface: deliberately broken under ADR 0081 S6.

    Returns upgrade-required and performs NO mutation - an old API against a
    v2 manager must fail safely without repointing households."""
    raise ManagerError(
        426,
        "manager_upgrade_required",
        "this manager speaks protocol major 2; use POST /v2/jobs - the legacy "
        "swap endpoint performs no mutation",
    )


# --- v2 surface -------------------------------------------------------------------


@app.get("/v2/capabilities")
def capabilities() -> dict:
    build = _build_identity()
    return {
        "protocol_major": PROTOCOL_MAJOR,
        "build": build,
        "journal_schema": 2,
    }


def _build_identity() -> str:
    build_file = Path(__file__).resolve().parents[1].parent / "BUILD"
    try:
        return build_file.read_text().strip() or "0"
    except OSError:
        return "0"


@app.post("/v2/jobs", status_code=202)
def admit_job(payload: V2JobRequest, request: Request) -> dict:
    _require_credential(request)
    admission = _admission_request(payload)
    deps = _deps()
    lock_fd = _acquire_deployment_lock()
    try:
        record = executor_module.admit(deps, admission, lock_fd=lock_fd)
    except executor_module.ExecutorRefusal as exc:
        raise ManagerError(_status_for(exc.reason_code), exc.reason_code.value, exc.message) from exc
    except JournalError as exc:
        raise ManagerError(503, exc.code.value, exc.detail) from exc
    finally:
        # Admission handed the fd to the executor (spawn) or failed before
        # spawn; closing the manager copy never unlocks an inherited one.
        with contextlib.suppress(OSError):
            os.close(lock_fd)
    return _job_payload(deps, record)


def _acquire_deployment_lock() -> int:
    import fcntl

    fd = os.open(PROJECT_DIR, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ManagerError(
            409, "swap_in_progress", "another apply owns the deployment lock"
        ) from None
    except OSError as exc:
        os.close(fd)
        raise ManagerError(503, "journal_unavailable", "could not lock the deployment") from exc
    return fd


def _status_for(reason: ReasonCode) -> int:
    if reason is ReasonCode.SWAP_IN_PROGRESS:
        return 409
    if reason in (
        ReasonCode.CLUSTER_CONSENT_REQUIRED,
        ReasonCode.CLUSTER_PEER_UNREACHABLE,
        ReasonCode.DEPLOYMENT_DRIFT,
        ReasonCode.PREVIOUS_RUNTIME_NOT_READY,
        ReasonCode.REQUEST_ID_CONFLICT,
        ReasonCode.RECOVERY_REQUIRED,
    ):
        return 409
    if reason is ReasonCode.EXECUTOR_START_FAILED:
        return 503
    return 422


@app.get("/v2/jobs/current")
def current_job() -> dict:
    deps = _deps()
    record = job_status_of(deps.journal)
    if record is None:
        raise ManagerError(404, "unknown_job", "no job has been admitted")
    return _job_payload(deps, record)


@app.get("/v2/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    deps = _deps()
    try:
        record = deps.journal.read_job_record(job_id)
    except JournalError as exc:
        raise ManagerError(503, exc.code.value, exc.detail) from exc
    if record is None:
        raise ManagerError(404, "unknown_job", "no retained job with that id")
    return _job_payload(deps, record)


@app.post("/v2/jobs/{job_id}/decision")
def decide(job_id: str, body: DecisionBody, request: Request) -> dict:
    _require_credential(request)
    deps = _deps()
    with deps.journal.locked():
        record = deps.journal.read_job_record(job_id)
        if record is None:
            raise ManagerError(404, "unknown_job", "no retained job with that id")
        if record.decision is JobDecision.UNDECIDED:
            record = executor_module._transition_holding(
                deps, record, decision=body.decision
            )
        elif record.decision is not body.decision:
            raise ManagerError(
                409,
                "request_id_conflict",
                "the durable decision is monotonic and already "
                + record.decision.value,
            )
        # Same decision replays OK (idempotent).
    return _job_payload(deps, record)


@app.post("/v2/jobs/{job_id}/ack")
def acknowledge(job_id: str, body: AckBody, request: Request) -> dict:
    _require_credential(request)
    deps = _deps()
    with deps.journal.locked():
        record = deps.journal.read_job_record(job_id)
        if record is None:
            raise ManagerError(404, "unknown_job", "no retained job with that id")
        if body.job_id != job_id:
            raise ManagerError(422, "invalid_model_id", "ack job id mismatch")
        if record.decision is not JobDecision.COMMIT:
            raise ManagerError(
                409, "request_id_conflict", "only a committed job can be acknowledged"
            )
        if record.state not in (JobState.COMMIT_PENDING, JobState.COMMIT_REPAIR_REQUIRED):
            raise ManagerError(
                409, "request_id_conflict", "the job is not awaiting acknowledgement"
            )
        if record.config_receipt_id not in (None, body.receipt_job_id):
            raise ManagerError(
                409, "request_id_conflict", "a different receipt already acknowledged this job"
            )
        if record.config_receipt_id is None:
            record = executor_module._transition_holding(
                deps, record, config_receipt_id=body.receipt_job_id
            )
        # Replay-safe: the same receipt idempotently re-returns the job; the
        # executor runs finalization and persists succeeded.
    return _job_payload(deps, record)


@app.post("/v2/jobs/{job_id}/recover")
def recover(job_id: str, request: Request) -> dict:
    _require_credential(request)
    deps = _deps()
    lock_fd = _acquire_deployment_lock()
    try:
        with deps.journal.locked():
            record = deps.journal.read_job_record(job_id)
            if record is None:
                raise ManagerError(404, "unknown_job", "no retained job with that id")
            if record.state is JobState.RECOVERY_FAILED:
                record = executor_module._transition_holding(
                    deps, record, state=JobState.RESTORING
                )
            elif record.state is not JobState.COMMIT_REPAIR_REQUIRED:
                raise ManagerError(
                    409,
                    "request_id_conflict",
                    "recovery applies to recovery_failed or commit_repair_required",
                )
        executor_module._spawn_executor(deps, job_id, lock_fd)
    finally:
        with contextlib.suppress(OSError):
            os.close(lock_fd)
    record = deps.journal.read_job_record(job_id)
    assert record is not None
    return _job_payload(deps, record)


def _job_payload(deps: executor_module.ExecutorDeps, record: Any) -> dict:
    status = job_status_of(deps.journal, job_id=record.job_id)
    assert status is not None
    return _status_document(status)


def _status_document(status: Any) -> dict:
    from family_cfo_model_manager.contracts import JobStatus as JobStatusContract

    assert isinstance(status, JobStatusContract)
    return {
        "job_id": status.job_id,
        "request_id": status.request_id,
        "sequence": status.sequence,
        "state": status.state.value,
        "decision": status.decision.value,
        "origin": status.origin.value,
        "requested_selection": _selection_document(status.requested_selection),
        "resolved_plan": _plan_summary_document(status.resolved_plan),
        "previous_selection": _selection_document(status.previous_selection),
        "created_at": status.created_at.isoformat(),
        "updated_at": status.updated_at.isoformat(),
        "deadline_at": status.deadline_at.isoformat() if status.deadline_at else None,
        "reason_code": status.reason_code.value if status.reason_code else None,
        "message": status.message,
        "recovery_available": status.recovery_available,
        "verified_at": status.verified_at.isoformat() if status.verified_at else None,
    }


def _selection_document(summary: Any) -> dict | None:
    if summary is None:
        return None
    return {
        "main_model": summary.main_model,
        "vision_model": summary.vision_model,
        "photo_mode": summary.photo_mode.value,
        "topology": summary.topology.value,
    }


def _plan_summary_document(summary: Any) -> dict | None:
    if summary is None:
        return None
    return {
        "main_model": summary.main_model,
        "main_revision": summary.main_revision,
        "vision_model": summary.vision_model,
        "vision_revision": summary.vision_revision,
        "photo_mode": summary.photo_mode.value,
        "topology": summary.topology.value,
        "runtime_version": summary.runtime_version,
        "image_digest": summary.image_digest,
        "context_limit": summary.context_limit,
        "concurrency_limit": summary.concurrency_limit,
        "node_requirements": [
            {
                "node": item.node,
                "memory_gb": item.memory_gb,
                "memory_evidence": item.memory_evidence.value,
                "disk_gb": item.disk_gb,
                "disk_evidence": item.disk_evidence.value,
            }
            for item in summary.node_requirements
        ],
        "warnings": list(summary.warnings),
    }


# --- startup + periodic recovery scan ----------------------------------------------


async def _recovery_loop() -> None:
    while True:
        await asyncio.sleep(executor_module.RECOVERY_SCAN_SECONDS)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(_run_recovery_scan)


def _run_recovery_scan() -> None:
    deps = _deps()
    executor_module.scan_and_recover(deps)


@app.on_event("startup")
def _startup() -> None:
    with contextlib.suppress(Exception):
        _run_recovery_scan()
    app.state.recovery_task = asyncio.ensure_future(_recovery_loop())


@app.on_event("shutdown")
def _shutdown() -> None:
    task = getattr(app.state, "recovery_task", None)
    if task is not None:
        task.cancel()

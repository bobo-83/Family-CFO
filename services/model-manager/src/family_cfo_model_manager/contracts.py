"""Protocol contracts for model-manager major 2 (ADR 0081).

Immutable protocol DTOs and closed enums shared by the manager, the API
client, and the CLI. Deliberately dependency-free (stdlib only): the CLI
imports these modules host-side without FastAPI or Pydantic (ADR 0081 S10).

Protocol DTOs describe the WIRE and JOURNAL shapes; immutable execution
values (plans, evidence) live in planning.py. Neither owns mutable
global status - the journal owns execution state.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

# Manager protocol major 2 (ADR 0081 S6): the deliberate, ADR-accepted break
# of bidirectional v1 mutation compatibility. Journal records are schema 2.
PROTOCOL_MAJOR = 2
JOURNAL_SCHEMA_VERSION = 2


class JobState(str, enum.Enum):
    """Durable apply-job lifecycle (spec 03: idle is never stored)."""

    ACCEPTED = "accepted"
    STAGING = "staging"
    LAUNCHING = "launching"
    VERIFYING = "verifying"
    COMMIT_PENDING = "commit_pending"
    RESTORING = "restoring"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RESTORED = "restored"
    RECOVERY_FAILED = "recovery_failed"
    COMMIT_REPAIR_REQUIRED = "commit_repair_required"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL_STATES

    @property
    def recoverable(self) -> bool:
        """Whether retry-recovery can re-run the frozen decision direction."""
        return self in (JobState.RECOVERY_FAILED, JobState.COMMIT_REPAIR_REQUIRED)


_TERMINAL_STATES = frozenset(
    {
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.RESTORED,
        JobState.RECOVERY_FAILED,
        # Terminal in outcome but forward-repairable; never rolls back.
        JobState.COMMIT_REPAIR_REQUIRED,
    }
)

# States that mean the deployment transaction is still in flight (or, for
# COMMIT_REPAIR_REQUIRED, blocked pending forward repair).
_RUNNING_STATES = frozenset(
    {
        JobState.ACCEPTED,
        JobState.STAGING,
        JobState.LAUNCHING,
        JobState.VERIFYING,
        JobState.COMMIT_PENDING,
        JobState.RESTORING,
        JobState.COMMIT_REPAIR_REQUIRED,
    }
)

_FAILED_STATES = frozenset(
    {JobState.FAILED, JobState.RESTORED, JobState.RECOVERY_FAILED}
)


class JobDecision(str, enum.Enum):
    """Durable decision direction - monotonic: commit never becomes restore."""

    UNDECIDED = "undecided"
    COMMIT = "commit"
    RESTORE = "restore"


class JobOrigin(str, enum.Enum):
    API = "api"
    CLI = "cli"


class PhotoMode(str, enum.Enum):
    AUTO = "auto"
    MAIN = "main"
    SEPARATE = "separate"
    OFF = "off"


class Topology(str, enum.Enum):
    AUTO = "auto"
    SINGLE = "single"
    CLUSTER = "cluster"


class Evidence(str, enum.Enum):
    """Classification every resource/capability fact carries (ADR 0081 S2).

    Unknown required evidence is never converted to zero, to "fits", or to a
    single-node assumption - it refuses with insufficient_evidence.
    """

    VERIFIED = "verified"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class LegacySwapState(str, enum.Enum):
    """The coarse enum shipped clients decode (AiSwapStatus)."""

    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class ReasonCode(str, enum.Enum):
    """Stable public/manager error and status reason vocabulary (spec 04)."""

    SWAP_IN_PROGRESS = "swap_in_progress"
    CLUSTER_CONSENT_REQUIRED = "cluster_consent_required"
    CLUSTER_PEER_UNREACHABLE = "cluster_peer_unreachable"
    DEPLOYMENT_DRIFT = "deployment_drift"
    PREVIOUS_RUNTIME_NOT_READY = "previous_runtime_not_ready"
    REQUEST_ID_CONFLICT = "request_id_conflict"
    RECOVERY_REQUIRED = "recovery_required"
    INVALID_MODEL_ID = "invalid_model_id"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_ACCESS_DENIED = "model_access_denied"
    RUNTIME_INCOMPATIBLE = "runtime_incompatible"
    HARDWARE_INCOMPATIBLE = "hardware_incompatible"
    UNSUPPORTED_PHOTO_MODE = "unsupported_photo_mode"
    INSUFFICIENT_MEMORY = "insufficient_memory"
    INSUFFICIENT_DISK = "insufficient_disk"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MANAGER_UNAVAILABLE = "manager_unavailable"
    MANAGER_UPGRADE_REQUIRED = "manager_upgrade_required"
    PREFLIGHT_UNAVAILABLE = "preflight_unavailable"
    JOURNAL_UNAVAILABLE = "journal_unavailable"
    EXECUTOR_START_FAILED = "executor_start_failed"
    REQUEST_EXPIRED = "request_expired"
    RUNTIME_STATE_CORRUPT = "runtime_state_corrupt"
    NO_PREVIOUS_RUNTIME = "no_previous_runtime"


def project_legacy_state(
    state: JobState | None, *, manager_reachable: bool = True
) -> LegacySwapState:
    """The additive legacy projection (ADR 0081 S6, spec 04).

    staging/launching/verifying/commit_pending (and accepted/restoring, plus
    commit_repair_required - the plan keeps it running) project to running;
    restored/recovery_failed to failed; succeeded to succeeded; no job to
    idle; an unreachable manager to unavailable.
    """
    if not manager_reachable:
        return LegacySwapState.UNAVAILABLE
    if state is None:
        return LegacySwapState.IDLE
    if state is JobState.SUCCEEDED:
        return LegacySwapState.SUCCEEDED
    if state in _FAILED_STATES:
        return LegacySwapState.FAILED
    if state in _RUNNING_STATES:
        return LegacySwapState.RUNNING
    raise ValueError(f"unprojectable job state: {state!r}")  # pragma: no cover


@dataclass(frozen=True, slots=True)
class SelectionSummary:
    """Normalized selection, or a safe summary of the previous deployment.

    photo_mode is always resolved (never AUTO) in a frozen value; topology
    carries the requested value (AUTO allowed) for a request and the resolved
    value (never AUTO) for a previous deployment.
    """

    main_model: str
    vision_model: str | None
    photo_mode: PhotoMode
    topology: Topology


@dataclass(frozen=True, slots=True)
class NodeRequirement:
    """One node's admission requirement, each figure evidence-classified."""

    node: str
    memory_gb: float
    memory_evidence: Evidence
    disk_gb: float
    disk_evidence: Evidence


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """Safe plan summary: identities, limits, per-node requirements.

    Never .env values, tokens, SSH details, raw checker tracebacks, or
    arbitrary container logs.
    """

    main_model: str
    main_revision: str
    vision_model: str | None
    vision_revision: str | None
    photo_mode: PhotoMode
    topology: Topology
    runtime_version: str
    image_digest: str
    context_limit: int
    concurrency_limit: int
    node_requirements: tuple[NodeRequirement, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    """One-shot v2 admission body (POST /v2/jobs).

    The API supplies authorized intent plus the household's cluster consent -
    consent never comes from the public request (ADR 0071/0081). CLI-origin
    admissions carry no initiator identity.
    """

    request_id: str
    main_model: str
    vision_model: str | None
    photo_mode: PhotoMode
    topology: Topology
    cluster_consent: bool
    origin: JobOrigin
    runtime_key: str
    initiator_household_id: str | None = None
    initiator_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class JobStatus:
    """The v2 job projection returned by the manager read routes."""

    job_id: str
    request_id: str
    sequence: int
    state: JobState
    decision: JobDecision
    origin: JobOrigin
    requested_selection: SelectionSummary | None
    resolved_plan: PlanSummary | None
    previous_selection: SelectionSummary | None
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime | None
    reason_code: ReasonCode | None
    message: str | None
    recovery_available: bool
    # Additive field (ADR 0074 Decision 5): when the target last passed
    # verification. The reconciler commits only on evidence <= 30 s old.
    verified_at: datetime | None = None
    # Additive (issue #170 item 4): the frozen identity the reconciler's
    # receipt and acknowledgement freeze, plus the initiator attribution for
    # the outcome audits. Never credentials; safe to expose on the wire.
    runtime_key: str | None = None
    target_plan_hash: str | None = None
    initiator_household_id: str | None = None
    initiator_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class JobRecord:
    """The durable journal record (jobs/<job_id>/record.json).

    Operational data only - never financial data or credentials. SQL holds no
    copy of this state machine.
    """

    schema_version: int
    job_id: str
    request_id: str
    sequence: int
    origin: JobOrigin
    runtime_key: str
    request_hash: str
    state: JobState
    decision: JobDecision
    created_at: datetime
    updated_at: datetime
    initiator_household_id: str | None = None
    initiator_user_id: str | None = None
    phase_deadline_at: datetime | None = None
    elapsed_seconds: float | None = None
    failure_code: ReasonCode | None = None
    safe_message: str | None = None
    previous_snapshot_hash: str | None = None
    target_plan_hash: str | None = None
    config_receipt_id: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    """Durable commit/restore decision from the API reconciler."""

    decision: JobDecision


@dataclass(frozen=True, slots=True)
class AckRequest:
    """Acknowledgement of the SQL receipt for a committed job."""

    job_id: str
    receipt_job_id: str
    plan_hash: str


@dataclass(frozen=True, slots=True)
class Capabilities:
    """GET /v2/capabilities response."""

    protocol_major: int
    build: str
    journal_schema: int

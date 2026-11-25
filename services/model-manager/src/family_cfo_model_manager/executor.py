"""Durable execution state machine (ADR 0081 S3/S5, issue #170 Item 3).

The executor is a SEPARATELY SUPERVISED PROCESS, not a daemon thread: the
manager performs bounded preflight, durably accepts the job, then spawns
`python -m family_cfo_model_manager.executor --job-id <uuid>` with the
directory-flock descriptor inherited. The executor owns the job until
acknowledgement or verified failure; manager shutdown may leave it alive
(explicitly supported). Ownership is NEVER inferred from a stored PID and a
PID recovered from a stale record is never signalled - only a child handle
created by the current supervisor is terminated/reaped, with #167's full
SIGTERM -> grace -> SIGKILL discipline.

States: accepted -> staging -> launching -> verifying -> commit_pending ->
succeeded; restoring -> restored / recovery_failed; failed; and
commit_repair_required forward repair. Phase deadlines (staging 60 min,
launch + readiness 30 min, restore + verify 30 min) are PERSISTED in the
journal - a restart never grants a fresh budget. All journal mutations take
the short journal-file lock and release it BEFORE waiting on deployment
ownership (no lock-order inversion).

`admit` is the one-shot admission pipeline shared by the manager HTTP facade
and the host-side CLI; `scan_and_recover` implements the restart/periodic
recovery scan. Importable without FastAPI/Pydantic.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from family_cfo_model_manager import deployment
from family_cfo_model_manager import runtime_check
from family_cfo_model_manager import verification as verification_module
from family_cfo_model_manager.adapters import (
    HubMetadataSource,
    LocalHardwareSource,
    MetadataUnavailable,
)
from family_cfo_model_manager.contracts import (
    AdmissionRequest,
    JobDecision,
    JobRecord,
    JobState,
    PhotoMode,
    ReasonCode,
    Topology,
)
from family_cfo_model_manager.deployment import (
    ComposeAdapter,
    DeploymentError,
    DeploymentRefusal,
    EnvFile,
    PeerInspector,
    RUNTIME_ENV_KEYS,
    assert_no_drift,
    capture_snapshot,
    empty_baseline_restore,
    launch_target,
    launch_values,
    restore_runtime,
    runtime_values_from_running,
)
from family_cfo_model_manager.journal import Journal, JournalError, RequestTombstone
from family_cfo_model_manager.planning import (
    LaunchPlan,
    PlanningRefusal,
    PlanRequest,
    ProfilePolicy,
    ProfileSource,
    resolve_launch_plan,
)
from family_cfo_model_manager.runtime_check import (
    CHECKER_OUTPUT_BYTES,
    CheckerContext,
    ContainerSpec,
    RuntimeCheckRefusal,
)
from family_cfo_model_manager.verification import ExpectedRuntime, Verifier

# --- phase deadlines (persisted; restart never grants fresh budget) -------------
STAGING_DEADLINE_SECONDS = 60 * 60.0
LAUNCH_DEADLINE_SECONDS = 30 * 60.0
RESTORE_DEADLINE_SECONDS = 30 * 60.0
# #167's grace discipline, reused for executor supervision.
EXECUTOR_TERMINATE_GRACE_SECONDS = 5.0
COMMIT_ACK_POLL_SECONDS = 2.0
# Env-tunable so integration tests (and operators) can shorten the
# commit-pending re-verification cadence; default matches the plan (15 s).
COMMIT_REVERIFY_SECONDS = float(
    os.environ.get("FAMILY_CFO_COMMIT_REVERIFY_SECONDS") or 15.0
)
RECOVERY_SCAN_SECONDS = 30.0


class ExecutorRefusal(Exception):
    """An admission-time refusal with a stable reason (preflight stage)."""

    def __init__(self, reason_code: ReasonCode, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


# --- process seams -------------------------------------------------------------------


class UrllibRuntimeHttp:
    """The real RuntimeHttp against the managed runtime (internal network).

    Contract: a KNOWN HTTP status returns (status, body-or-None). An empty or
    unparseable body on a 2xx is body=None, NOT a transport failure - vLLM's
    /health answers 200 with an empty body, and collapsing that into (0, None)
    made healthy deployments fail baseline verification. Only genuine
    transport errors (URLError, timeout, OS errors, HTTP errors, malformed
    HTTP responses) return (0, None). Consumers decide per endpoint whether
    the body is required."""

    @staticmethod
    def _parse_body(response: Any) -> Any:
        body = response.read(CHECKER_OUTPUT_BYTES)
        try:
            return json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None  # the status is still known; the body is simply unusable

    def get_json(self, url: str, timeout: float) -> tuple[int, Any]:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.status, self._parse_body(response)
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            return 0, None

    def post_json(self, url: str, payload: dict[str, Any], timeout: float) -> tuple[int, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, self._parse_body(response)
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            return 0, None


class DockerContainerRunner:
    """The real ContainerRunner: `docker run` with the bounded checker spec.

    Read-only rootfs, bounded tmpfs, 2 CPUs / 4 GiB caps, dropped
    capabilities, no privileged mode, no Docker socket, exact immutable image
    ID, --pull=never, job labels; fixed entry point. Output is bounded."""

    def run(self, spec: ContainerSpec, timeout: float) -> runtime_check.ContainerResult:
        command = ["docker", "run", "--rm", "--pull=never"]
        if spec.read_only_rootfs:
            command.append("--read-only")
        command += ["--tmpfs", f"/tmp:size={spec.tmpfs_mb}m,mode=1777"]
        command += ["--cpus", str(spec.cpus), "--memory", f"{spec.memory_gb}g"]
        command += ["--security-opt", "no-new-privileges", "--cap-drop", "ALL"]
        if spec.network == "none":
            command.append("--network=none")
        for key, value in spec.env:
            command += ["-e", f"{key}={value}"]
        for source, target, mode in spec.volumes:
            command += ["-v", f"{source}:{target}:{mode}"]
        for key, value in spec.labels:
            command += ["--label", f"{key}={value}"]
        command += [*spec.entrypoint, *spec.command, spec.image_id]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeCheckRefusal(
                ReasonCode.PREFLIGHT_UNAVAILABLE,
                "the compatibility checker timed out",
            ) from exc
        except OSError as exc:
            raise RuntimeCheckRefusal(
                ReasonCode.PREFLIGHT_UNAVAILABLE,
                f"the compatibility checker could not run: {type(exc).__name__}",
            ) from exc
        return runtime_check.ContainerResult(
            exit_code=result.returncode,
            stdout=result.stdout[-CHECKER_OUTPUT_BYTES:],
            stderr=result.stderr[-CHECKER_OUTPUT_BYTES:],
        )


def probe_runtime_image(
    runner: runtime_check.ContainerRunner, image_id: str, timeout: float
) -> str:
    """The installed release's version, read from the EXACT image by a fixed
    one-shot probe (no mutable tag, no pull, no upgrade)."""
    script = (
        "import importlib.metadata, json\n"
        "try:\n"
        "    version = importlib.metadata.version('vllm')\n"
        "except Exception:\n"
        "    version = None\n"
        "print(json.dumps({'version': version}))\n"
    )
    spec = ContainerSpec(
        image_id=image_id,
        entrypoint=("python", "-c"),
        command=(script,),
        env=(),
        volumes=(),
        labels=(("family.cfo.probe", "runtime-version"),),
    )
    try:
        result = runner.run(spec, timeout)
    except RuntimeCheckRefusal:
        raise
    except Exception as exc:  # noqa: BLE001 - infrastructure failure
        raise RuntimeCheckRefusal(
            ReasonCode.PREFLIGHT_UNAVAILABLE,
            f"the runtime version probe failed: {type(exc).__name__}",
        ) from exc
    if result.exit_code != 0:
        raise RuntimeCheckRefusal(
            ReasonCode.PREFLIGHT_UNAVAILABLE,
            "the runtime version probe failed; the installed image could not "
            "be identified",
        )
    try:
        document = json.loads(result.stdout)
        version = document.get("version")
    except (json.JSONDecodeError, AttributeError):
        version = None
    if not version or not isinstance(version, str):
        raise RuntimeCheckRefusal(
            ReasonCode.RUNTIME_INCOMPATIBLE,
            "the installed image does not report a vLLM version; no checker "
            "adapter can be selected",
        )
    return version


@dataclass(frozen=True, slots=True)
class ExecutorDeps:
    """Everything the executor and the admission pipeline need."""

    journal: Journal
    compose: ComposeAdapter
    http: verification_module.RuntimeHttp
    runner: runtime_check.ContainerRunner
    profiles: ProfileSource
    metadata: HubMetadataSource
    hardware: LocalHardwareSource
    peer: PeerInspector | None
    project_dir: str
    cache_probe: Callable[[str, str | None], int]
    clock: Callable[[], float] = field(default=time.monotonic)
    sleep: Callable[[float], None] = field(default=time.sleep)
    spawn: Callable[..., subprocess.Popen] | None = None  # test seam


# --- journal transition helper ----------------------------------------------------------


def _transition(deps: ExecutorDeps, record: JobRecord, **updates: Any) -> JobRecord:
    """One durable transition: short journal lock, never held while waiting."""
    now = datetime.now(UTC)
    updated = replace(record, **updates, updated_at=now)
    with deps.journal.locked():
        deps.journal.write_job_record(updated)
    return updated


def _transition_holding(
    deps: ExecutorDeps, record: JobRecord, **updates: Any
) -> JobRecord:
    """A durable transition performed while the caller ALREADY holds the
    journal lock (routes read + validate under one lock, then write in
    place).  Must only do work that cannot wait: no compose calls, no
    deployment ownership, no subprocess (ADR 0081 S5)."""
    now = datetime.now(UTC)
    updated = replace(record, **updates, updated_at=now)
    deps.journal.write_job_record(updated)
    return updated


def _persist_evidence(deps: ExecutorDeps, job_id: str, evidence: dict[str, Any]) -> None:
    with deps.journal.locked():
        deps.journal.write_verification_evidence(job_id, evidence)


def _deadline_at(seconds: float) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


def _remaining(record: JobRecord) -> float:
    """Remaining phase budget from the PERSISTED deadline (never fresh).

    A restart never grants a new budget: the persisted wall-clock deadline is
    the only source of truth."""
    if record.phase_deadline_at is None:
        return 0.0
    remaining = record.phase_deadline_at.timestamp() - datetime.now(UTC).timestamp()
    return max(0.0, remaining)


# --- admission helpers ------------------------------------------------------------------


def request_hash_of(request: AdmissionRequest) -> str:
    """The normalized-intent digest: the replay identity."""
    import hashlib

    payload = {
        "main_model": request.main_model,
        "vision_model": request.vision_model,
        "photo_mode": request.photo_mode.value,
        "topology": request.topology.value,
        "cluster_consent": request.cluster_consent,
        "origin": request.origin.value,
        "runtime_key": request.runtime_key,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def check_replay(deps: ExecutorDeps, request: AdmissionRequest) -> JobRecord | None:
    """Replay resolution BEFORE ownership: identical request -> its job;
    different content -> request_id_conflict; pruned detail -> request_expired."""
    try:
        record = deps.journal.request_state(request.request_id)
    except JournalError as exc:
        raise ExecutorRefusal(exc.code, exc.detail) from exc
    if record is None:
        return None
    if record.request_hash != request_hash_of(request):
        raise ExecutorRefusal(
            ReasonCode.REQUEST_ID_CONFLICT,
            "this request id was already used with different content",
        )
    return record


def assert_admissible(deps: ExecutorDeps) -> None:
    """Durable unfinished jobs (and unresolved recoveries) block admission."""
    for record in deps.journal.list_job_records():
        if record.state is JobState.RECOVERY_FAILED:
            raise ExecutorRefusal(
                ReasonCode.RECOVERY_REQUIRED,
                "an earlier apply needs explicit recovery before a new one",
            )
        if record.state is JobState.COMMIT_REPAIR_REQUIRED:
            raise ExecutorRefusal(
                ReasonCode.RECOVERY_REQUIRED,
                "a committed apply needs forward repair before a new one",
            )
        if not record.state.terminal:
            raise ExecutorRefusal(
                ReasonCode.SWAP_IN_PROGRESS,
                f"job {record.job_id} is {record.state.value}",
            )


def inspect_for_admission(
    deps: ExecutorDeps,
    request: AdmissionRequest,
    env_file: EnvFile,
) -> tuple[tuple[Any, ...], dict[str, str | None]]:
    """Effective deployment inspection + drift check (admission step 2).

    The runtime-owned .env keys are compared against the RUNNING deployment;
    unexplained drift refuses with deployment_drift."""
    try:
        services = deployment.routing_services(
            deps.compose, deps.project_dir, deployment._files_from_env()
        )
    except DeploymentError as exc:
        raise ExecutorRefusal(ReasonCode.PREFLIGHT_UNAVAILABLE, str(exc)) from exc
    running_values = runtime_values_from_running(services)
    try:
        assert_no_drift(env_file, running_values)
    except DeploymentRefusal as exc:
        raise ExecutorRefusal(exc.reason_code, exc.message) from exc
    return services, running_values


def _kv_budget(main_facts: Any, profile: ProfilePolicy | None) -> int | None:
    """Total KV bytes for the plan's effective limits."""
    if main_facts.kv_bytes_per_token is None:
        return None
    context, concurrency = _limits_for(main_facts, profile)
    return int(main_facts.kv_bytes_per_token * context * concurrency)


def _limits_for(main_facts: Any, profile: ProfilePolicy | None) -> tuple[int, int]:
    from family_cfo_model_manager.planning import effective_limits

    return effective_limits(profile, main_facts.model_context_limit)


def _missing_artifact_bytes(deps: ExecutorDeps, main_facts: Any, vision_facts: Any) -> int | None:
    """Byte size of candidate artifacts NOT already in the local cache.

    Complete cached files are usable offline; the probe is bounded and any
    failure keeps the evidence unknown (never zero)."""
    try:
        probe = deps.cache_probe(main_facts.repo_id, main_facts.revision)
        missing = main_facts.resident_weight_bytes - probe
    except Exception:  # noqa: BLE001
        return None
    if vision_facts is not None and vision_facts.resident_weight_bytes is not None:
        try:
            vision_probe = deps.cache_probe(vision_facts.repo_id, vision_facts.revision)
            missing += max(0, vision_facts.resident_weight_bytes - vision_probe)
        except Exception:  # noqa: BLE001
            return None
    return max(0, missing)


# --- cluster peer facts ------------------------------------------------------------------


def _peer_nodes(deps: ExecutorDeps) -> tuple[Any, ...]:
    """The declared peer's hardware facts from the read-only SSH allowlist.

    Reachability alone is insufficient: Ray membership, GPU ranks, and the
    peer's image identity are probed; any failure refuses the topology."""
    from family_cfo_model_manager.deployment import parse_gpu_memory, parse_ray_status
    from family_cfo_model_manager.contracts import Evidence
    from family_cfo_model_manager.planning import NodeHardware

    if deps.peer is None:
        raise ExecutorRefusal(
            ReasonCode.CLUSTER_PEER_UNREACHABLE,
            "no peer inspection channel is configured for cluster serving",
        )
    env_file = EnvFile.read(os.path.join(deps.project_dir, ".env"))
    peer_host = env_file.value("CLUSTER_PEER_HOST")
    if not peer_host:
        raise ExecutorRefusal(
            ReasonCode.CLUSTER_PEER_UNREACHABLE,
            "the deployment declares no cluster peer (CLUSTER_PEER_HOST)",
        )
    try:
        ray = deps.peer.probe(peer_host, "ray_status")
        nodes = parse_ray_status(ray)
        gpus = parse_gpu_memory(deps.peer.probe(peer_host, "gpu_memory"))
        # The peer's installed image must be inspectable too (a failed probe
        # refuses above); its identity is captured with the snapshot itself.
        deps.peer.probe(peer_host, "image_id")
        disk = deps.peer.probe(peer_host, "cache_disk")
    except DeploymentRefusal as exc:
        raise ExecutorRefusal(exc.reason_code, exc.message) from exc
    if nodes["alive"] < 2:
        raise ExecutorRefusal(
            ReasonCode.CLUSTER_PEER_UNREACHABLE,
            f"the Ray cluster reports {nodes['alive']} alive node(s); TP2 needs two",
        )
    total_mib = max((total for _used, total in gpus), default=0)
    cache_gb, free_gb = _parse_peer_disk(disk)
    return (
        NodeHardware(
            node="peer",
            memory_gb=total_mib / 1000.0,  # unified-memory pool on DGX-class hosts
            cache_filesystem_gb=cache_gb,
            disk_free_gb=free_gb,
            resident_managed_gb=None,
            evidence=Evidence.VERIFIED if total_mib > 0 else Evidence.UNKNOWN,
        ),
    )


def _parse_peer_disk(output: str) -> tuple[float | None, float | None]:
    for line in output.splitlines():
        parts = line.split()
        # POSIX df: Filesystem 1024-blocks Used Available Capacity ...
        if (
            len(parts) >= 4
            and parts[0] != "Filesystem"
            and parts[1].isdigit()
            and parts[3].isdigit()
        ):
            return int(parts[1]) / 1_000_000, int(parts[3]) / 1_000_000
    return None, None  # unknown, never zero


# --- the installed-image checker -----------------------------------------------------------


def _serve_options(
    plan_request: PlanRequest, profile: ProfilePolicy | None, main_facts: Any
) -> dict[str, Any]:
    from family_cfo_model_manager.planning import parser_policy

    tool_parser, reasoning_parser = parser_policy(profile, main_facts.architecture)
    context, _concurrency = _limits_for(main_facts, profile)
    return {
        "max_model_len": context,
        "tool_call_parser": tool_parser,
        "reasoning_parser": reasoning_parser,
        "dtype": "auto",
        "quantization": main_facts.quantization,
    }


def _run_checker(
    deps: ExecutorDeps,
    *,
    context: CheckerContext,
    preflight_deadline: float,
) -> None:
    """One checker container; raises ExecutorRefusal on any fail-closed path."""
    remaining = preflight_deadline - deps.clock()
    try:
        runtime_check.check_model_compatibility(
            context,
            deps.runner,
            remaining,
            labels={"family.cfo.apply": "checker"},
        )
    except RuntimeCheckRefusal as exc:
        raise ExecutorRefusal(exc.reason_code, exc.message) from exc


# --- deployment baseline -------------------------------------------------------------------


def _installed_image_id(deps: ExecutorDeps) -> tuple[str, str | None]:
    """The exact installed immutable image ID + declared reference.

    Never a mutable tag and never an implicit pull: an absent local image
    refuses."""
    try:
        config = deps.compose.config(deps.project_dir, deployment._files_from_env())
    except DeploymentError as exc:
        raise ExecutorRefusal(ReasonCode.PREFLIGHT_UNAVAILABLE, str(exc)) from exc
    vllm_service = (config.get("services") or {}).get("vllm") or {}
    reference = vllm_service.get("image")
    if not reference:
        raise ExecutorRefusal(
            ReasonCode.PREFLIGHT_UNAVAILABLE,
            "the compose configuration declares no runtime image",
        )
    try:
        image_id = deps.compose.image_id(reference)
    except DeploymentError as exc:
        raise ExecutorRefusal(ReasonCode.PREFLIGHT_UNAVAILABLE, str(exc)) from exc
    if not image_id:
        raise ExecutorRefusal(
            ReasonCode.RUNTIME_INCOMPATIBLE,
            f"the runtime image {reference} is not installed locally; install "
            "it first - a swap never pulls images implicitly",
        )
    return image_id, reference


def capture_and_verify_baseline(
    deps: ExecutorDeps,
    *,
    services: tuple[Any, ...],
    env_file: EnvFile,
) -> deployment.DeploymentSnapshot | None:
    """Verify the currently active managed runtime and capture it as the
    rollback baseline. An unverifiable runtime refuses; a genuinely empty
    first deployment returns None."""
    running_values = runtime_values_from_running(services)
    vllm = next((s for s in services if s.name == "vllm"), None)
    vision = next((s for s in services if s.name == "vllm-vision"), None)
    managed_exists = bool(running_values.get("VLLM_MODEL")) or (
        vllm is not None and vllm.image_id is not None
    )
    if not managed_exists:
        return None  # empty baseline
    if vllm is None or not vllm.running:
        raise ExecutorRefusal(
            ReasonCode.PREVIOUS_RUNTIME_NOT_READY,
            "a managed runtime is configured but its container is not running; "
            "it cannot be adopted as a recoverable baseline",
        )
    photo_mode = _photo_mode_of(running_values, vision)
    topology = (
        Topology.CLUSTER
        if (vllm.command and "--tensor-parallel-size=2" in vllm.command)
        else Topology.SINGLE
    )
    snapshot = capture_snapshot(
        deps.compose,
        deps.project_dir,
        env_file,
        topology=topology,
        photo_mode=photo_mode,
    )
    verifier = Verifier(
        http=deps.http,
        compose_services=lambda: deployment.routing_services(
            deps.compose, deps.project_dir, snapshot.compose_files
        ),
        peer=deps.peer if topology is Topology.CLUSTER else None,
        sleep=deps.sleep,
        clock=deps.clock,
    )
    expected = _expected_from_snapshot(snapshot, running_values)
    deadline = deps.clock() + verification_module.PROBE_TIMEOUT_SECONDS * (
        verification_module.MAX_ATTEMPTS + 1
    )
    outcome = verifier.verify(expected, deadline=deadline)
    if not outcome.passed:
        raise ExecutorRefusal(
            ReasonCode.PREVIOUS_RUNTIME_NOT_READY,
            "the currently active runtime could not be verified as a working "
            f"baseline: {outcome.message}",
        )
    return snapshot


def _photo_mode_of(
    running_values: dict[str, str | None], vision: Any
) -> PhotoMode:
    vision_enabled = (running_values.get("FAMILY_CFO_AI_VISION_ENABLED") or "").lower()
    if vision is not None and vision.running and vision_enabled == "true":
        return PhotoMode.SEPARATE
    supports = (running_values.get("FAMILY_CFO_AI_SUPPORTS_VISION") or "").lower()
    if supports == "true":
        return PhotoMode.MAIN
    return PhotoMode.OFF


def _expected_from_snapshot(
    snapshot: deployment.DeploymentSnapshot, running_values: dict[str, str | None]
) -> ExpectedRuntime:
    vllm = snapshot.service("vllm")
    vision = snapshot.service("vllm-vision")
    extra = running_values.get("VLLM_EXTRA_ARGS")
    max_len = running_values.get("VLLM_MAX_MODEL_LEN")
    seqs = running_values.get("VLLM_MAX_NUM_SEQS")
    return ExpectedRuntime(
        main_model=snapshot.main_model or "",
        vision_model=snapshot.vision_model,
        photo_mode=snapshot.photo_mode,
        topology=snapshot.topology,
        main_revision=running_values.get("VLLM_REVISION"),
        vision_revision=running_values.get("VLLM_VISION_REVISION"),
        image_id=vllm.image_id if vllm else None,
        vision_image_id=vision.image_id if vision else None,
        tool_call_parser=running_values.get("VLLM_TOOL_PARSER"),
        reasoning_parser=running_values.get("VLLM_REASONING_PARSER"),
        max_model_len=int(max_len) if max_len and max_len.isdigit() else None,
        max_num_seqs=int(seqs) if seqs and seqs.isdigit() else None,
        extra_args=extra,
        peer_host=snapshot.declared_peer,
    )


# --- one-shot admission pipeline -------------------------------------------------------------


def admit(
    deps: ExecutorDeps,
    request: AdmissionRequest,
    *,
    lock_fd: int,
    preflight_budget_seconds: float = 180.0,
) -> JobRecord:
    """Validate -> resolve -> capture -> persist -> start the executor.

    The directory flock (lock_fd) is ALREADY held non-blockingly by the
    caller; admission order per the plan: durable-state checks, inspection,
    resolution/validation, baseline capture, durable acceptance, executor
    start. Preflight refusals create no job and no mutation."""
    started = deps.clock()
    preflight_deadline = started + preflight_budget_seconds

    replay = check_replay(deps, request)
    if replay is not None:
        return replay  # a read; no replacement ownership
    assert_admissible(deps)

    env_file = EnvFile.read(os.path.join(deps.project_dir, ".env"))
    services, running_values = inspect_for_admission(deps, request, env_file)
    image_id, image_reference = _installed_image_id(deps)

    runtime_version = _installed_version(deps, image_id, preflight_deadline)
    profile = deps.profiles.policy_for(request.main_model, None)
    try:
        main_facts = deps.metadata.facts_for(request.main_model)
        vision_facts = (
            deps.metadata.facts_for(request.vision_model)
            if request.vision_model
            else None
        )
    except MetadataUnavailable as exc:
        # A metadata failure is a structured preflight refusal (never a 500):
        # the Hub/cache source classifies it (not found, denied, unreachable,
        # or insufficient offline evidence).
        raise ExecutorRefusal(exc.reason_code, str(exc)) from exc
    if profile is None:
        profile = deps.profiles.policy_for(request.main_model, main_facts.architecture)
    if profile is None and vision_facts is not None:
        profile = deps.profiles.policy_for(request.vision_model, vision_facts.architecture)

    plan, plan_request = _resolve_or_refuse(
        deps,
        request,
        main_facts=main_facts,
        vision_facts=vision_facts,
        image_id=image_id,
        image_reference=image_reference,
        runtime_version=runtime_version,
        preflight_deadline=preflight_deadline,
        profile=profile,
    )

    baseline = capture_and_verify_baseline(deps, services=services, env_file=env_file)
    snapshot_hash = (
        _snapshot_digest(baseline) if baseline is not None else None
    )

    record = _accept(
        deps,
        request=request,
        plan=plan,
        baseline=baseline,
        snapshot_hash=snapshot_hash,
        lock_fd=lock_fd,
    )
    deps.journal.append_diagnostics(
        record.job_id,
        json.dumps(
            {
                "event": "accepted",
                "origin": request.origin.value,
                "topology": plan.topology.value,
                "photo_mode": plan.photo_mode.value,
            }
        ),
    )
    try:
        _spawn_executor(deps, record.job_id, lock_fd)
    except ExecutorRefusal:
        # Durably mark failed and release ownership; polling still finds the
        # same terminal job if acceptance was already delivered (ADR 0081 S3).
        _fail_before_mutation(
            deps,
            record,
            ReasonCode.EXECUTOR_START_FAILED,
            "the executor process could not be started",
        )
        raise
    return record


def _installed_version(
    deps: ExecutorDeps, image_id: str, preflight_deadline: float
) -> str:
    remaining = preflight_deadline - deps.clock()
    try:
        return probe_runtime_image(deps.runner, image_id, min(60.0, max(1.0, remaining)))
    except RuntimeCheckRefusal as exc:
        raise ExecutorRefusal(exc.reason_code, exc.message) from exc


def _resolve_or_refuse(
    deps: ExecutorDeps,
    request: AdmissionRequest,
    *,
    main_facts: Any,
    vision_facts: Any,
    image_id: str,
    image_reference: str,
    runtime_version: str,
    preflight_deadline: float,
    profile: ProfilePolicy | None,
) -> tuple[LaunchPlan, PlanRequest]:
    plan_request = PlanRequest(
        request_id=request.request_id,
        main_model=request.main_model,
        vision_model=request.vision_model,
        photo_mode=request.photo_mode,
        topology=request.topology,
        cluster_consent=request.cluster_consent,
    )
    kv_bytes = _kv_budget(main_facts, profile)
    missing = _missing_artifact_bytes(deps, main_facts, vision_facts)
    nodes = deps.hardware.nodes()
    if request.topology is Topology.CLUSTER or (
        request.topology is Topology.AUTO and request.cluster_consent
    ):
        nodes = nodes + _peer_nodes(deps)

    # Every distinct image/model combination passes the installed-image gate:
    # here exactly one runtime image serves both roles.
    _run_checker(
        deps,
        context=CheckerContext(
            image_id=image_id,
            image_repository=deployment.image_repository_of(image_reference),
            runtime_version=runtime_version,
            repo_id=request.main_model,
            revision=main_facts.revision or "",
            serve_options=_serve_options(plan_request, profile, main_facts),
            profile=profile,
            hf_offline=os.environ.get("HF_HUB_OFFLINE", "").strip() == "1",
        ),
        preflight_deadline=preflight_deadline,
    )

    try:
        plan = resolve_launch_plan(
            plan_request,
            main_facts=main_facts,
            vision_facts=vision_facts,
            hardware=nodes,
            profile=profile,
            runtime_version=runtime_version,
            image_digest=image_id,
            kv_bytes=kv_bytes,
            missing_artifact_bytes=missing,
        )
    except PlanningRefusal as exc:
        raise ExecutorRefusal(exc.reason_code, exc.message) from exc
    except MetadataUnavailable as exc:
        raise ExecutorRefusal(ReasonCode.INSUFFICIENT_EVIDENCE, str(exc)) from exc
    return plan, plan_request


def _snapshot_digest(snapshot: deployment.DeploymentSnapshot) -> str:
    import hashlib

    canonical = json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _accept(
    deps: ExecutorDeps,
    *,
    request: AdmissionRequest,
    plan: LaunchPlan,
    baseline: deployment.DeploymentSnapshot | None,
    snapshot_hash: str | None,
    lock_fd: int,
) -> JobRecord:
    """Persist the accepted job + mapping + snapshot + pointer atomically, then
    start the executor. A failed required journal write prevents the next
    deployment mutation (it raises out of admission)."""
    import uuid

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    record = JobRecord(
        schema_version=2,
        job_id=job_id,
        request_id=request.request_id,
        sequence=0,
        origin=request.origin,
        runtime_key=request.runtime_key,
        request_hash=request_hash_of(request),
        state=JobState.ACCEPTED,
        decision=JobDecision.UNDECIDED,
        created_at=now,
        updated_at=now,
        initiator_household_id=request.initiator_household_id,
        initiator_user_id=request.initiator_user_id,
        phase_deadline_at=None,
        failure_code=None,
        safe_message=None,
        previous_snapshot_hash=snapshot_hash,
        target_plan_hash=plan.digest(),
    )
    with deps.journal.locked():
        sequence = deps.journal.allocate_sequence()
        record = replace(record, sequence=sequence)
        deps.journal.write_job_record(record)
        if baseline is not None:
            deps.journal.write_previous_snapshot(job_id, baseline.to_dict())
        deps.journal.write_target_plan(job_id, _target_document(deps, plan, request))
        deps.journal.record_request(
            RequestTombstone(
                request_id=request.request_id,
                request_hash=record.request_hash,
                job_id=job_id,
                created_at=now,
            )
        )
        deps.journal.set_current_job(job_id)
    return record


def _target_document(
    deps: ExecutorDeps, plan: LaunchPlan, request: AdmissionRequest
) -> dict[str, Any]:
    """The fully materialized frozen target: plan + launch values + profile."""
    values = launch_values(plan, node_memory_gb=None, photo_node_memory_gb=None, profile=None)
    del values  # launch values are materialized by the executor with hardware
    return {
        "schema_version": 2,
        "plan": _plan_document(plan),
        "request_selection": {
            "main_model": request.main_model,
            "vision_model": request.vision_model,
            "photo_mode": request.photo_mode.value,
            "topology": request.topology.value,
        },
        "request": {
            "request_id": request.request_id,
            "origin": request.origin.value,
            "runtime_key": request.runtime_key,
        },
    }


def _plan_document(plan: LaunchPlan) -> dict[str, Any]:
    return {
        "main_model": plan.main_model,
        "main_revision": plan.main_revision,
        "vision_model": plan.vision_model,
        "vision_revision": plan.vision_revision,
        "photo_mode": plan.photo_mode.value,
        "topology": plan.topology.value,
        "runtime_version": plan.runtime_version,
        "image_digest": plan.image_digest,
        "context_limit": plan.context_limit,
        "concurrency_limit": plan.concurrency_limit,
        "tool_call_parser": plan.tool_call_parser,
        "reasoning_parser": plan.reasoning_parser,
        "vision_demand_gb": plan.vision_demand_gb,
        "node_requirements": [
            {
                "node": item.node,
                "memory_gb": item.memory_gb,
                "memory_evidence": item.memory_evidence.value,
                "disk_gb": item.disk_gb,
                "disk_evidence": item.disk_evidence.value,
            }
            for item in plan.node_requirements
        ],
        "warnings": list(plan.warnings),
    }


def plan_from_document(document: dict[str, Any]) -> LaunchPlan:
    """Rebuild the frozen LaunchPlan from target.json (fail closed)."""
    from family_cfo_model_manager.contracts import Evidence, NodeRequirement

    try:
        if document.get("schema_version") != 2:
            raise ValueError("unsupported target schema")
        plan = document["plan"]
        return LaunchPlan(
            main_model=plan["main_model"],
            main_revision=plan["main_revision"],
            vision_model=plan.get("vision_model"),
            vision_revision=plan.get("vision_revision"),
            photo_mode=PhotoMode(plan["photo_mode"]),
            topology=Topology(plan["topology"]),
            runtime_version=plan["runtime_version"],
            image_digest=plan["image_digest"],
            context_limit=int(plan["context_limit"]),
            concurrency_limit=int(plan["concurrency_limit"]),
            tool_call_parser=plan["tool_call_parser"],
            reasoning_parser=plan["reasoning_parser"],
            vision_demand_gb=plan.get("vision_demand_gb"),
            node_requirements=tuple(
                NodeRequirement(
                    node=item["node"],
                    memory_gb=float(item["memory_gb"]),
                    memory_evidence=Evidence(item["memory_evidence"]),
                    disk_gb=float(item["disk_gb"]),
                    disk_evidence=Evidence(item["disk_evidence"]),
                )
                for item in plan["node_requirements"]
            ),
            warnings=tuple(plan.get("warnings", ())),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise JournalError(
            ReasonCode.RUNTIME_STATE_CORRUPT, f"target plan is corrupt: {exc}"
        ) from exc


# --- executor process supervision -----------------------------------------------------------


def executor_command(
    job_id: str, *, python: str | None = None, package_src: str | None = None
) -> list[str]:
    """The fixed executor command. The CLI supplies the package source path so
    host-side execution never imports FastAPI/Pydantic."""
    python = python or sys.executable
    if package_src:
        return [python, "-c", _bootstrap_script(package_src), "--job-id", job_id]
    return [python, "-m", "family_cfo_model_manager.executor", "--job-id", job_id]


def _bootstrap_script(package_src: str) -> str:
    return (
        "import sys\n"
        f"sys.path.insert(0, {package_src!r})\n"
        "from family_cfo_model_manager.executor import main\n"
        "raise SystemExit(main(sys.argv[1:]))\n"
    )


def spawn_executor(
    deps: ExecutorDeps,
    job_id: str,
    lock_fd: int,
    *,
    package_src: str | None = None,
) -> subprocess.Popen:
    """Spawn the separately supervised executor, inheriting the flock fd.

    The child gets its own session (terminate() signals only this group) and
    FAMILY_CFO_SWAP_LOCK_FD pointing at the inherited descriptor."""
    command = executor_command(job_id, package_src=package_src)
    env = {
        **os.environ,
        "FAMILY_CFO_SWAP_LOCK_FD": str(lock_fd),
        "FAMILY_CFO_PROJECT_DIR": deps.project_dir,
    }
    try:
        return subprocess.Popen(
            command,
            cwd=deps.project_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(lock_fd,),
            env=env,
        )
    except OSError as exc:
        raise ExecutorRefusal(
            ReasonCode.EXECUTOR_START_FAILED,
            f"the executor process could not be started: {type(exc).__name__}",
        ) from exc


def terminate_executor(
    process: subprocess.Popen, grace: float = EXECUTOR_TERMINATE_GRACE_SECONDS
) -> None:
    """#167 discipline, applied to an executor child handle ONLY.

    SIGTERM the whole session group, give descendants the FULL grace (never
    reap the leader before the last signal - its PID pins the group), then
    SIGKILL, then reap. Never called for a handle we did not create."""
    if process.returncode is not None:
        process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.wait()
        return
    time.sleep(grace)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _spawn_executor(
    deps: ExecutorDeps, job_id: str, lock_fd: int, *, package_src: str | None = None
) -> subprocess.Popen:
    spawn = deps.spawn or spawn_executor
    try:
        return spawn(deps, job_id, lock_fd, package_src=package_src)
    except ExecutorRefusal:
        raise
    except Exception as exc:  # noqa: BLE001 - any spawn failure is terminal
        raise ExecutorRefusal(
            ReasonCode.EXECUTOR_START_FAILED,
            f"the executor process could not be started: {type(exc).__name__}",
        ) from exc


# --- the state machine (executor process body) ----------------------------------------------


class _Interrupted(Exception):
    pass


def run_job(deps: ExecutorDeps, job_id: str) -> int:
    """The executor process body: resume the durable state machine.

    Signal policy: while the decision is undecided, SIGTERM/SIGINT stop child
    work and run the restoration path; after a durable commit decision,
    interruption only detaches - it cannot request rollback (ADR 0081 S3)."""
    record = deps.journal.read_job_record(job_id)
    if record is None:
        return 2
    state: dict[str, bool] = {"interrupted": False}

    def interrupted(signum: int, _frame: Any) -> None:
        state["interrupted"] = True
        if record.decision is JobDecision.UNDECIDED:
            raise _Interrupted()

    previous = {
        sig: signal.signal(sig, interrupted)
        for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        return _dispatch(deps, record, state)
    except _Interrupted:
        # Before a commit decision: stop child work, attempt restoration.
        return _interrupted_restore(deps, record)
    except JournalError as exc:
        # A failed required journal write prevents the NEXT deployment
        # mutation: stop immediately, leave the durable state untouched.
        deps.journal.append_diagnostics(
            job_id, json.dumps({"event": "journal_failure", "detail": exc.detail})
        )
        return 3
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _dispatch(deps: ExecutorDeps, record: JobRecord, state: dict[str, bool]) -> int:
    if record.state in (JobState.ACCEPTED, JobState.STAGING):
        return _phase_staging(deps, record, state)
    if record.state in (JobState.LAUNCHING, JobState.VERIFYING):
        if record.decision is JobDecision.UNDECIDED:
            # Conservative recovery: no mutation completed before the crash is
            # provable; choose restore (plan: Restart and interruption).
            record = _transition(
                deps,
                record,
                state=JobState.RESTORING,
                decision=JobDecision.RESTORE,
                phase_deadline_at=_deadline_at(RESTORE_DEADLINE_SECONDS),
            )
        return _phase_restore(deps, record)
    if record.state is JobState.RESTORING:
        return _phase_restore(deps, record)
    if record.state is JobState.COMMIT_PENDING:
        return _phase_commit_pending(deps, record, state)
    if record.state is JobState.COMMIT_REPAIR_REQUIRED:
        return _phase_forward_repair(deps, record)
    return 0  # terminal records do not resume


def _load_plan(deps: ExecutorDeps, job_id: str) -> LaunchPlan:
    document = deps.journal.read_target_plan(job_id)
    if document is None:
        raise JournalError(ReasonCode.RUNTIME_STATE_CORRUPT, "target.json is missing")
    return plan_from_document(document)


def _load_baseline(deps: ExecutorDeps, job_id: str) -> deployment.DeploymentSnapshot | None:
    document = deps.journal.read_previous_snapshot(job_id)
    if document is None:
        return None
    return deployment.DeploymentSnapshot.from_dict(document)


def _phase_staging(
    deps: ExecutorDeps, record: JobRecord, state: dict[str, bool]
) -> int:
    """Staging: download/verify pinned artifacts while the old runtime still
    serves. Deadline persisted; restart never grants a fresh budget."""
    if record.phase_deadline_at is None:
        record = _transition(
            deps,
            record,
            state=JobState.STAGING,
            phase_deadline_at=_deadline_at(STAGING_DEADLINE_SECONDS),
        )
    remaining = _remaining(record)
    if remaining <= 0:
        return _fail_before_mutation(
            deps, record, ReasonCode.INSUFFICIENT_DISK, "the staging deadline expired"
        )
    plan = _load_plan(deps, record.job_id)
    baseline = _load_baseline(deps, record.job_id)
    try:
        _stage_artifacts(deps, plan, baseline, remaining, state)
    except _Interrupted:
        raise
    except DeploymentRefusal as exc:
        return _fail_before_mutation(deps, record, exc.reason_code, exc.message)
    except ExecutorRefusal as exc:
        return _fail_before_mutation(deps, record, exc.reason_code, exc.message)
    if state["interrupted"]:
        raise _Interrupted()
    record = deps.journal.read_job_record(record.job_id) or record
    record = _transition(
        deps,
        record,
        state=JobState.LAUNCHING,
        phase_deadline_at=_deadline_at(LAUNCH_DEADLINE_SECONDS),
    )
    return _phase_launch(deps, record, state)


def _stage_artifacts(
    deps: ExecutorDeps,
    plan: LaunchPlan,
    baseline: deployment.DeploymentSnapshot | None,
    remaining: float,
    state: dict[str, bool],
) -> None:
    """Download missing pinned artifacts into the shared model cache and verify
    rollback artifacts remain available. Never touches active configuration."""
    deadline = deps.clock() + remaining
    cache_mount = _model_cache_mount(deps)
    for model, revision in (
        (plan.main_model, plan.main_revision),
        (plan.vision_model, plan.vision_revision),
    ):
        if not model:
            continue
        _stage_one(
            deps, model, revision, cache_mount, deadline
        )
    # Rollback artifacts must remain locally available.
    if baseline is not None and baseline.main_model:
        _stage_one(
            deps,
            baseline.main_model,
            None,
            cache_mount,
            deadline,
            local_files_only=True,
        )


def _model_cache_mount(deps: ExecutorDeps) -> str:
    try:
        config = deps.compose.config(deps.project_dir, deployment._files_from_env())
    except DeploymentError as exc:
        raise ExecutorRefusal(ReasonCode.PREFLIGHT_UNAVAILABLE, str(exc)) from exc
    vllm = (config.get("services") or {}).get("vllm") or {}
    for mount in vllm.get("volumes") or []:
        if "huggingface" in str(mount):
            return str(mount)
    raise ExecutorRefusal(
        ReasonCode.PREFLIGHT_UNAVAILABLE,
        "the compose configuration declares no model cache mount",
    )


def _stage_one(
    deps: ExecutorDeps,
    model: str,
    revision: str | None,
    cache_mount: str,
    deadline: float,
    *,
    local_files_only: bool = False,
) -> None:
    """One allowlisted staging container: pinned repo/revision into the shared
    cache. The snippet is fixed; no user input reaches the command line."""
    volume, target = cache_mount.split(":", 1) if ":" in cache_mount else (cache_mount, "/root/.cache/huggingface")
    snippet = (
        "import json, sys\n"
        "from huggingface_hub import snapshot_download\n"
        f"path = snapshot_download(\n"
        f"    repo_id={model!r},\n"
        f"    revision={revision!r},\n"
        f"    local_files_only={local_files_only!r},\n"
        ")\n"
        "print(json.dumps({'path': path}))\n"
    )
    spec = ContainerSpec(
        image_id=_runtime_image_id_or_refuse(deps),
        entrypoint=("python", "-c"),
        command=(snippet,),
        env=(("HF_HUB_OFFLINE", "1" if local_files_only else "0"),),
        volumes=((volume, target, "rw"),),
        labels=(("family.cfo.apply", "staging"),),
    )
    timeout = max(1.0, deadline - deps.clock())
    try:
        result = deps.runner.run(spec, timeout)
    except RuntimeCheckRefusal as exc:
        raise ExecutorRefusal(exc.reason_code, exc.message) from exc
    if result.exit_code != 0:
        detail = (result.stderr or result.stdout)[-300:]
        raise ExecutorRefusal(
            ReasonCode.INSUFFICIENT_DISK if local_files_only else ReasonCode.PREFLIGHT_UNAVAILABLE,
            "staging could not prepare the model cache: " + detail.strip(),
        )


def _runtime_image_id_or_refuse(deps: ExecutorDeps) -> str:
    image_id, _reference = _installed_image_id(deps)
    return image_id


def _fail_before_mutation(
    deps: ExecutorDeps, record: JobRecord, reason: ReasonCode, message: str
) -> int:
    """Failure before any active-deployment mutation: terminal failed."""
    _transition(
        deps,
        record,
        state=JobState.FAILED,
        failure_code=reason,
        safe_message=message[:2048],
    )
    with deps.journal.locked():
        deps.journal.set_current_job(None)
    return 1


def _restore_after_mutation(
    deps: ExecutorDeps, record: JobRecord, reason: ReasonCode, message: str
) -> int:
    """A mutation already happened (or may have): the previous runtime is the
    only safe destination. Entering the restore phase is a PHASE TRANSITION,
    so the restore budget is stamped here - never a restart renewing an old
    phase. The triggering failure is recorded; a successful restore keeps it
    as the reason the apply ended."""
    current = deps.journal.read_job_record(record.job_id) or record
    current = _transition(
        deps,
        current,
        state=JobState.RESTORING,
        decision=(
            JobDecision.RESTORE
            if current.decision is JobDecision.UNDECIDED
            else current.decision
        ),
        failure_code=reason,
        safe_message=message[:2048],
        phase_deadline_at=_deadline_at(RESTORE_DEADLINE_SECONDS),
    )
    return _phase_restore(deps, current)


def _phase_launch(
    deps: ExecutorDeps, record: JobRecord, state: dict[str, bool]
) -> int:
    """Installing target runtime values and replacing the managed services.

    The launching state is persisted BEFORE the first active mutation."""
    plan = _load_plan(deps, record.job_id)
    remaining = _remaining(record)
    if remaining <= 0:
        return _restore_after_mutation(
            deps, record, ReasonCode.PREFLIGHT_UNAVAILABLE, "the launch deadline expired"
        )
    env_file = EnvFile.read(os.path.join(deps.project_dir, ".env"))
    before_keys = tuple(key for key in RUNTIME_ENV_KEYS if env_file.value(key) is None)
    values = launch_values(
        plan,
        node_memory_gb=_primary_memory_gb(deps),
        photo_node_memory_gb=_primary_memory_gb(deps),
        profile=deps.profiles.policy_for(plan.main_model, None),
    )
    if plan.photo_mode is PhotoMode.SEPARATE and values.get("VLLM_VISION_GPU_FRACTION") is None:
        return _fail_before_mutation(
            deps,
            record,
            ReasonCode.INSUFFICIENT_MEMORY,
            "the photo model's GPU fraction could not be assigned within the node budget",
        )
    if plan.topology is Topology.SINGLE and values.get("VLLM_GPU_FRACTION") is None:
        return _fail_before_mutation(
            deps,
            record,
            ReasonCode.INSUFFICIENT_MEMORY,
            "the main model's GPU fraction could not be assigned within the node budget",
        )
    added_keys = tuple(
        key for key in RUNTIME_ENV_KEYS if env_file.value(key) is None and values.get(key)
    )
    before = (env_file, before_keys, added_keys)
    try:
        env_file.write_runtime_keys(os.path.join(deps.project_dir, ".env"), values)
    except DeploymentError as exc:
        return _fail_before_mutation(deps, record, ReasonCode.JOURNAL_UNAVAILABLE, str(exc))
    if state["interrupted"]:
        raise _Interrupted()
    try:
        launch_target(
            deps.compose,
            deps.project_dir,
            plan,
            deployment.compose_files_for(deps.project_dir, plan.topology),
        )
    except DeploymentError as exc:
        return _restore_after_mutation(
            deps, record, ReasonCode.PREFLIGHT_UNAVAILABLE, f"the launch failed: {exc}"
        )
    del before
    record = deps.journal.read_job_record(record.job_id) or record
    return _phase_verify(deps, record, state)


def _primary_memory_gb(deps: ExecutorDeps) -> float | None:
    nodes = deps.hardware.nodes()
    return nodes[0].memory_gb if nodes else None


def _phase_verify(
    deps: ExecutorDeps, record: JobRecord, state: dict[str, bool]
) -> int:
    plan = _load_plan(deps, record.job_id)
    remaining = _remaining(record)
    verifier = Verifier(
        http=deps.http,
        compose_services=lambda: deployment.routing_services(
            deps.compose,
            deps.project_dir,
            deployment.compose_files_for(deps.project_dir, plan.topology),
        ),
        peer=deps.peer if plan.topology is Topology.CLUSTER else None,
        sleep=deps.sleep,
        clock=deps.clock,
    )
    expected = ExpectedRuntime.from_plan(
        plan, image_id=_runtime_image_id_or_refuse(deps), peer_host=_peer_host(deps)
    )
    deadline = deps.clock() + remaining
    outcome = verifier.verify(expected, deadline=deadline)
    if outcome.passed and outcome.evidence is not None:
        with deps.journal.locked():
            updated = replace(
                record,
                state=JobState.COMMIT_PENDING,
                decision=JobDecision.COMMIT,
                updated_at=datetime.now(UTC),
            )
            deps.journal.write_job_record(updated)
            deps.journal.write_verification_evidence(
                record.job_id, outcome.evidence.to_dict()
            )
        return _phase_commit_pending(deps, updated, state)
    message = outcome.message or "verification failed"
    return _restore_after_mutation(
        deps, record, outcome.reason_code or ReasonCode.RUNTIME_INCOMPATIBLE, message
    )


def _peer_host(deps: ExecutorDeps) -> str | None:
    env_file = EnvFile.read(os.path.join(deps.project_dir, ".env"))
    return env_file.value("CLUSTER_PEER_HOST")


def _phase_commit_pending(
    deps: ExecutorDeps, record: JobRecord, state: dict[str, bool]
) -> int:
    """Await configuration acknowledgement. NEVER rolls back: the durable
    commit decision is monotonic. The executor keeps the verification evidence
    fresh (<= 30 s) so the reconciler can commit safely, runs post-commit
    finalization once the receipt is recorded, then persists succeeded.

    The wait is bounded by the PERSISTED phase deadline (inherited from the
    launch phase). When it expires without a receipt - for example the
    candidate died while the API was unavailable - the job lands in
    commit_repair_required (an explicit forward-repair state) instead of
    stranding commit_pending forever; the decision itself is never reversed."""
    if record.phase_deadline_at is None:
        # Only an explicit forward-repair retry can arrive here without a
        # deadline; the retry is a NEW bounded attempt, not a restart (a
        # restart never grants a fresh budget).
        record = _transition(
            deps,
            record,
            phase_deadline_at=_deadline_at(LAUNCH_DEADLINE_SECONDS),
        )
    while True:
        current = deps.journal.read_job_record(record.job_id)
        if current is None:
            return 3
        if current.state is JobState.SUCCEEDED:
            return 0
        if current.config_receipt_id:
            return _phase_finalize(deps, current)
        if _remaining(current) <= 0:
            # The deadline expired before the household configuration was
            # acknowledged. The durable commit decision stands (monotonic,
            # never rolled back); the job needs explicit forward repair.
            return _mark_repair_required(
                deps,
                current,
                "the commit acknowledgement deadline expired before the "
                "household configuration was acknowledged; the durable commit "
                "decision stands and needs forward repair",
            )
        # Keep the evidence fresh while waiting for the reconciler.
        plan = _load_plan(deps, record.job_id)
        outcome = _verify_target_once(deps, plan)
        if outcome.passed and outcome.evidence is not None:
            _persist_evidence(deps, record.job_id, outcome.evidence.to_dict())
        deps.sleep(COMMIT_REVERIFY_SECONDS)
        # Interruption after a commit decision only detaches the caller.
        state["interrupted"] = False


def _verify_target_once(deps: ExecutorDeps, plan: LaunchPlan):
    verifier = Verifier(
        http=deps.http,
        compose_services=lambda: deployment.routing_services(
            deps.compose,
            deps.project_dir,
            deployment.compose_files_for(deps.project_dir, plan.topology),
        ),
        peer=deps.peer if plan.topology is Topology.CLUSTER else None,
        sleep=deps.sleep,
        clock=deps.clock,
    )
    expected = ExpectedRuntime.from_plan(
        plan, image_id=_runtime_image_id_or_refuse(deps), peer_host=_peer_host(deps)
    )
    deadline = deps.clock() + verification_module.PROBE_TIMEOUT_SECONDS * (
        verification_module.MAX_ATTEMPTS + 1
    )
    return verifier.verify(expected, deadline=deadline)


def _phase_finalize(deps: ExecutorDeps, record: JobRecord) -> int:
    """Post-commit finalization: promote env by recreating api/worker with
    their CURRENT application images, then verify routing/health.

    Failure is concrete and forward-repairable: commit_repair_required - never
    an automatic rollback while PostgreSQL points at the new runtime."""
    plan = _load_plan(deps, record.job_id)
    try:
        deps.compose.up(
            deps.project_dir,
            deployment._files_from_env(),
            ("api", "worker"),
            scale={},
            no_deps=True,
        )
    except DeploymentError as exc:
        return _mark_repair_required(deps, record, str(exc))
    routing_ok, message = _verify_application_routing(
        deps, plan, budget_seconds=_remaining(record) or None
    )
    if not routing_ok:
        return _mark_repair_required(deps, record, message or "routing verification failed")
    _transition(
        deps,
        record,
        state=JobState.SUCCEEDED,
        phase_deadline_at=None,
    )
    with deps.journal.locked():
        deps.journal.set_current_job(None)
    return 0


def _verify_application_routing(
    deps: ExecutorDeps, plan: LaunchPlan, *, budget_seconds: float | None
) -> tuple[bool, str | None]:
    try:
        services = deployment.routing_services(
            deps.compose, deps.project_dir, deployment._files_from_env()
        )
    except DeploymentError as exc:
        return False, str(exc)
    api = next((s for s in services if s.name == "api"), None)
    if api is None or not api.running:
        return False, "the application api service is not running"
    env = api.env
    if env.get("FAMILY_CFO_AI_MODEL") != plan.main_model:
        return False, "application chat routing does not target the committed model"
    expected_supports = "true" if plan.photo_mode is PhotoMode.MAIN else "false"
    if env.get("FAMILY_CFO_AI_SUPPORTS_VISION", "").lower() != expected_supports:
        return False, "application photo routing does not match the committed mode"
    if plan.photo_mode is PhotoMode.SEPARATE:
        if env.get("FAMILY_CFO_AI_VISION_MODEL") != plan.vision_model:
            return False, "application photo routing does not target the committed describer"
    api_health = (
        os.environ.get("FAMILY_CFO_API_HEALTH_URL", "").strip()
        or "http://api:8000/api/v1/health"
    )
    # Bounded readiness wait, same probe discipline as verification: the
    # recreated api waits for PostgreSQL and runs migrations before it opens
    # its socket (docker/entrypoint-api.sh), so a refused connection during
    # that window is a normal restart, not a failure. Requests use the 30 s
    # probe timeout, 5 s apart, within the remaining phase budget; when no
    # budget remains (a late acknowledgement), at most MAX_ATTEMPTS complete
    # attempts give explicit forward repair a real window instead of failing
    # on a stale deadline. An exhausted budget ends in commit_repair_required
    # - never a false success and never an unbounded loop.
    budget = budget_seconds
    attempts = 0
    while True:
        timeout = (
            verification_module.PROBE_TIMEOUT_SECONDS
            if budget is None
            else min(verification_module.PROBE_TIMEOUT_SECONDS, max(1.0, budget))
        )
        status, _payload = deps.http.get_json(api_health, timeout)
        if status == 200:
            return True, None
        attempts += 1
        gap = verification_module.ATTEMPT_GAP_SECONDS
        if budget is None:
            if attempts >= verification_module.MAX_ATTEMPTS:
                return False, "the application api health check failed"
        else:
            if gap >= budget:
                return False, "the application api health check failed"
            budget -= gap
        deps.sleep(gap)


def _mark_repair_required(deps: ExecutorDeps, record: JobRecord, message: str) -> int:
    _transition(
        deps,
        record,
        state=JobState.COMMIT_REPAIR_REQUIRED,
        safe_message=message[:2048],
    )
    return 1


def _phase_forward_repair(deps: ExecutorDeps, record: JobRecord) -> int:
    """Forward repair only - never a rollback of a committed decision."""
    current = deps.journal.read_job_record(record.job_id)
    if current is None:
        return 3
    if current.decision is not JobDecision.COMMIT:
        return 1
    if not current.config_receipt_id:
        # The SQL commit never landed (for example the reconciler could not
        # reach the manager before the deadline expired). Finalizing now would
        # report succeeded while the household settings were never written -
        # the exact lie ADR 0081 forbids. Re-await the acknowledgement
        # instead: back to commit_pending with a fresh bounded window so the
        # reconciler can still execute the durable commit decision.
        current = _transition(
            deps,
            current,
            state=JobState.COMMIT_PENDING,
            phase_deadline_at=_deadline_at(LAUNCH_DEADLINE_SECONDS),
        )
        return _phase_commit_pending(deps, current, {"interrupted": False})
    return _phase_finalize(deps, current)


def _phase_restore(deps: ExecutorDeps, record: JobRecord) -> int:
    """Restore the previous verified snapshot (or the empty baseline)."""
    if record.phase_deadline_at is None:
        record = _transition(
            deps,
            record,
            state=JobState.RESTORING,
            phase_deadline_at=_deadline_at(RESTORE_DEADLINE_SECONDS),
        )
    remaining = _remaining(record)
    baseline = _load_baseline(deps, record.job_id)
    try:
        if baseline is None:
            # Explicit empty baseline restore (first deployment).
            plan = _load_plan(deps, record.job_id)
            added = tuple(
                key
                for key in RUNTIME_ENV_KEYS
                if plan.main_model and key not in ("CLUSTER_PEER_HOST",
                                                   "CLUSTER_PEER_PORT",
                                                   "CLUSTER_NCCL_IFNAME",
                                                   "CLUSTER_HEAD_ADDR")
            )
            empty_baseline_restore(
                deps.compose,
                deps.project_dir,
                deployment._files_from_env(),
                added,
                EnvFile.read(os.path.join(deps.project_dir, ".env")),
                os.path.join(deps.project_dir, ".env"),
            )
            return _fail_no_previous(deps, record)
        restore_runtime(
            deps.compose,
            deps.project_dir,
            baseline,
            EnvFile.read(os.path.join(deps.project_dir, ".env")),
            os.path.join(deps.project_dir, ".env"),
        )
    except DeploymentError as exc:
        return _mark_recovery_failed(deps, record, str(exc))
    except DeploymentRefusal as exc:
        return _mark_recovery_failed(deps, record, exc.message)
    if remaining <= 0:
        return _mark_recovery_failed(
            deps, record, "the restoration deadline expired before verification"
        )
    # The restore path must repeat the EXACT readiness + probe sequence.
    running_values = runtime_values_from_running(
        deployment.routing_services(
            deps.compose, deps.project_dir, baseline.compose_files
        )
    )
    expected = _expected_from_snapshot(baseline, running_values)
    verifier = Verifier(
        http=deps.http,
        compose_services=lambda: deployment.routing_services(
            deps.compose, deps.project_dir, baseline.compose_files
        ),
        peer=deps.peer if baseline.topology is Topology.CLUSTER else None,
        sleep=deps.sleep,
        clock=deps.clock,
    )
    deadline = deps.clock() + remaining
    outcome = verifier.verify(expected, deadline=deadline)
    if outcome.passed and outcome.evidence is not None:
        with deps.journal.locked():
            updated = replace(
                record,
                state=JobState.RESTORED,
                updated_at=datetime.now(UTC),
            )
            deps.journal.write_job_record(updated)
            deps.journal.write_verification_evidence(
                record.job_id, outcome.evidence.to_dict()
            )
        with deps.journal.locked():
            deps.journal.set_current_job(None)
        return 0
    return _mark_recovery_failed(
        deps, record, outcome.message or "the restored runtime failed verification"
    )


def _fail_no_previous(deps: ExecutorDeps, record: JobRecord) -> int:
    _transition(
        deps,
        record,
        state=JobState.FAILED,
        failure_code=ReasonCode.NO_PREVIOUS_RUNTIME,
        safe_message="the first deployment failed; there was no previous "
        "runtime to restore",
    )
    with deps.journal.locked():
        deps.journal.set_current_job(None)
    return 1


def _mark_recovery_failed(deps: ExecutorDeps, record: JobRecord, message: str) -> int:
    _transition(
        deps,
        record,
        state=JobState.RECOVERY_FAILED,
        safe_message=message[:2048],
    )
    return 1


def _interrupted_restore(deps: ExecutorDeps, record: JobRecord) -> int:
    """CLI interruption before a commit decision: stop child work and attempt
    restoration (the caller cannot request rollback after a commit)."""
    current = deps.journal.read_job_record(record.job_id)
    if current is None:
        return 2
    if current.decision is JobDecision.COMMIT:
        return 0  # detached; the commit stands
    current = _transition(
        deps,
        current,
        state=JobState.RESTORING,
        decision=JobDecision.RESTORE,
    )
    return _phase_restore(deps, current)


# --- restart / periodic recovery scan -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    resumed: tuple[str, ...] = ()
    held_by_owner: tuple[str, ...] = ()
    awaiting_attention: tuple[str, ...] = ()


def scan_and_recover(deps: ExecutorDeps, *, package_src: str | None = None) -> RecoveryReport:
    """The startup + periodic scan (plan: Restart and interruption).

    - Another process holding the directory flock is NEVER killed and NEVER
      superseded: its journal is read and progress reported.
    - A free lock with an unfinished job is resumed before admitting another.
    - No ownership is ever inferred from a stored PID; no stale PID is
      signalled; only freshly spawned child handles are terminated later."""
    resumed: list[str] = []
    held: list[str] = []
    attention: list[str] = []
    for record in deps.journal.list_job_records():
        if record.state.terminal and record.state is not JobState.COMMIT_REPAIR_REQUIRED:
            if record.state is not JobState.RECOVERY_FAILED:
                continue
        if record.state in (JobState.RECOVERY_FAILED, JobState.COMMIT_REPAIR_REQUIRED):
            attention.append(record.job_id)
            continue
        fd = os.open(deps.project_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            held.append(record.job_id)
            continue
        try:
            if record.state in (JobState.LAUNCHING, JobState.VERIFYING) and (
                record.decision is JobDecision.UNDECIDED
            ):
                record = _transition(
                    deps,
                    record,
                    state=JobState.RESTORING,
                    decision=JobDecision.RESTORE,
                    phase_deadline_at=_deadline_at(RESTORE_DEADLINE_SECONDS),
                )
            _spawn_executor(deps, record.job_id, fd, package_src=package_src)
            resumed.append(record.job_id)
        finally:
            os.close(fd)  # the executor inherited the open file description
    return RecoveryReport(
        resumed=tuple(resumed), held_by_owner=tuple(held), awaiting_attention=tuple(attention)
    )


# --- process entry -------------------------------------------------------------------------


def _build_deps() -> ExecutorDeps:
    from family_cfo_model_manager.adapters import FileProfiles

    project_dir = os.environ.get("FAMILY_CFO_PROJECT_DIR", os.getcwd())
    package_root = Path(__file__).resolve().parent
    return ExecutorDeps(
        journal=Journal(os.path.join(project_dir, ".runtime-swap", "v2")),
        compose=deployment.DockerComposeAdapter(),
        http=UrllibRuntimeHttp(),
        runner=DockerContainerRunner(),
        profiles=FileProfiles(package_root / "profiles.json"),
        metadata=HubMetadataSource(),
        hardware=LocalHardwareSource(),
        peer=deployment.SshPeerInspector(),
        project_dir=project_dir,
        cache_probe=_docker_cache_probe,
    )


def _docker_cache_probe(repo_id: str, revision: str | None) -> int:
    """Bytes of the repo's pinned snapshot already present in the model cache
    (allowlisted read-only exec). Unknown evidence -> raises; never zero."""
    result = subprocess.run(
        [
            "docker", "exec", "vllm", "python", "-c",
            "import json,os,sys\n"
            "root='/root/.cache/huggingface/hub'\n"
            f"repo={repo_id!r}\n"
            "d=os.path.join(root,'models--'+repo.replace('/','--'))\n"
            "print(json.dumps({'exists': os.path.isdir(d)}))\n",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0 or "true" not in result.stdout:
        raise MetadataUnavailable("the model is not in the local cache")
    return 0  # presence probed; exact byte accounting is staging's job


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "--job-id":
        print("usage: executor --job-id <uuid>", file=sys.stderr)
        return 2
    deps = _build_deps()
    return run_job(deps, args[1])


if __name__ == "__main__":
    sys.exit(main())

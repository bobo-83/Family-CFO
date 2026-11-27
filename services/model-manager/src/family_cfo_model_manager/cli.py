"""Host-side CLI intent parsing + admission (issue #170 Item 3).

`scripts/swap-model.sh` retains argument compatibility and the #167 lock
entry, then delegates here. The CLI runs HOST-SIDE with stdlib only: it
validates the inherited directory-flock descriptor (the same `check_lock`
semantics as scripts/lib/swap_lock.py - status is not ownership), builds the
shared engine dependencies (manager_state.build_manager_deps - no FastAPI,
no Pydantic), translates the positional intent into the normalized v2
admission request, and hands the deployment to executor.admit().

Interruption discipline (ADR 0081 S3): before a durable commit decision an
interrupt stops child work and attempts restoration by forwarding the
signal to the separately supervised executor; after `decision=commit` an
interrupt only detaches - the commit stands and the executor finishes it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from family_cfo_model_manager import executor as executor_module
from family_cfo_model_manager.contracts import (
    AdmissionRequest,
    JobDecision,
    JobOrigin,
    JobState,
    PhotoMode,
    Topology,
)
from family_cfo_model_manager.journal import Journal, JournalError
from family_cfo_model_manager.manager_state import build_manager_deps

# The old script's default describer, kept as the no-vision-arg fallback
# for a main model that does not verify as vision-capable.
DEFAULT_VISION_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
USAGE = "usage: scripts/swap-model.sh [--cluster] <main-model> [vision-model|none]"
LOCK_FD_ENV = "FAMILY_CFO_SWAP_LOCK_FD"


def log(message: str) -> None:
    print(f"\033[1;36m==>\033[0m {message}", flush=True)


def die(message: str) -> None:
    print(f"\033[1;31mError:\033[0m {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


# --- the inherited lock descriptor ---------------------------------------------------


def validate_lock(directory: str) -> int:
    """Validate + re-flock the inherited descriptor (swap_lock.check_lock
    semantics). The descriptor is the ONLY ownership proof; an env marker
    alone is never a bypass, and a contended lock refuses."""
    marker = os.environ.get(LOCK_FD_ENV, "").strip()
    if not marker:
        die(
            "the swap lock must be held by scripts/lib/swap_lock.py - run"
            " scripts/swap-model.sh instead of invoking the CLI directly"
        )
    try:
        fd = int(marker)
        if fd < 0:
            raise ValueError("invalid descriptor")
    except ValueError:
        die("Could not lock the model swap deployment directory.")
    try:
        if not os.path.samestat(os.fstat(fd), os.stat(directory)):
            raise ValueError("descriptor is not the deployment directory")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        die("A model swap is already in progress. Wait for it to finish, then retry.")
    except (OSError, ValueError):
        die("Could not lock the model swap deployment directory.")
    return fd


# --- host-side runtime reachability ---------------------------------------------------


def _container_ip(service: str, project: str) -> str | None:
    """The running compose container's IP (best effort; read-only)."""
    try:
        listing = subprocess.run(
            [
                "docker", "ps", "-q",
                "--filter", f"label=com.docker.compose.project={project}",
                "--filter", f"label=com.docker.compose.service={service}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        ids = listing.stdout.split()
        if listing.returncode != 0 or not ids:
            return None
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", ids[0]],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return None
        networks = json.loads(result.stdout)
        for network in networks.values():
            address = (network or {}).get("IPAddress")
            if address:
                return address
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return None
    return None


def export_runtime_urls() -> None:
    """Make the runtime reachable from a HOST-side executor.

    The compose runtime is never published to the host, so verification
    URLs are resolved from the running containers' IPs and exported for
    the executor process to inherit. An explicitly configured URL always
    wins; a failed resolution keeps the in-network defaults (verification
    then fails closed with its own reason - never silently skipped).
    """
    project = (
        os.environ.get("COMPOSE_PROJECT_NAME", "").strip() or "family-cfo"
    )
    targets = (
        ("vllm", "FAMILY_CFO_MAIN_RUNTIME_URL", "http://{ip}:8000"),
        ("vllm-vision", "FAMILY_CFO_PHOTO_RUNTIME_URL", "http://{ip}:8000"),
        (
            "api",
            "FAMILY_CFO_API_HEALTH_URL",
            "http://{ip}:8000/api/v1/health",
        ),
    )
    for service, variable, template in targets:
        if os.environ.get(variable, "").strip():
            continue  # explicit configuration wins
        address = _container_ip(service, project)
        if address:
            os.environ[variable] = template.format(ip=address)


# --- positional intent -> normalized request -------------------------------------------


def parse_arguments(argv: list[str]) -> tuple[str, str | None, bool]:
    """(main_model, vision_argument, cluster) from the legacy positional form."""
    cluster = False
    positional: list[str] = []
    for argument in argv:
        if argument == "--cluster":
            cluster = True
        elif argument in ("-h", "--help"):
            print(USAGE)
            raise SystemExit(0)
        else:
            positional.append(argument)
    if not 1 <= len(positional) <= 2:
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)
    vision = positional[1] if len(positional) == 2 else None
    if vision is not None and not vision.strip():
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)
    return positional[0], vision, cluster


def resolve_photo_intent(
    deps: executor_module.ExecutorDeps,
    main_model: str,
    vision_argument: str | None,
) -> tuple[PhotoMode, str | None, str]:
    """Old-script photo semantics, expressed as normalized placement.

    A named vision model is a deliberate separate describer; `none` is
    photos off; NO argument means the main describes photos itself when the
    installed metadata verifies image-input support, else the default
    describer serves them separately (never an implicit guess).
    """
    if vision_argument is not None and vision_argument.lower() != "none":
        return PhotoMode.SEPARATE, vision_argument, f"photo describer: {vision_argument}"
    if vision_argument is not None:
        return PhotoMode.OFF, None, "photo analysis disabled"
    try:
        facts = deps.metadata.facts_for(main_model)
        multimodal = facts.multimodal
    except Exception:  # noqa: BLE001 - unknown evidence never guesses a mode
        multimodal = None
    if multimodal is True:
        return (
            PhotoMode.MAIN,
            None,
            f"main model '{main_model}' is vision-capable - it will describe photos itself",
        )
    return (
        PhotoMode.SEPARATE,
        DEFAULT_VISION_MODEL,
        f"photo describer: {DEFAULT_VISION_MODEL}",
    )


def resolve_topology(
    deps: executor_module.ExecutorDeps,
    main_model: str,
    cluster: bool,
) -> tuple[Topology, bool]:
    """`--cluster` requests TP2 explicitly; a reviewed min_nodes:2 profile
    implies it (the old script's cluster-tier behaviour). Otherwise the
    plan is single-node - the CLI never escalates on its own."""
    if cluster:
        return Topology.CLUSTER, True
    profile = deps.profiles.policy_for(main_model, None)
    if profile is None:
        try:
            facts = deps.metadata.facts_for(main_model)
        except Exception:  # noqa: BLE001
            facts = None
        if facts is not None:
            profile = deps.profiles.policy_for(main_model, facts.architecture)
    if profile is not None and profile.min_nodes >= 2:
        return Topology.CLUSTER, True
    return Topology.SINGLE, False


# --- executor child supervision ---------------------------------------------------------


class _SpawnCapture:
    """Record the executor child handle the admission pipeline spawns, and
    pin the package source path so the host-side child never depends on an
    inherited PYTHONPATH."""

    def __init__(self, package_src: str) -> None:
        self._package_src = package_src
        self.process: subprocess.Popen | None = None

    def __call__(
        self,
        deps: executor_module.ExecutorDeps,
        job_id: str,
        lock_fd: int,
        *,
        package_src: str | None = None,
    ) -> subprocess.Popen:
        self.process = executor_module.spawn_executor(
            deps, job_id, lock_fd, package_src=package_src or self._package_src
        )
        return self.process


class _Interrupted(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum


def _outcome_message(journal: Journal, job_id: str) -> str:
    """A one-line, safe outcome from the durable record."""
    try:
        record = journal.read_job_record(job_id)
    except JournalError:
        record = None
    if record is None:
        return "the swap job could not be found in the journal"
    detail = record.safe_message or ""
    state = record.state
    if state.value == "succeeded":
        return "swap complete - the new model passed verification"
    if state.value == "restored":
        prefix = "swap failed; the previous runtime was restored and verified"
    elif state.value == "recovery_failed":
        prefix = "restoration failed and needs attention"
    elif state.value == "commit_repair_required":
        prefix = "the swap committed but finalization needs forward repair"
    elif state.value == "failed":
        prefix = "swap failed before any deployment change"
    else:
        return f"the swap is still {state.value} (job {job_id})"
    return f"{prefix}: {detail}" if detail else prefix


def wait_for_executor(
    process: subprocess.Popen, journal: Journal, job_id: str
) -> int:
    """Wait for the executor, translating interruption per ADR 0081 S3.

    Before a durable commit decision the signal is forwarded to the
    executor, which stops child work and restores; after `decision=commit`
    interruption only detaches and the commit stands."""
    received: int | None = None

    def interrupted(signum: int, _frame: object) -> None:
        nonlocal received
        if received is None:
            received = signum
            raise _Interrupted(signum)

    previous = {
        sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        code = process.wait()
        log(_outcome_message(journal, job_id))
        # The executor exits 0 whenever it completed its own phase cleanly -
        # including a RESTORED rollback. The operator's exit code reflects the
        # DURABLE outcome: only a succeeded apply is success.
        try:
            record = journal.read_job_record(job_id)
        except JournalError:
            record = None
        if record is not None and record.state is JobState.SUCCEEDED:
            return 0
        return code or 1
    except _Interrupted as exc:
        return _detach_or_restore(process, journal, job_id, exc.signum)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _detach_or_restore(
    process: subprocess.Popen,
    journal: Journal,
    job_id: str,
    signum: int,
) -> int:
    try:
        record = journal.read_job_record(job_id)
    except JournalError:
        record = None
    if record is not None and record.decision is JobDecision.COMMIT:
        # Monotonic decision: the household transaction committed, so the
        # caller can only detach - never request rollback.
        log(
            f"the swap passed verification and is committing; it continues"
            f" in the background (job {job_id})"
        )
        return 128 + signum
    print(
        "\033[1;36m==>\033[0m Interrupted - stopping the swap and restoring the"
        " previous runtime\033[0m",
        flush=True,
    )
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(process.pid, signal.SIGTERM)  # the executor restores

    def force(_signum: int, _frame: object) -> None:
        # A second signal during restoration force-stops the child; the
        # durable journal keeps the job recoverable.
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(process.pid, signal.SIGKILL)
        os._exit(128 + signum)

    signal.signal(signal.SIGINT, force)
    signal.signal(signal.SIGTERM, force)
    process.wait()
    log(_outcome_message(journal, job_id))
    return 128 + signum


# --- entry point ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    main_model, vision_argument, cluster = parse_arguments(args)

    project_dir = os.environ.get("FAMILY_CFO_PROJECT_DIR", os.getcwd())
    lock_fd = validate_lock(project_dir)
    export_runtime_urls()

    # The frozen deps get the spawn seam through replace(): the capture also
    # pins the package source path so the executor child never depends on an
    # inherited (possibly relative) PYTHONPATH.
    import dataclasses

    capture = _SpawnCapture(str(Path(__file__).resolve().parent.parent))
    deps = dataclasses.replace(build_manager_deps(project_dir), spawn=capture)

    photo_mode, vision_model, photo_note = resolve_photo_intent(
        deps, main_model, vision_argument
    )
    topology, cluster_consent = resolve_topology(deps, main_model, cluster)
    if photo_mode is PhotoMode.MAIN:
        log(photo_note)
    elif photo_mode is PhotoMode.SEPARATE:
        log(photo_note)
    else:
        log(photo_note)

    request = AdmissionRequest(
        request_id=str(uuid.uuid4()),
        main_model=main_model,
        vision_model=vision_model,
        photo_mode=photo_mode,
        topology=topology,
        cluster_consent=cluster_consent,
        origin=JobOrigin.CLI,
        runtime_key="vllm",
    )
    try:
        record = executor_module.admit(deps, request, lock_fd=lock_fd)
    except executor_module.ExecutorRefusal as exc:
        die(f"{exc.message}")
    except JournalError as exc:
        die(f"{exc.detail}")

    assert deps.spawn is not None and isinstance(deps.spawn, _SpawnCapture)
    log(
        f"swap accepted (job {record.job_id}); the executor replaces the"
        " runtime + app containers only after verification passes"
    )
    process = deps.spawn.process
    if process is None:
        # A replay of an identical request returns its job without spawning.
        log(_outcome_message(deps.journal, record.job_id))
        return 0 if record.state.terminal and record.state.value == "succeeded" else 1
    return wait_for_executor(process, deps.journal, record.job_id)


if __name__ == "__main__":
    sys.exit(main())
"""Structured Docker/Compose/peer adapter and the immutable DeploymentSnapshot
(ADR 0081 S4, issue #170 Item 3).

Everything the apply transaction knows about the actual deployment is captured
here: the effective Compose configuration, running image IDs, managed
services, structured runtime arguments, cache mounts, routing, and the
declared peer. Runtime-owned `.env` keys are compared against the RUNNING
deployment (unexplained drift -> deployment_drift) and replaced atomically
(temp + rename, only allowlisted keys, never a whole-file rewrite, mode and
ownership preserved).

Peer inspection runs a bounded SSH command allowlist over the enrolled peer
(host-key-verified; read-only probes: Ray status, per-GPU memory, image ID);
any allowlist, timeout, or parse failure rejects the cluster topology. No
user-supplied command, image name, path, or URL is ever executed.

Importable without FastAPI/Pydantic (the CLI shares this engine).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from family_cfo_model_manager.contracts import PhotoMode, ReasonCode, Topology
from family_cfo_model_manager.planning import LaunchPlan, ProfilePolicy

# --- the runtime-key allowlist (one module, tested against Compose inputs) ------
# These are the ONLY `.env` keys the apply flow may read, write, snapshot, or
# restore. Never snapshot credentials (HUGGING_FACE_HUB_TOKEN stays out),
# database settings, household data, or whole application environments.
RUNTIME_ENV_KEYS: tuple[str, ...] = (
    # Model + parser + fraction + vision keys written by the previous script.
    "VLLM_MODEL",
    "VLLM_TOOL_PARSER",
    "VLLM_REASONING_PARSER",
    "VLLM_EXTRA_ARGS",
    "VLLM_GPU_FRACTION",
    "VLLM_VISION_GPU_FRACTION",
    "VLLM_VISION_MODEL",
    "VLLM_MAX_MODEL_LEN",
    "FAMILY_CFO_AI_SUPPORTS_VISION",
    "FAMILY_CFO_AI_VISION_ENABLED",
    # Revision/context keys required by reviewed profiles (pinned revisions).
    "VLLM_REVISION",
    "VLLM_VISION_REVISION",
    # Topology keys (declared peer; owned by setup-cluster.sh, restored only).
    "CLUSTER_PEER_HOST",
    "CLUSTER_PEER_PORT",
    "CLUSTER_NCCL_IFNAME",
    "CLUSTER_HEAD_ADDR",
)

MANAGED_SERVICES: tuple[str, ...] = ("vllm", "vllm-vision")
# The verifier and post-commit routing checks additionally read the
# application `api` service's routing env; snapshots/restore never include it.
ROUTING_SERVICES: tuple[str, ...] = ("vllm", "vllm-vision", "api")
# The ONLY service env keys the engine ever consumes (application chat +
# photo routing). Everything else - tokens, database settings, whole
# application environments - is scrubbed before it can reach a snapshot or
# a verifier.
ROUTING_ENV_KEYS: tuple[str, ...] = (
    "FAMILY_CFO_AI_MODEL",
    "FAMILY_CFO_AI_SUPPORTS_VISION",
    "FAMILY_CFO_AI_VISION_ENABLED",
    "FAMILY_CFO_AI_VISION_MODEL",
)
CLUSTER_OVERLAY = "docker-compose.cluster.yml"
DEFAULT_COMPOSE_FILES: tuple[str, ...] = ("docker-compose.yml",)

_DOCKER_TIMEOUT_SECONDS = 30.0
_PEER_TIMEOUT_SECONDS = 15.0

# The peer SSH allowlist: exactly these read-only probes, nothing else.
PEER_PROBES: dict[str, str] = {
    "ray_status": "ray status",
    "gpu_memory": "nvidia-smi --query-gpu=index,memory.used,memory.total "
    "--format=csv,noheader,nounits",
    "image_id": "docker inspect --format {{.Id}} vllm",
    "cache_disk": "df -k -P /root/.cache/huggingface",
}


class DeploymentError(RuntimeError):
    """Deployment infrastructure failure (Compose/Docker could not be used)."""


class DeploymentRefusal(Exception):
    """A stable refusal about the deployment (drift, peer, not-ready)."""

    def __init__(self, reason_code: ReasonCode, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


# --- .env handling -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnvFile:
    """Parsed `.env`: ordered KEY=VALUE lines with comments preserved."""

    lines: tuple[str, ...]

    @classmethod
    def read(cls, path: Path | str) -> EnvFile:
        try:
            raw = Path(path).read_text()
        except FileNotFoundError:
            return cls(lines=())
        except OSError as exc:
            raise DeploymentError(f"could not read {Path(path).name}: {exc}") from exc
        return cls(lines=tuple(raw.splitlines(keepends=True)))

    def value(self, key: str) -> str | None:
        """The current value of key, or None when absent."""
        for line in self.lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            candidate, _, raw_value = stripped.partition("=")
            if candidate.strip() == key:
                return _unquote(raw_value.strip())
        return None

    def keys(self) -> tuple[str, ...]:
        found = []
        for line in self.lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            found.append(stripped.partition("=")[0].strip())
        return tuple(found)

    def runtime_values(self) -> dict[str, str | None]:
        """Presence + value of every allowlisted runtime key."""
        return {key: self.value(key) for key in RUNTIME_ENV_KEYS}

    def write_runtime_keys(self, path: Path | str, updates: dict[str, str | None]) -> None:
        """Atomically replace ONLY runtime keys; never a whole-file rewrite.

        None removes a key that must not exist afterwards. Unrelated keys,
        comments, ordering, file mode, and ownership are preserved.
        """
        path = Path(path)
        try:
            stat = path.stat()
            mode, uid, gid = stat.st_mode, stat.st_uid, stat.st_gid
        except FileNotFoundError:
            mode, uid, gid = 0o600, -1, -1
        except OSError as exc:
            raise DeploymentError(f"could not stat {path.name}: {exc}") from exc

        pending = dict(updates)
        new_lines: list[str] = []
        for line in self.lines:
            stripped = line.strip()
            key = None
            if not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
            if key in pending:
                value = pending.pop(key)
                if value is not None:
                    new_lines.append(f"{key}={value}\n")
                # A removal simply drops the line.
            else:
                new_lines.append(line)
        for key, value in pending.items():
            if value is not None:  # removals of absent keys are no-ops
                new_lines.append(f"{key}={value}\n")

        directory = path.parent
        try:
            fd, temp_name = tempfile.mkstemp(dir=directory, prefix=".env-")
            os.fchmod(fd, mode & 0o777)
            with os.fdopen(fd, "w") as handle:
                handle.write("".join(new_lines))
                handle.flush()
                os.fsync(handle.fileno())
            if uid >= 0:
                os.chown(temp_name, uid, gid)
            os.replace(temp_name, path)
            dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            raise DeploymentError(f"could not update {path.name}: {exc}") from exc


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def runtime_key_hash(values: dict[str, str | None]) -> str:
    """A stable digest over the allowlisted runtime keys (presence + value).

    Credentials never enter this map: the allowlist contains none.
    """
    import hashlib

    payload = json.dumps(
        {key: values.get(key) for key in RUNTIME_ENV_KEYS},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# --- inspection ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServiceInspection:
    """One managed service's effective configuration + running identity."""

    name: str
    declared_image: str | None
    image_id: str | None
    running: bool
    scale: int
    restart_policy: str | None
    env: dict[str, str]
    command: str | None
    restart_count: int = 0


@dataclass(frozen=True, slots=True)
class DeploymentSnapshot:
    """Immutable capture of the working deployment (rollback authority)."""

    runtime_env: dict[str, str | None]  # presence + value of every allowlisted key
    services: tuple[ServiceInspection, ...]
    compose_files: tuple[str, ...]
    topology: Topology
    photo_mode: PhotoMode
    main_model: str | None
    vision_model: str | None
    model_cache_mount: str | None
    declared_peer: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "runtime_env": dict(self.runtime_env),
            "services": [
                {
                    "name": service.name,
                    "declared_image": service.declared_image,
                    "image_id": service.image_id,
                    "running": service.running,
                    "scale": service.scale,
                    "restart_policy": service.restart_policy,
                    "env": dict(service.env),
                    "command": service.command,
                    "restart_count": service.restart_count,
                }
                for service in self.services
            ],
            "compose_files": list(self.compose_files),
            "topology": self.topology.value,
            "photo_mode": self.photo_mode.value,
            "main_model": self.main_model,
            "vision_model": self.vision_model,
            "model_cache_mount": self.model_cache_mount,
            "declared_peer": self.declared_peer,
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> DeploymentSnapshot:
        try:
            return cls(
                runtime_env={
                    key: document["runtime_env"].get(key) for key in RUNTIME_ENV_KEYS
                },
                services=tuple(
                    ServiceInspection(
                        name=service["name"],
                        declared_image=service.get("declared_image"),
                        image_id=service.get("image_id"),
                        running=bool(service.get("running", False)),
                        scale=int(service.get("scale", 0)),
                        restart_policy=service.get("restart_policy"),
                        env=dict(service.get("env", {})),
                        command=service.get("command"),
                        restart_count=int(service.get("restart_count", 0)),
                    )
                    for service in document["services"]
                ),
                compose_files=tuple(document["compose_files"]),
                topology=Topology(document["topology"]),
                photo_mode=PhotoMode(document["photo_mode"]),
                main_model=document.get("main_model"),
                vision_model=document.get("vision_model"),
                model_cache_mount=document.get("model_cache_mount"),
                declared_peer=document.get("declared_peer"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DeploymentError(f"corrupt deployment snapshot: {exc}") from exc

    def service(self, name: str) -> ServiceInspection | None:
        for candidate in self.services:
            if candidate.name == name:
                return candidate
        return None

    def selection_summary(self) -> tuple[str | None, str | None]:
        return self.main_model, self.vision_model


def image_repository_of(reference: str) -> str:
    """The repository name of a declared image reference, tag and digest
    stripped.  Registry ports (`localhost:5000/repo:tag`) are preserved."""
    name = reference.strip().split("@", 1)[0]
    last_slash = name.rfind("/")
    last_colon = name.rfind(":")
    if last_colon > last_slash:
        name = name[:last_colon]
    return name


class ComposeAdapter(Protocol):
    """The Docker/Compose seam (real adapter shells out; tests inject fakes)."""

    def config(self, project_dir: str, files: tuple[str, ...]) -> dict[str, Any]: ...

    def running_services(
        self,
        project_dir: str,
        files: tuple[str, ...],
        services: tuple[str, ...] = MANAGED_SERVICES,
    ) -> tuple[ServiceInspection, ...]: ...

    def up(
        self,
        project_dir: str,
        files: tuple[str, ...],
        services: tuple[str, ...],
        *,
        scale: dict[str, int],
        no_deps: bool = False,
    ) -> None: ...

    def stop(self, project_dir: str, files: tuple[str, ...], services: tuple[str, ...]) -> None: ...

    def image_id(self, reference: str) -> str | None: ...


class DockerComposeAdapter:
    """The real adapter: `docker compose` + `docker inspect`, bounded timeouts,
    strictly allowlisted subcommands. Never runs caller-supplied commands."""

    def _compose(self, project_dir: str, files: tuple[str, ...], *args: str) -> str:
        command = ["docker", "compose"]
        for name in files:
            command += ["-f", name]
        command += list(args)
        try:
            result = subprocess.run(
                command,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=_DOCKER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise DeploymentError("docker compose timed out") from exc
        except OSError as exc:
            raise DeploymentError(f"docker compose failed: {exc}") from exc
        if result.returncode != 0:
            raise DeploymentError(
                "docker compose failed: " + (result.stderr or result.stdout)[-500:]
            )
        return result.stdout

    def config(self, project_dir: str, files: tuple[str, ...]) -> dict[str, Any]:
        output = self._compose(project_dir, files, "config", "--format", "json")
        try:
            document = json.loads(output)
        except json.JSONDecodeError as exc:
            raise DeploymentError("compose config is not valid JSON") from exc
        if not isinstance(document, dict):
            raise DeploymentError("compose config is not an object")
        return document

    def running_services(
        self,
        project_dir: str,
        files: tuple[str, ...],
        services: tuple[str, ...] = MANAGED_SERVICES,
    ) -> tuple[ServiceInspection, ...]:
        config = self.config(project_dir, files)
        document = config.get("services") or {}
        inspections: list[ServiceInspection] = []
        for name in sorted(document):
            if name not in services:
                continue
            service = document[name] or {}
            declared = service.get("image")
            command = service.get("command")
            if isinstance(command, list):
                command = " ".join(str(part) for part in command)
            # Env is scrubbed to the routing keys the engine consumes: a
            # credential must never reach a snapshot or a verifier.
            env = {
                str(key): str(value)
                for key, value in (service.get("environment") or {}).items()
                if str(key) in ROUTING_ENV_KEYS
            }
            running, image_id, restart_count, containers = self._running_identity(
                project_dir, name
            )
            inspections.append(
                ServiceInspection(
                    name=name,
                    declared_image=declared,
                    image_id=image_id,
                    running=running,
                    # The RUNNING scale (actual container count): the compose
                    # file's declared scale is 1 even when the operator scaled
                    # the service off, which would misreport photo routing.
                    scale=containers,
                    restart_policy=self._restart_policy(service),
                    env=env,
                    command=command,
                    restart_count=restart_count,
                )
            )
        return tuple(inspections)

    @staticmethod
    def _restart_policy(service: dict[str, Any]) -> str | None:
        deploy = service.get("deploy") or {}
        policy = (deploy.get("restart_policy") or {}).get("condition")
        if policy:
            return str(policy)
        restart = service.get("restart")
        return str(restart) if restart else None

    def _running_identity(
        self, project_dir: str, service: str
    ) -> tuple[bool, str | None, int, int]:
        listing = self._compose(project_dir, (), "ps", "-q", service)
        container_ids = [line.strip() for line in listing.splitlines() if line.strip()]
        if not container_ids:
            return False, None, 0, 0
        inspect_format = (
            "{{.State.Running}}|{{.Image}}|{{.RestartCount}}"
        )
        command = ["docker", "inspect", "--format", inspect_format, *container_ids]
        try:
            result = subprocess.run(
                command,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=_DOCKER_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise DeploymentError(f"docker inspect failed: {exc}") from exc
        if result.returncode != 0:
            raise DeploymentError("docker inspect failed")
        running, image_id, restarts = False, None, 0
        for line in result.stdout.splitlines():
            parts = line.strip().split("|")
            if len(parts) != 3:
                continue
            running = running or parts[0] == "true"
            image_id = image_id or (parts[1] or None)
            restarts = max(restarts, int(parts[2] or 0))
        return running, image_id, restarts, len(container_ids)

    def up(
        self,
        project_dir: str,
        files: tuple[str, ...],
        services: tuple[str, ...],
        *,
        scale: dict[str, int],
        no_deps: bool = False,
    ) -> None:
        args: list[str] = ["up", "-d"]
        if no_deps:
            args.append("--no-deps")
        for service, count in sorted(scale.items()):
            args += ["--scale", f"{service}={count}"]
        args += list(services)
        self._compose(project_dir, files, *args)

    def stop(
        self, project_dir: str, files: tuple[str, ...], services: tuple[str, ...]
    ) -> None:
        self._compose(project_dir, files, "stop", *services)

    def image_id(self, reference: str) -> str | None:
        # reference is validated by the caller (an inspected image string).
        if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.:/@-]*$", reference):
            raise DeploymentError("refusing to inspect a malformed image reference")
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.Id}}", reference],
                capture_output=True,
                text=True,
                timeout=_DOCKER_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise DeploymentError(f"docker inspect failed: {exc}") from exc
        if result.returncode != 0:
            return None
        image_id = result.stdout.strip()
        return image_id or None


def routing_services(
    compose: ComposeAdapter, project_dir: str, files: tuple[str, ...]
) -> tuple[ServiceInspection, ...]:
    """The managed runtime services PLUS the application api service the
    verifier reads routing env from. Snapshots/restore stay runtime-only."""
    return compose.running_services(project_dir, files, ROUTING_SERVICES)


# --- running-deployment value mapping ---------------------------------------------------


_VLLM_CMD_RE = re.compile(
    r"exec vllm serve '(?P<model>[^']+)'"
    r"(?:.*--tool-call-parser '(?P<tool>[^']+)')?"
    r"(?:.*--reasoning-parser '(?P<reasoning>[^']+)')?"
    r"(?:.*--gpu-memory-utilization '(?P<fraction>[^']+)')?"
    r"(?:.*--max-model-len '(?P<max_len>[^']+)')?"
    r"(?:.*--revision '(?P<revision>[^']+)')?"
    r"(?P<extra>.*)",
    re.DOTALL,
)
_VISION_CMD_RE = re.compile(
    r"--model '(?P<model>[^']+)'"
    r"(?:.*--revision '(?P<revision>[^']+)')?"
    r".*--gpu-memory-utilization '(?P<fraction>[^']+)'",
    re.DOTALL,
)


def runtime_values_from_running(services: tuple[ServiceInspection, ...]) -> dict[str, str | None]:
    """Map the RUNNING deployment back onto the runtime-key allowlist.

    Parsed structurally from the known command shapes this project writes --
    never free-form shell parsing of arbitrary commands.
    """
    values: dict[str, str | None] = {key: None for key in RUNTIME_ENV_KEYS}
    vllm = next((s for s in services if s.name == "vllm"), None)
    vision = next((s for s in services if s.name == "vllm-vision"), None)
    api = next((s for s in services if s.name == "api"), None)
    if vllm is not None and vllm.command:
        match = _VLLM_CMD_RE.search(vllm.command)
        if match:
            values["VLLM_MODEL"] = match.group("model")
            values["VLLM_TOOL_PARSER"] = match.group("tool") or None
            values["VLLM_REASONING_PARSER"] = match.group("reasoning") or None
            values["VLLM_GPU_FRACTION"] = match.group("fraction")
            values["VLLM_MAX_MODEL_LEN"] = match.group("max_len")
            values["VLLM_REVISION"] = match.group("revision") or None
            values["VLLM_EXTRA_ARGS"] = (match.group("extra") or "").strip() or None
    if vision is not None and vision.command:
        match = _VISION_CMD_RE.search(vision.command)
        if match:
            values["VLLM_VISION_MODEL"] = match.group("model")
            values["VLLM_VISION_REVISION"] = match.group("revision") or None
            values["VLLM_VISION_GPU_FRACTION"] = match.group("fraction")
    if vision is not None:
        values["FAMILY_CFO_AI_VISION_ENABLED"] = (
            "true" if vision.scale > 0 else "false"
        )
    if api is not None:
        env = api.env
        values["FAMILY_CFO_AI_SUPPORTS_VISION"] = env.get(
            "FAMILY_CFO_AI_SUPPORTS_VISION"
        )
        values["FAMILY_CFO_AI_VISION_MODEL"] = env.get("FAMILY_CFO_AI_VISION_MODEL")
    return values


def assert_no_drift(
    env_file: EnvFile, running_values: dict[str, str | None]
) -> None:
    """Unexplained drift between `.env` and the running deployment refuses.

    A key present in `.env` must agree with the running deployment: a
    different running value (an excluded writer or an unrecreated edit) or a
    running deployment that does not use the key at all (revision pins are
    the one legitimate ahead-of-deployment case) is drift.

    A key ABSENT from `.env` is never drift, whatever the running value:
    the compose files render their own `${VAR:-default}` fallback for an
    unset key, so that value is explained by the project's trusted compose
    inputs -- and after every photo-off swap the describer keys are absent
    by design (a swap must stay re-admissible).
    """
    env_values = env_file.runtime_values()
    problems: list[str] = []
    for key in RUNTIME_ENV_KEYS:
        # An empty .env value (the retired script wrote e.g. VLLM_EXTRA_ARGS=)
        # means "absent"; the running mapping reports None for the same state.
        in_env = env_values.get(key) or None
        in_running = running_values.get(key) or None
        if in_env is None:
            continue  # absent: the compose fallback explains the running value
        if in_running is None:
            # A key the file carries but the running deployment does not use:
            # only new profile keys (revision pins) are legitimately ahead of
            # an old deployment; the routing/fraction/model keys are not.
            if key not in ("VLLM_REVISION", "VLLM_VISION_REVISION"):
                problems.append(key)
        elif in_env != in_running:
            problems.append(key)
    if problems:
        raise DeploymentRefusal(
            ReasonCode.DEPLOYMENT_DRIFT,
            "the .env file disagrees with the running deployment for: "
            + ", ".join(sorted(problems)),
        )


# --- snapshot capture ----------------------------------------------------------------------


def compose_files_for(project_dir: str, topology: Topology) -> tuple[str, ...]:
    """Base files plus the cluster overlay for TP2 (ADR 0071)."""
    base = _files_from_env()
    if topology is Topology.CLUSTER:
        if not (Path(project_dir) / CLUSTER_OVERLAY).exists():
            raise DeploymentRefusal(
                ReasonCode.CLUSTER_PEER_UNREACHABLE,
                f"cluster serving requires the {CLUSTER_OVERLAY} overlay",
            )
        return base + (CLUSTER_OVERLAY,)
    return base


def _files_from_env() -> tuple[str, ...]:
    raw = os.environ.get("COMPOSE_FILES", "").strip()
    if not raw:
        return DEFAULT_COMPOSE_FILES
    return tuple(part for part in raw.split() if part)


def capture_snapshot(
    compose: ComposeAdapter,
    project_dir: str,
    env_file: EnvFile,
    *,
    topology: Topology,
    photo_mode: PhotoMode,
) -> DeploymentSnapshot:
    """Capture the working deployment. Callers verify readiness separately;
    capture records identity, not health."""
    files = compose_files_for(project_dir, topology)
    services = compose.running_services(project_dir, files)
    running_values = runtime_values_from_running(services)
    env_values = env_file.runtime_values()
    merged = {key: env_values.get(key) or running_values.get(key) for key in RUNTIME_ENV_KEYS}
    peer = merged.get("CLUSTER_PEER_HOST")
    cache_mount = None
    for service in services:
        if service.name == "vllm":
            cache_mount = service.env.get("HF_HOME") or "model_cache"
    return DeploymentSnapshot(
        runtime_env=merged,
        services=services,
        compose_files=files,
        topology=topology,
        photo_mode=photo_mode,
        main_model=merged.get("VLLM_MODEL"),
        vision_model=merged.get("VLLM_VISION_MODEL"),
        model_cache_mount=cache_mount,
        declared_peer=peer,
    )


# --- launch / restore ------------------------------------------------------------------------


def launch_values(
    plan: LaunchPlan,
    *,
    node_memory_gb: float | None,
    photo_node_memory_gb: float | None,
    profile: ProfilePolicy | None,
) -> dict[str, str | None]:
    """The runtime-key values the frozen plan installs (structure, never shell)."""
    from family_cfo_model_manager.planning import assigned_fraction

    requirements = {item.node: item for item in plan.node_requirements}
    main_requirement = next(iter(requirements.values()), None)
    fraction = None
    if node_memory_gb and main_requirement is not None:
        fraction = assigned_fraction(node_memory_gb, main_requirement.memory_gb)
        if fraction is not None and fraction > 0.92:
            fraction = None  # rounding invalidated the fit; refuse below
    vision_fraction = None
    if (
        plan.photo_mode is PhotoMode.SEPARATE
        and photo_node_memory_gb
        and plan.vision_demand_gb is not None
    ):
        vision_fraction = assigned_fraction(photo_node_memory_gb, plan.vision_demand_gb)
        if vision_fraction is not None and vision_fraction > 0.92:
            vision_fraction = None  # rounding invalidated the fit; refuse below

    values: dict[str, str | None] = {key: None for key in RUNTIME_ENV_KEYS}
    values["VLLM_MODEL"] = plan.main_model
    values["VLLM_TOOL_PARSER"] = plan.tool_call_parser
    values["VLLM_REASONING_PARSER"] = plan.reasoning_parser
    values["VLLM_MAX_MODEL_LEN"] = str(plan.context_limit)
    values["VLLM_REVISION"] = plan.main_revision
    values["FAMILY_CFO_AI_SUPPORTS_VISION"] = (
        "true" if plan.photo_mode is PhotoMode.MAIN else "false"
    )
    values["FAMILY_CFO_AI_VISION_ENABLED"] = (
        "true" if plan.photo_mode is PhotoMode.SEPARATE else "false"
    )
    if plan.photo_mode is PhotoMode.SEPARATE:
        values["VLLM_VISION_MODEL"] = plan.vision_model
        values["VLLM_VISION_REVISION"] = plan.vision_revision
        values["VLLM_VISION_GPU_FRACTION"] = (
            f"{vision_fraction:.2f}" if vision_fraction is not None else None
        )
    if fraction is not None:
        values["VLLM_GPU_FRACTION"] = f"{fraction:.2f}"
    extra = (profile.extra_args if profile is not None else None) or ""
    if plan.topology is Topology.CLUSTER:
        tp_args = "--tensor-parallel-size=2 --distributed-executor-backend=ray"
        extra = f"{tp_args} {extra}".strip()
    elif extra and "--tensor-parallel-size" in extra:
        # A single-node plan must never inherit a TP2 argument.
        extra = " ".join(
            part for part in extra.split() if not part.startswith("--tensor-parallel-size")
        )
    values["VLLM_EXTRA_ARGS"] = extra or None
    return values


def scale_for(plan: LaunchPlan) -> dict[str, int]:
    """Service scale implied by the photo mode: off/main stops the describer."""
    return {"vllm": 1, "vllm-vision": 1 if plan.photo_mode is PhotoMode.SEPARATE else 0}


def launch_target(
    compose: ComposeAdapter,
    project_dir: str,
    plan: LaunchPlan,
    files: tuple[str, ...],
) -> None:
    """Recreate ONLY the managed runtime services with frozen identities.

    The application `api`/`worker` are NOT recreated here -- routing flips
    only during post-commit finalization (ADR 0081 S3)."""
    compose.up(project_dir, files, MANAGED_SERVICES, scale=scale_for(plan))


def restore_runtime(
    compose: ComposeAdapter,
    project_dir: str,
    snapshot: DeploymentSnapshot,
    env_file: EnvFile,
    env_path: Path | str,
) -> None:
    """Restore the snapshot: runtime keys (including removals) + services.

    Application images are never pulled or upgraded; only the managed runtime
    services are recreated with their frozen image IDs."""
    updates = dict(snapshot.runtime_env)
    env_file.write_runtime_keys(env_path, updates)
    services = tuple(service.name for service in snapshot.services)
    scale = {service.name: service.scale for service in snapshot.services}
    compose.up(project_dir, snapshot.compose_files, services, scale=scale)


def empty_baseline_restore(
    compose: ComposeAdapter,
    project_dir: str,
    files: tuple[str, ...],
    added_keys: tuple[str, ...],
    env_file: EnvFile,
    env_path: Path | str,
) -> None:
    """First-deployment restore: the explicit off/empty baseline.

    Stop candidate-owned services, remove the runtime keys the job added, and
    leave the describer scaled off. There is no snapshot to re-verify; this
    cleanup IS the restore (reports failed/no_previous_runtime upstream)."""
    compose.stop(project_dir, files, MANAGED_SERVICES)
    env_file.write_runtime_keys(env_path, {key: None for key in added_keys})


# --- peer inspection ---------------------------------------------------------------------------


class PeerInspector(Protocol):
    def probe(self, peer_host: str, probe: str) -> str: ...


class SshPeerInspector:
    """Bounded SSH command allowlist over the enrolled peer.

    Host-key verified, batch mode (never prompts), read-only probes only; a
    timeout or parse failure rejects the topology (ADR 0081 S2)."""

    def __init__(self, *, connect_timeout: float = 10.0) -> None:
        self._connect_timeout = connect_timeout

    def probe(self, peer_host: str, probe: str) -> str:
        if probe not in PEER_PROBES:
            raise DeploymentRefusal(
                ReasonCode.HARDWARE_INCOMPATIBLE,
                "the requested peer probe is not on the read-only allowlist",
            )
        if not re.match(r"^[A-Za-z0-9_.-]+$", peer_host):
            raise DeploymentRefusal(
                ReasonCode.CLUSTER_PEER_UNREACHABLE,
                "the declared peer host is malformed; refusing to connect",
            )
        command = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"ConnectTimeout={int(self._connect_timeout)}",
            peer_host,
            PEER_PROBES[probe],
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=_PEER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise DeploymentRefusal(
                ReasonCode.CLUSTER_PEER_UNREACHABLE,
                f"the peer probe {probe!r} timed out",
            ) from exc
        except OSError as exc:
            raise DeploymentRefusal(
                ReasonCode.CLUSTER_PEER_UNREACHABLE,
                f"the peer probe {probe!r} could not run: {type(exc).__name__}",
            ) from exc
        if result.returncode != 0:
            raise DeploymentRefusal(
                ReasonCode.CLUSTER_PEER_UNREACHABLE,
                f"the peer probe {probe!r} failed (exit {result.returncode})",
            )
        return result.stdout


def parse_ray_status(output: str) -> dict[str, int]:
    """Structural parse of `ray status` node counts.

    Returns {"alive": n, "total": n}; any unexpected shape refuses the
    topology (parse failure -> reject, never assume).
    """
    alive = 0
    total = 0
    for line in output.splitlines():
        match = re.match(r"(\d+) node_(?:.*?)(?:ALIVE|DEAD)", line.strip())
        if match:
            total += 1
            if "ALIVE" in line:
                alive += 1
    if total == 0:
        # Fallback for the summary form: "2 node(s) alive".
        match = re.search(r"(\d+) node\(s\)", output)
        if match:
            total = alive = int(match.group(1))
    if total == 0:
        raise DeploymentRefusal(
            ReasonCode.CLUSTER_PEER_UNREACHABLE,
            "the peer's Ray status could not be parsed; refusing the topology",
        )
    return {"alive": alive, "total": total}


def parse_gpu_memory(output: str) -> tuple[tuple[int, int], ...]:
    """(used_mib, total_mib) per GPU; unparseable output refuses."""
    gpus: list[tuple[int, int]] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",") if part.strip()]
        if len(parts) != 2:
            continue
        try:
            gpus.append((int(parts[0]), int(parts[1])))
        except ValueError as exc:
            raise DeploymentRefusal(
                ReasonCode.CLUSTER_PEER_UNREACHABLE,
                "the peer's GPU memory report could not be parsed",
            ) from exc
    if not gpus:
        raise DeploymentRefusal(
            ReasonCode.CLUSTER_PEER_UNREACHABLE,
            "the peer reported no GPU memory; refusing the topology",
        )
    return tuple(gpus)


def shlex_join(command: list[str]) -> str:
    """Escape hatch for logging (never for execution): a safe rendering."""
    return shlex.join(command)

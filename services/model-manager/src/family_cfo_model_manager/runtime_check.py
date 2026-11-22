"""Installed-image compatibility checker (ADR 0081 S2, issue #170 Item 3).

The faithful gate for "can the installed runtime serve this checkpoint" is
constructing the installed release's effective vLLM ``ModelConfig`` -- not a
registry allowlist -- because with ``model_impl="auto"`` a Transformers-backend
fallback is attempted first and only ModelConfig construction exercises it.

How it runs: an ephemeral checker container from the EXACT installed immutable
image ID (never a mutable tag, never an implicit pull, never a runtime
upgrade), config-only -- no CUDA, no weights, no Docker socket, no privileged
mode; read-only root filesystem; bounded tmpfs; caps of 2 CPUs and 4 GiB (the
checker must not contend with the serving runtime for the box's unified
memory pool); a fixed checker entry point (no request-supplied Python or
shell); the candidate revision pinned; remote model code disabled unless a
reviewed profile explicitly permits it. Each checker subprocess is capped at
60 s, clipped to the remaining preflight budget (180 s total).

Version-specific adapters (0.24.x, 0.32.x, and one explicitly tested vendor
build) own the installed release's import surface and ModelConfig signature.
Any adapter absence, container failure, unparsable output, or timeout is a
FAIL-CLOSED refusal with an actionable reason -- never a static architecture
list, never a silent upgrade. Importable without FastAPI/Pydantic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from family_cfo_model_manager.contracts import Evidence, ReasonCode
from family_cfo_model_manager.planning import ProfilePolicy

# --- bounds (plan: Detailed design 2 / transport budget chain) -----------------
CHECKER_SUBPROCESS_CEILING_SECONDS = 60.0
PREFLIGHT_BUDGET_SECONDS = 180.0
CHECKER_CPUS = 2.0
CHECKER_MEMORY_GB = 4  # 4 GiB, docker --memory
CHECKER_TMPFS_MB = 256
CHECKER_OUTPUT_BYTES = 8192  # bounded stdout read from the checker

# Exact official vLLM runtime images by repository origin. A development or
# vendor build (e.g. the MiniMax image) is NOT matched by version guessing --
# it is approved only through an explicit, reviewed adapter binding below.
OFFICIAL_RUNTIME_IMAGE = "vllm/vllm-openai"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


class RuntimeCheckRefusal(Exception):
    """Fail-closed checker refusal with a stable reason code."""

    def __init__(self, reason_code: ReasonCode, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    """The bounded checker container contract. Fixed entry point; the only
    caller influence is the pinned model identity and structured options."""

    image_id: str
    entrypoint: tuple[str, ...]
    command: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    volumes: tuple[tuple[str, str, str], ...]  # (source, target, mode)
    read_only_rootfs: bool = True
    cpus: float = CHECKER_CPUS
    memory_gb: int = CHECKER_MEMORY_GB
    tmpfs_mb: int = CHECKER_TMPFS_MB
    network: str | None = "default"  # config.json fetch; never the Docker socket
    privileged: bool = False
    labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ContainerResult:
    exit_code: int
    stdout: str
    stderr: str


class ContainerRunner(Protocol):
    """The Docker seam. The real adapter shells out; tests inject fakes."""

    def run(self, spec: ContainerSpec, timeout: float) -> ContainerResult: ...


@dataclass(frozen=True, slots=True)
class CheckerContext:
    """One image/model compatibility check. image_id is the EXACT immutable
    installed image (what the container runs); image_repository is the origin
    identity (what the adapter is selected against); revision is pinned;
    options are the structured, relevant serve options from the frozen plan."""

    image_id: str
    runtime_version: str
    repo_id: str
    revision: str
    serve_options: dict[str, Any]
    profile: ProfilePolicy | None
    hf_offline: bool = False
    image_repository: str = OFFICIAL_RUNTIME_IMAGE


@dataclass(frozen=True, slots=True)
class CheckerVerdict:
    """What the installed runtime actually reports. ``backend`` distinguishes
    a native vLLM architecture from the Transformers-backend fallback."""

    supported: bool
    backend: str | None
    reason: str | None
    architectures: tuple[str, ...] = ()
    transformers_fallback: bool = False
    evidence: Evidence = Evidence.VERIFIED


def allow_remote_code(profile: ProfilePolicy | None) -> bool:
    """Remote model code runs ONLY when a reviewed profile explicitly says so."""
    return bool(profile is not None and profile.remote_code)


def clip_to_budget(seconds: float, remaining: float) -> float:
    """Individual checker subprocess ceiling, clipped to the remaining budget."""
    return max(0.0, min(CHECKER_SUBPROCESS_CEILING_SECONDS, remaining))


# --- version-specific adapters ----------------------------------------------------


class VLLM024Adapter:
    """vLLM 0.24.x: the installed release that rejected qwen4_exp (issue #170)."""

    versions = ("0.24",)
    # 0.24.x ships the tool/reasoning parsers under the OpenAI entrypoint
    # package; ModelConfig lives in vllm.config.
    tool_parser_module = "vllm.entrypoints.openai.tool_parsers"
    config_module = "vllm.config"

    def config_script(self, context: CheckerContext) -> str:
        return _config_script(context, self)


class VLLM032Adapter:
    """vLLM 0.32.x: the two-Spark verified release (transformers >=5.16 pin).

    The fallback gate is unchanged -- ModelConfig construction with
    model_impl="auto" -- so the adapter differs from 0.24.x only in its
    documented import surface, which the in-container probe exercises: any
    import failure there is a refusal, never an approval."""

    versions = ("0.32",)
    tool_parser_module = "vllm.entrypoints.openai.tool_parsers"
    config_module = "vllm.config"

    def config_script(self, context: CheckerContext) -> str:
        return _config_script(context, self)


class MiniMaxVendorAdapter:
    """The MiniMax development/vendor build. Selected ONLY through an exact
    image-repository binding -- semantic-version guessing must never approve a
    vendor image (explicitly tested adapter/profile, ADR 0081 S2)."""

    versions = ()
    tool_parser_module = "vllm.entrypoints.openai.tool_parsers"
    config_module = "vllm.config"
    image_repository = "minimax/minimax-m2"

    def config_script(self, context: CheckerContext) -> str:
        return _config_script(context, self)


# Reviewed vendor-build adapter bindings, by exact image repository suffix.
VENDOR_BUILD_ADAPTERS: dict[str, type[MiniMaxVendorAdapter]] = {
    MiniMaxVendorAdapter.image_repository: MiniMaxVendorAdapter
}

_OFFICIAL_ADAPTERS: tuple[type, ...] = (VLLM024Adapter, VLLM032Adapter)


def adapter_for(context: CheckerContext) -> Any:
    """The adapter for the installed release, or an actionable refusal.

    Vendor builds are approved only by an exact image-repository match in
    VENDOR_BUILD_ADAPTERS; official images are matched by parsed semantic
    version against the tested adapters. Anything else refuses.
    """
    repository = context.image_repository
    for prefix, adapter in VENDOR_BUILD_ADAPTERS.items():
        if repository.endswith(prefix):
            return adapter()
    if not repository.endswith(OFFICIAL_RUNTIME_IMAGE):
        raise RuntimeCheckRefusal(
            ReasonCode.RUNTIME_INCOMPATIBLE,
            f"the installed runtime image '{repository}' is a development or "
            "vendor build with no reviewed checker adapter; add an explicitly "
            "tested adapter/profile before applying models to it",
        )
    if _VERSION_RE.match(context.runtime_version.strip()) is None:
        raise RuntimeCheckRefusal(
            ReasonCode.RUNTIME_INCOMPATIBLE,
            f"the installed runtime version {context.runtime_version!r} could "
            "not be parsed, so no checker adapter can be selected",
        )
    for adapter in _OFFICIAL_ADAPTERS:
        if any(
            context.runtime_version.startswith(version + ".")
            for version in adapter.versions
        ):
            return adapter()
    supported = ", ".join(
        version + ".x"
        for candidate in _OFFICIAL_ADAPTERS
        for version in candidate.versions
    )
    raise RuntimeCheckRefusal(
        ReasonCode.RUNTIME_INCOMPATIBLE,
        f"the installed vLLM {context.runtime_version} has no checker adapter "
        f"(reviewed adapters cover {supported}); upgrade the runtime or add a "
        "reviewed adapter before applying this model",
    )


def _config_script(context: CheckerContext, adapter: Any) -> str:
    """The fixed checker entry point executed inside the checker container.

    No request-supplied Python or shell reaches the command line: the script
    is generated from the adapter + pinned identity only. It constructs the
    effective ModelConfig -- which is the automatic Transformers-backend
    fallback gate -- and validates parser availability. EVERY failure path
    prints a JSON verdict; only a supported verdict may ever approve.
    """
    options = context.serve_options
    trust_remote_code = "True" if allow_remote_code(context.profile) else "False"
    max_model_len = int(options.get("max_model_len") or 0) or "None"
    quantization = (
        repr(options.get("quantization")) if options.get("quantization") else "None"
    )
    dtype = repr(options.get("dtype") or "auto")
    tool_parser = repr(options.get("tool_call_parser"))
    reasoning_parser = repr(options.get("reasoning_parser"))
    offline = "1" if context.hf_offline else "0"
    return f"""
import json, os, sys
os.environ.setdefault("HF_HUB_OFFLINE", "{offline}")
result = {{"supported": False, "backend": None, "reason": None,
          "architectures": [], "transformers_fallback": False,
          "parser_available": False}}
def finish():
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()

try:
    from {adapter.config_module} import ModelConfig
    # Constructing ModelConfig fetches config.json ONLY -- no CUDA, no
    # weights. model_impl defaults to "auto", so a registry miss falls back
    # to the Transformers backend inside this construction: this call IS
    # the faithful gate, including the fallback.
    config = ModelConfig(
        model={context.repo_id!r},
        revision={context.revision!r},
        trust_remote_code={trust_remote_code},
        max_model_len={max_model_len},
        dtype={dtype},
        quantization={quantization},
    )
    result["supported"] = True
    result["architectures"] = list(getattr(config, "architectures", []) or [])
    # Classify the backend best-effort: architectures outside the registry
    # were served through the automatic Transformers-backend fallback.
    try:
        from vllm.model_executor.models.registry import ModelRegistry

        known = set(ModelRegistry.get_supported_archs())
        result["transformers_fallback"] = not set(result["architectures"]) <= known
        result["backend"] = (
            "transformers" if result["transformers_fallback"] else "vllm-native"
        )
    except Exception:  # noqa: BLE001 - classification only; the verdict stands
        result["backend"] = "vllm-native"
except ValueError as exc:
    # Unknown/removed architecture (both native and Transformers fallback
    # failed), or a bad quantization option. The tail keeps the message
    # bounded; the manager redacts further.
    result["reason"] = str(exc)[-2000:]
    finish()
    raise SystemExit(0)
except Exception as exc:  # noqa: BLE001 - ANY other failure must not approve
    result["reason"] = type(exc).__name__ + ": " + str(exc)[-1000:]
    finish()
    raise SystemExit(0)

try:
    from {adapter.tool_parser_module} import ToolParserManager
    for parser in ({tool_parser}, {reasoning_parser}):
        if parser:
            ToolParserManager.get_tool_parser(parser)
    result["parser_available"] = True
except Exception as exc:  # noqa: BLE001 - a missing parser is a refusal
    result["supported"] = False
    result["reason"] = "tool/reasoning parser unavailable: " + str(exc)[-500:]
finish()
"""


def _parse_verdict(stdout: str) -> CheckerVerdict | None:
    """Parse the checker's JSON verdict; None means unparsable (fail closed)."""
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        document = json.loads(stdout[start:])
    except (ValueError, TypeError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("supported"), bool):
        return None
    architectures = document.get("architectures")
    return CheckerVerdict(
        supported=bool(document["supported"]),
        backend=document.get("backend"),
        reason=document.get("reason"),
        architectures=tuple(architectures) if isinstance(architectures, list) else (),
        transformers_fallback=bool(document.get("transformers_fallback", False)),
        evidence=Evidence.VERIFIED,
    )


def safe_reason(reason: str | None) -> str | None:
    """Bound and strip a checker reason for a public diagnostic (<= 2 KiB)."""
    if not reason:
        return None
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(reason))
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", cleaned)
    return cleaned.strip()[:2048]


def check_model_compatibility(
    context: CheckerContext,
    runner: ContainerRunner,
    remaining_budget_seconds: float,
    *,
    labels: dict[str, str] | None = None,
) -> CheckerVerdict:
    """Run one checker container from the exact installed image ID.

    The subprocess ceiling is clipped to the remaining preflight budget. Any
    adapter absence, container failure, unparsable output, or timeout refuses
    fail-closed -- never approves.
    """
    adapter = adapter_for(context)  # raises RuntimeCheckRefusal when unsupported
    if remaining_budget_seconds <= 0:
        raise RuntimeCheckRefusal(
            ReasonCode.PREFLIGHT_UNAVAILABLE,
            "the preflight budget is exhausted before the compatibility "
            "checker could run",
        )
    timeout = clip_to_budget(CHECKER_SUBPROCESS_CEILING_SECONDS, remaining_budget_seconds)
    script = adapter.config_script(context)
    spec = ContainerSpec(
        image_id=context.image_id,
        entrypoint=("python", "-c"),  # fixed checker entry point
        command=(script,),
        env=(
            ("HF_HUB_OFFLINE", "1" if context.hf_offline else "0"),
            ("TRANSFORMERS_OFFLINE", "1" if context.hf_offline else "0"),
        ),
        volumes=(),
        labels=tuple((labels or {}).items()),
    )
    try:
        result = runner.run(spec, timeout)
    except RuntimeCheckRefusal:
        raise
    except Exception as exc:  # noqa: BLE001 - infrastructure failure, not approval
        raise RuntimeCheckRefusal(
            ReasonCode.PREFLIGHT_UNAVAILABLE,
            f"the compatibility checker could not run: {type(exc).__name__}",
        ) from exc
    if result.exit_code != 0:
        raise RuntimeCheckRefusal(
            ReasonCode.PREFLIGHT_UNAVAILABLE,
            "the compatibility checker container failed "
            f"(exit {result.exit_code}); the runtime could not be validated",
        )
    verdict = _parse_verdict(result.stdout)
    if verdict is None:
        raise RuntimeCheckRefusal(
            ReasonCode.PREFLIGHT_UNAVAILABLE,
            "the compatibility checker produced no parsable verdict; the "
            "runtime could not be validated",
        )
    if not verdict.supported:
        raise RuntimeCheckRefusal(
            ReasonCode.RUNTIME_INCOMPATIBLE,
            safe_reason(verdict.reason)
            or "the installed runtime rejected this model configuration",
        )
    return verdict

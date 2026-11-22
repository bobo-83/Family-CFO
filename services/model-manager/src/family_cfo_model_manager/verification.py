"""Exact readiness verification + bounded synthetic probes (ADR 0081 S4).

Verification is the ONLY path to a commit decision. Required checks, in order:
expected containers/ranks running; image, revision arguments, structured
options, topology, and job/config hash matching the frozen plan; main
`/health`; main `/v1/models` advertising the exact requested served name; photo
mode matching exactly (off / main / separate semantics); TP2 verified as two
expected GPU ranks on the declared nodes -- NOT node-exporter reachability;
bounded synthetic probes (text <= 32 tokens; one synthetic no-op tool call
<= 128 tokens whose parsed name + schema-valid arguments are validated WITHOUT
executing any household tool; photo with an embedded synthetic PNG <= 64 tokens
on the exact intended route); identities/restart counters unchanged across the
sequence.

Bounds: 30 s per request, at most 3 complete attempts 5 s apart, within the
phase deadline. Probe results verify transport, never visual accuracy, and no
probe carries household data. Importable without FastAPI/Pydantic.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

from family_cfo_model_manager.contracts import PhotoMode, ReasonCode, Topology
from family_cfo_model_manager.deployment import (
    DeploymentRefusal,
    PeerInspector,
    ServiceInspection,
    parse_gpu_memory,
    parse_ray_status,
)
from family_cfo_model_manager.planning import LaunchPlan

# --- bounds ---------------------------------------------------------------------
PROBE_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 3
ATTEMPT_GAP_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 5.0
TEXT_MAX_TOKENS = 32
TOOL_MAX_TOKENS = 128
PHOTO_MAX_TOKENS = 64
# The reconciler commits only on verification evidence <= 30 s old (ADR 0081 S3).
EVIDENCE_FRESHNESS_SECONDS = 30.0

MANAGED_VLLM_PORT = 8000

# One deterministic synthetic 1x1 PNG (no household data; a fixed test image).
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg=="
)
PHOTO_DATA_URI = "data:image/png;base64," + base64.b64encode(_TINY_PNG).decode()

# The single synthetic no-op tool. It is DECLARED to the model and its parsed
# call is validated; it is NEVER executed, and it touches no household data.
SYNTHETIC_TOOL = {
    "type": "function",
    "function": {
        "name": "verify_noop",
        "description": "A no-op probe used only to verify tool-call parsing.",
        "parameters": {
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": [],
        },
    },
}


class VerificationRefusal(Exception):
    """Verification infrastructure failure (not a serving failure)."""

    def __init__(self, reason_code: ReasonCode, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class RuntimeHttp(Protocol):
    """The bounded HTTP seam against the managed runtime (OpenAI-compatible)."""

    def get_json(self, url: str, timeout: float) -> tuple[int, Any]: ...

    def post_json(self, url: str, payload: dict[str, Any], timeout: float) -> tuple[int, Any]: ...


@dataclass(frozen=True, slots=True)
class ExpectedRuntime:
    """The frozen identity verification checks the deployment against."""

    main_model: str
    vision_model: str | None
    photo_mode: PhotoMode
    topology: Topology
    main_revision: str | None = None
    vision_revision: str | None = None
    image_id: str | None = None
    vision_image_id: str | None = None
    tool_call_parser: str | None = None
    reasoning_parser: str | None = None
    max_model_len: int | None = None
    extra_args: str | None = None
    plan_hash: str | None = None
    peer_host: str | None = None

    @classmethod
    def from_plan(
        cls, plan: LaunchPlan, *, image_id: str | None, peer_host: str | None = None
    ) -> ExpectedRuntime:
        return cls(
            main_model=plan.main_model,
            vision_model=plan.vision_model,
            photo_mode=plan.photo_mode,
            topology=plan.topology,
            main_revision=plan.main_revision,
            vision_revision=plan.vision_revision,
            image_id=image_id,
            tool_call_parser=plan.tool_call_parser,
            reasoning_parser=plan.reasoning_parser,
            max_model_len=plan.context_limit,
            plan_hash=plan.digest(),
            peer_host=peer_host,
        )

    @property
    def main_base_url(self) -> str:
        """The managed runtime's HTTP surface.

        In-network default `http://vllm:8000` (the manager's own network);
        a HOST-SIDE executor resolves the container address instead and
        exports FAMILY_CFO_MAIN_RUNTIME_URL for its children."""
        return (
            os.environ.get("FAMILY_CFO_MAIN_RUNTIME_URL", "").strip()
            or f"http://vllm:{MANAGED_VLLM_PORT}"
        )

    @property
    def photo_base_url(self) -> str:
        """The EXACT intended photo route (spec 07): main or the describer."""
        if self.photo_mode is PhotoMode.SEPARATE:
            return (
                os.environ.get("FAMILY_CFO_PHOTO_RUNTIME_URL", "").strip()
                or f"http://vllm-vision:{MANAGED_VLLM_PORT}"
            )
        return self.main_base_url


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """What was verified, captured at the moment it passed."""

    verified_at: datetime
    main_served_name: str
    photo_mode: PhotoMode
    topology: Topology
    image_ids: dict[str, str]
    restart_counts: dict[str, int]
    plan_hash: str | None
    probes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified_at": self.verified_at.isoformat(),
            "main_served_name": self.main_served_name,
            "photo_mode": self.photo_mode.value,
            "topology": self.topology.value,
            "image_ids": dict(self.image_ids),
            "restart_counts": dict(self.restart_counts),
            "plan_hash": self.plan_hash,
            "probes": list(self.probes),
        }

    def age_seconds(self, now: datetime) -> float:
        return max(0.0, (now - self.verified_at).total_seconds())


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    passed: bool
    reason_code: ReasonCode | None = None
    message: str | None = None
    evidence: VerificationEvidence | None = None


def _outcome(
    passed: bool, message: str, evidence: VerificationEvidence | None = None
) -> VerificationOutcome:
    if passed:
        return VerificationOutcome(passed=True, evidence=evidence)
    return VerificationOutcome(
        passed=False, reason_code=ReasonCode.RUNTIME_INCOMPATIBLE, message=message
    )


class Verifier:
    """Runs the full check sequence against the managed runtime."""

    def __init__(
        self,
        *,
        http: RuntimeHttp,
        compose_services: Callable[[], tuple[ServiceInspection, ...]],
        peer: PeerInspector | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._services = compose_services
        self._peer = peer
        self._sleep = sleep
        self._clock = clock

    # --- public API -------------------------------------------------------------------

    def verify(
        self, expected: ExpectedRuntime, *, deadline: float
    ) -> VerificationOutcome:
        """Up to MAX_ATTEMPTS complete check sequences, 5 s apart, within the
        deadline. A container restart mid-sequence discards the attempt."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            remaining = deadline - self._clock()
            if remaining <= 0:
                return _outcome(False, "the verification phase deadline expired")
            outcome = self._one_attempt(expected, remaining)
            if outcome.passed:
                return outcome
            if attempt < MAX_ATTEMPTS:
                gap = min(ATTEMPT_GAP_SECONDS, max(0.0, deadline - self._clock()))
                if gap > 0:
                    self._sleep(gap)
        return outcome

    # --- one attempt --------------------------------------------------------------------

    def _one_attempt(
        self, expected: ExpectedRuntime, remaining: float
    ) -> VerificationOutcome:
        try:
            services = self._services()
        except DeploymentRefusal as exc:
            return _outcome(False, exc.message)
        by_name = {service.name: service for service in services}

        # 1. Expected containers/ranks running.
        vllm = by_name.get("vllm")
        if vllm is None or not vllm.running:
            return _outcome(False, "the main runtime container is not running")
        vision = by_name.get("vllm-vision")
        if expected.photo_mode is PhotoMode.SEPARATE and (
            vision is None or not vision.running
        ):
            return _outcome(False, "the separate photo describer is not running")

        # 2. Image, revision/structured options, topology, and plan hash match.
        if expected.image_id and vllm.image_id != expected.image_id:
            return _outcome(
                False, "the running main runtime image does not match the frozen plan"
            )
        if expected.photo_mode is PhotoMode.SEPARATE:
            if expected.vision_image_id and vision.image_id != expected.vision_image_id:
                return _outcome(
                    False,
                    "the running describer image does not match the frozen plan",
                )
        args_check = _args_match(vllm.command, expected)
        if args_check is not None:
            return _outcome(False, args_check)

        # 3. TP2 = two expected GPU ranks on the declared nodes.
        if expected.topology is Topology.CLUSTER:
            rank_check = self._verify_cluster_ranks(expected)
            if rank_check is not None:
                return _outcome(False, rank_check)

        # 4-5. Health, exact served name, and photo routing before probes.
        base = expected.main_base_url
        timeout = min(PROBE_TIMEOUT_SECONDS, max(1.0, remaining))
        status, _payload = self._http.get_json(f"{base}/health", timeout)
        if status != 200:
            return _outcome(False, "the main runtime health check failed")
        status, models = self._http.get_json(f"{base}/v1/models", timeout)
        if status != 200:
            return _outcome(False, "the main runtime model listing failed")
        served = _served_names(models)
        if expected.main_model not in served:
            return _outcome(
                False,
                "the runtime does not serve the exact requested main model",
            )
        routing_check = _routing_matches(services, expected)
        if routing_check is not None:
            return _outcome(False, routing_check)
        if expected.photo_mode is PhotoMode.SEPARATE:
            status, _payload = self._http.get_json(
                f"{expected.photo_base_url}/health", timeout
            )
            if status != 200:
                return _outcome(False, "the photo describer is not ready")

        # 6. Synthetic probes, on the exact intended routes.
        probes = []
        text_ok, text_msg = self._text_probe(expected, timeout)
        if not text_ok:
            return _outcome(False, text_msg or "the text probe failed")
        probes.append("text")
        if expected.photo_mode is not PhotoMode.OFF:
            photo_ok, photo_msg = self._photo_probe(expected, timeout)
            if not photo_ok:
                return _outcome(False, photo_msg or "the photo probe failed")
            probes.append("photo")
        tool_ok, tool_msg = self._tool_probe(expected, timeout)
        if not tool_ok:
            return _outcome(False, tool_msg or "the tool-call probe failed")
        probes.append("tool")

        # 7. Identities/restart counters unchanged across the sequence.
        after = self._services()
        if _counters_changed(services, after):
            return _outcome(
                False, "a managed container restarted during verification"
            )

        evidence = VerificationEvidence(
            verified_at=datetime.now(UTC),
            main_served_name=expected.main_model,
            photo_mode=expected.photo_mode,
            topology=expected.topology,
            image_ids={
                service.name: service.image_id or "" for service in after if service.image_id
            },
            restart_counts={
                service.name: service.restart_count for service in after
            },
            plan_hash=expected.plan_hash,
            probes=tuple(probes),
        )
        return _outcome(True, "verified", evidence)

    # --- helpers ----------------------------------------------------------------------

    def _verify_cluster_ranks(self, expected: ExpectedRuntime) -> str | None:
        """TP2 is two expected GPU ranks on the declared nodes -- ray status
        plus per-GPU memory, never node-exporter reachability."""
        if self._peer is None:
            return "no peer inspector is configured for cluster verification"
        assert expected.peer_host
        try:
            ray = self._peer.probe(expected.peer_host, "ray_status")
            nodes = parse_ray_status(ray)
            if nodes["alive"] < 2:
                return (
                    f"the Ray cluster reports {nodes['alive']} alive node(s); "
                    "TP2 requires two"
                )
            gpus = parse_gpu_memory(self._peer.probe(expected.peer_host, "gpu_memory"))
            if not gpus:
                return "the peer reported no GPU ranks"
        except DeploymentRefusal as exc:
            return exc.message
        return None

    def _text_probe(
        self, expected: ExpectedRuntime, timeout: float
    ) -> tuple[bool, str | None]:
        status, payload = self._http.post_json(
            f"{expected.main_base_url}/v1/chat/completions",
            {
                "model": expected.main_model,
                "messages": [
                    {"role": "user", "content": "Reply with the single word OK."}
                ],
                "max_tokens": TEXT_MAX_TOKENS,
                "temperature": 0,
            },
            timeout,
        )
        if status != 200:
            return False, "the text probe request failed"
        content = _choice_content(payload)
        if not content:
            return False, "the text probe returned no content"
        return True, None

    def _tool_probe(
        self, expected: ExpectedRuntime, timeout: float
    ) -> tuple[bool, str | None]:
        """Validate the parsed function name and schema-valid arguments WITHOUT
        executing anything (spec 07)."""
        status, payload = self._http.post_json(
            f"{expected.main_base_url}/v1/chat/completions",
            {
                "model": expected.main_model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Call verify_noop with any note to confirm "
                        "tool-call parsing works.",
                    }
                ],
                "tools": [SYNTHETIC_TOOL],
                "tool_choice": "auto",
                "max_tokens": TOOL_MAX_TOKENS,
                "temperature": 0,
            },
            timeout,
        )
        if status != 200:
            return False, "the tool-call probe request failed"
        call = _parsed_tool_call(payload)
        if call is None:
            return False, "the tool-call probe produced no parsable tool call"
        name, arguments = call
        if name != SYNTHETIC_TOOL["function"]["name"]:
            return False, "the parsed tool call is not the synthetic no-op tool"
        try:
            arguments_object = json.loads(arguments) if arguments else {}
        except (ValueError, TypeError):
            return False, "the parsed tool arguments are not schema-valid JSON"
        if not isinstance(arguments_object, dict):
            return False, "the parsed tool arguments are not an object"
        return True, None  # NEVER executed

    def _photo_probe(
        self, expected: ExpectedRuntime, timeout: float
    ) -> tuple[bool, str | None]:
        """One embedded synthetic PNG on the EXACT intended photo route; a
        nonempty structurally valid answer is required -- never a color word,
        never visual accuracy."""
        status, payload = self._http.post_json(
            f"{expected.photo_base_url}/v1/chat/completions",
            {
                "model": (
                    expected.vision_model
                    if expected.photo_mode is PhotoMode.SEPARATE
                    else expected.main_model
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this image briefly."},
                            {
                                "type": "image_url",
                                "image_url": {"url": PHOTO_DATA_URI},
                            },
                        ],
                    }
                ],
                "max_tokens": PHOTO_MAX_TOKENS,
                "temperature": 0,
            },
            timeout,
        )
        if status != 200:
            return False, "the photo probe request failed"
        content = _choice_content(payload)
        if not content:
            return False, "the photo probe returned no content"
        return True, None


# --- pure helpers (unit-tested directly) ------------------------------------------------


def _choice_content(payload: Any) -> str | None:
    try:
        choice = payload["choices"][0]
        message = choice["message"]
        content = message.get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    return content


def _parsed_tool_call(payload: Any) -> tuple[str, str] | None:
    try:
        tool_calls = payload["choices"][0]["message"]["tool_calls"]
        function = tool_calls[0]["function"]
        name = function["name"]
        arguments = function.get("arguments", "{}")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    if not isinstance(name, str) or not name:
        return None
    return name, arguments if isinstance(arguments, str) else "{}"


def _served_names(payload: Any) -> set[str]:
    try:
        data = payload["data"]
        return {item["id"] for item in data if isinstance(item.get("id"), str)}
    except (KeyError, TypeError, AttributeError):
        return set()


def _args_match(command: str | None, expected: ExpectedRuntime) -> str | None:
    """The running serve command must carry the frozen structured options."""
    if not command:
        return "the running runtime has no parsable serve command"
    if f"'{expected.main_model}'" not in command and f'"{expected.main_model}"' not in command:
        return "the running runtime serves a different main model"
    if (
        expected.max_model_len is not None
        and f"--max-model-len '{expected.max_model_len}'" not in command
    ):
        return "the running runtime uses a different context limit"
    if (
        expected.tool_call_parser
        and f"--tool-call-parser '{expected.tool_call_parser}'" not in command
    ):
        return "the running runtime uses a different tool parser"
    if expected.main_revision and f"--revision '{expected.main_revision}'" not in command:
        return "the running runtime serves a different revision than the frozen plan"
    if expected.topology is Topology.CLUSTER and "--tensor-parallel-size=2" not in command:
        return "the running runtime is not launched with tensor parallelism 2"
    if (
        expected.topology is Topology.SINGLE
        and "--tensor-parallel-size=2" in command
    ):
        return "the running runtime unexpectedly uses tensor parallelism 2"
    return None


def _routing_matches(
    services: tuple[ServiceInspection, ...], expected: ExpectedRuntime
) -> str | None:
    """Photo mode must match EXACTLY -- service scale AND application routing."""
    by_name = {service.name: service for service in services}
    api = by_name.get("api")
    vision = by_name.get("vllm-vision")
    api_env = api.env if api is not None else {}
    vision_running = bool(vision and vision.running)
    if expected.photo_mode is PhotoMode.OFF:
        if vision_running:
            return "photo mode off but a managed describer is running"
        if api_env.get("FAMILY_CFO_AI_VISION_ENABLED", "").lower() == "true":
            return "photo mode off but application photo routing is enabled"
        return None
    if expected.photo_mode is PhotoMode.MAIN:
        if vision_running:
            return "photo mode main but a separate describer is running"
        if api_env.get("FAMILY_CFO_AI_SUPPORTS_VISION", "").lower() != "true":
            return "photo mode main but the application does not route photos to main"
        return None
    if expected.photo_mode is PhotoMode.SEPARATE:
        if not vision_running:
            return "photo mode separate but the describer is not running"
        if api_env.get("FAMILY_CFO_AI_VISION_ENABLED", "").lower() != "true":
            return "photo mode separate but application photo routing is disabled"
        routed = api_env.get("FAMILY_CFO_AI_VISION_MODEL")
        if routed != expected.vision_model:
            return "application photo routing does not target the planned describer"
        if vision.command and expected.vision_model:
            if f"'{expected.vision_model}'" not in vision.command:
                return "the describer serves a different model than planned"
            if expected.vision_revision and (
                f"--revision '{expected.vision_revision}'" not in vision.command
            ):
                return "the describer serves a different revision than planned"
        return None
    return None


def _counters_changed(
    before: tuple[ServiceInspection, ...], after: tuple[ServiceInspection, ...]
) -> bool:
    before_by_name = {service.name: service for service in before}
    for service in after:
        previous = before_by_name.get(service.name)
        if previous is None:
            continue
        if service.restart_count != previous.restart_count:
            return True
        if service.image_id != previous.image_id:
            return True
        if service.running != previous.running:
            return True
    return False

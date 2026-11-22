"""Shared fakes for the model-manager suite (issue #170 Item 3).

No test pulls a vLLM image or talks to a real Hub: the checker/probe seams are
injected. python:3.12-slim remains reserved for the real-flock gate in
scripts/tests, which needs no vLLM image either.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from family_cfo_model_manager import deployment
from family_cfo_model_manager import executor as executor_module
from family_cfo_model_manager import runtime_check
from family_cfo_model_manager.contracts import JobDecision, JobRecord, JobState, JobOrigin
from family_cfo_model_manager.journal import Journal
from family_cfo_model_manager.planning import ProfilesDocument

MAIN = "Qwen/Qwen2.5-14B-Instruct"
VISION = "Qwen/Qwen2.5-VL-3B-Instruct"
REQUEST_ID = "0f0e1d2c-3b4a-5968-7789-abcdef012345"
JOB_ID = "1a1b1c1d-1e1f-2a2b-3c3d-4e4f5a5b5c5d"
IMAGE_ID = "sha256:0000000000000000000000000000000000000000000000000000000000000000"

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)


class FakeCompose:
    """Configurable Compose/Docker adapter."""

    def __init__(
        self,
        services: tuple[deployment.ServiceInspection, ...] = (),
        config: dict[str, Any] | None = None,
        image_id: str | None = IMAGE_ID,
        up_error: Exception | None = None,
    ) -> None:
        self.services = services
        self.config_document = config or {
            "services": {
                "vllm": {
                    "image": "vllm/vllm-openai:latest",
                    "volumes": ["model_cache:/root/.cache/huggingface"],
                }
            }
        }
        self.canned_image_id = image_id
        self.up_error = up_error
        self.up_calls: list[tuple[tuple[str, ...], dict[str, int], bool]] = []
        self.stop_calls: list[tuple[str, ...]] = []

    def config(self, project_dir: str, files: tuple[str, ...]) -> dict[str, Any]:
        return self.config_document

    def running_services(
        self,
        project_dir: str,
        files: tuple[str, ...],
        services: tuple[str, ...] = deployment.MANAGED_SERVICES,
    ) -> tuple[deployment.ServiceInspection, ...]:
        return self.services

    def up(
        self,
        project_dir: str,
        files: tuple[str, ...],
        services: tuple[str, ...],
        *,
        scale: dict[str, int],
        no_deps: bool = False,
    ) -> None:
        self.up_calls.append((services, dict(scale), no_deps))
        if self.up_error is not None:
            raise self.up_error

    def stop(
        self, project_dir: str, files: tuple[str, ...], services: tuple[str, ...]
    ) -> None:
        self.stop_calls.append(services)

    def image_id(self, reference: str) -> str | None:
        return self.canned_image_id


class FakeRunner:
    """Canned checker/staging container results, recorded for assertions."""

    def __init__(self, results: list[runtime_check.ContainerResult] | None = None) -> None:
        self.results = list(results or [])
        self.specs: list[runtime_check.ContainerSpec] = []

    def run(self, spec: runtime_check.ContainerSpec, timeout: float) -> runtime_check.ContainerResult:
        self.specs.append(spec)
        if self.results:
            return self.results.pop(0)
        raise runtime_check.RuntimeCheckRefusal(
            runtime_check.ReasonCode.PREFLIGHT_UNAVAILABLE, "no staged container result"
        )


class FakeHttp:
    """Canned runtime HTTP responses; records the requests it saw.

    An optional handler inspects each request first (so the text/tool/photo
    probes can share one endpoint), falling back to the keyed table."""

    def __init__(
        self,
        responses: dict[str, tuple[int, Any]] | None = None,
        handler: Any = None,
    ) -> None:
        self.responses = responses or {}
        self.handler = handler
        self.calls: list[tuple[str, str, Any]] = []

    def get_json(self, url: str, timeout: float) -> tuple[int, Any]:
        self.calls.append(("GET", url, None))
        if self.handler is not None:
            handled = self.handler("GET", url, None)
            if handled is not None:
                return handled
        entry = self.responses.get(url, (0, None))
        return entry[0], entry[1]

    def post_json(self, url: str, payload: dict[str, Any], timeout: float) -> tuple[int, Any]:
        self.calls.append(("POST", url, payload))
        if self.handler is not None:
            handled = self.handler("POST", url, payload)
            if handled is not None:
                return handled
        entry = self.responses.get(url, (0, None))
        return entry[0], entry[1]


class FakePeer:
    def __init__(self, outputs: dict[str, str] | None = None) -> None:
        self.outputs = outputs or {}
        self.probes: list[tuple[str, str]] = []

    def probe(self, peer_host: str, probe: str) -> str:
        self.probes.append((peer_host, probe))
        if probe not in self.outputs:
            raise deployment.DeploymentRefusal(
                deployment.ReasonCode.CLUSTER_PEER_UNREACHABLE,
                f"no canned output for probe {probe!r}",
            )
        return self.outputs[probe]


def make_profiles(**overrides) -> ProfilesDocument:
    from family_cfo_model_manager.planning import ProfilePolicy

    profile = ProfilePolicy(repo_id=MAIN, min_nodes=1)
    return ProfilesDocument(profiles=(profile,))


def _default_runner() -> FakeRunner:
    """A version probe + a supported checker verdict (the happy default)."""
    import json as jsonlib

    return FakeRunner(
        results=[
            runtime_check.ContainerResult(
                exit_code=0,
                stdout=jsonlib.dumps({"version": "0.32.0"}),
                stderr="",
            ),
            runtime_check.ContainerResult(
                exit_code=0,
                stdout=jsonlib.dumps(
                    {
                        "supported": True,
                        "backend": "vllm-native",
                        "architectures": ["Qwen2ForCausalLM"],
                    }
                ),
                stderr="",
            ),
        ]
    )


def verify_ok_http() -> FakeHttp:
    """A FakeHttp whose handler answers every verifier probe positively.

    Health, the exact served names, text and photo chat probes - the shape
    the verifier requires, never executed beyond the parse."""

    def handler(method: str, url: str, payload: Any) -> tuple[int, Any] | None:
        if url.endswith("/health"):
            return (200, {"status": "ok"})
        if url.endswith("/v1/models"):
            return (
                200,
                {
                    "data": [
                        {"id": model}
                        for model in (MAIN, VISION, "Qwen/Qwen2.5-7B-Instruct")
                    ]
                },
            )
        if url.endswith("/v1/chat/completions"):
            message: dict[str, Any] = {"role": "assistant", "content": "OK"}
            if isinstance(payload, dict) and payload.get("tools"):
                # The synthetic no-op tool, parsed but NEVER executed.
                message["content"] = None
                message["tool_calls"] = [
                    {
                        "id": "probe-1",
                        "type": "function",
                        "function": {
                            "name": "verify_noop",
                            "arguments": "{\"note\": \"probe\"}",
                        },
                    }
                ]
            return (200, {"choices": [{"message": message, "finish_reason": "stop"}]})
        return None

    return FakeHttp(handler=handler)


def previous_runtime_services(
    *,
    main_model: str = "Qwen/Qwen2.5-7B-Instruct",
    vision_model: str | None = VISION,
) -> tuple[deployment.ServiceInspection, ...]:
    """A running previous deployment, exactly as the manager would find it.

    Command shapes match the structural regexes in deployment.py so the
    baseline capture reads the selection back."""
    services = [
        service(
            "vllm",
            running=True,
            image_id=IMAGE_ID,
            command=(
                f"exec vllm serve '{main_model}' "
                "--revision '8b5e6c1f' "
                "--tool-call-parser 'hermes' "
                "--gpu-memory-utilization '0.85' --max-model-len '8192'"
            ),
        )
    ]
    if vision_model is not None:
        services.append(
            service(
                "vllm-vision",
                running=True,
                image_id=IMAGE_ID,
                command=(
                    "exec vllm serve "
                    f"--model '{vision_model}' "
                    "--revision '8b5e6c1f' "
                    "--gpu-memory-utilization '0.20'"
                ),
            )
        )
    # The compose file maps FAMILY_CFO_AI_MODEL from VLLM_MODEL, so the
    # running api's inspected env carries the active main model.
    api_env = {
        "FAMILY_CFO_AI_MODEL": main_model,
        "FAMILY_CFO_AI_SUPPORTS_VISION": "true" if vision_model else "false",
    }
    if vision_model is not None:
        api_env["FAMILY_CFO_AI_VISION_ENABLED"] = "true"
        api_env["FAMILY_CFO_AI_VISION_MODEL"] = vision_model
    services.append(
        service("api", running=True, image_id=IMAGE_ID, env=api_env)
    )
    return tuple(services)


def write_previous_runtime_env(
    project_dir: Path,
    *,
    main_model: str = "Qwen/Qwen2.5-7B-Instruct",
    vision_model: str | None = VISION,
) -> None:
    """A synthetic .env that AGREES with previous_runtime_services().

    A real deployment keeps the env file and the running containers in sync;
    the drift check must see that here too."""
    lines = [
        "SYNTHETIC_FIXTURE=1",
        f"VLLM_MODEL={main_model}",
        "VLLM_REVISION=8b5e6c1f",
        "VLLM_TOOL_PARSER=hermes",
        "VLLM_GPU_FRACTION=0.85",
        "VLLM_MAX_MODEL_LEN=8192",
        "FAMILY_CFO_AI_SUPPORTS_VISION=" + ("true" if vision_model else "false"),
    ]
    if vision_model is not None:
        lines += [
            f"VLLM_VISION_MODEL={vision_model}",
            "VLLM_VISION_REVISION=8b5e6c1f",
            "VLLM_VISION_GPU_FRACTION=0.20",
            "FAMILY_CFO_AI_VISION_ENABLED=true",
            "FAMILY_CFO_AI_SUPPORTS_VISION=true",
        ]
    (project_dir / ".env").write_text("\n".join(lines) + "\n")


class DualRunner:
    """Answers checker probes from a canned list and succeeds every staging
    container (labeled family.cfo.apply=staging). Records all specs."""

    def __init__(self, checker_results: list[runtime_check.ContainerResult] | None = None) -> None:
        self.checker_results = list(
            checker_results
            if checker_results is not None
            else _default_runner().results
        )
        self.specs: list[runtime_check.ContainerSpec] = []

    def run(self, spec: runtime_check.ContainerSpec, timeout: float) -> runtime_check.ContainerResult:
        self.specs.append(spec)
        labels = dict(spec.labels or ())
        if labels.get("family.cfo.apply") == "staging":
            return runtime_check.ContainerResult(exit_code=0, stdout="{}", stderr="")
        if self.checker_results:
            return self.checker_results.pop(0)
        raise runtime_check.RuntimeCheckRefusal(
            runtime_check.ReasonCode.PREFLIGHT_UNAVAILABLE, "no staged container result"
        )


def rearm_runner(deps) -> None:
    """Re-arm the canned checker results (one admission consumes them)."""
    if isinstance(deps.runner, FakeRunner):
        deps.runner.results = list(_default_runner().results)


def make_deps(
    tmp_path: Path,
    *,
    compose: FakeCompose | None = None,
    runner: FakeRunner | None = None,
    http: FakeHttp | None = None,
    peer: FakePeer | None = None,
    journal: Journal | None = None,
    profiles: Any = None,
    hardware: Any = None,
    metadata: Any = None,
    cache_probe: Any = None,
    spawn: Any = None,
) -> executor_module.ExecutorDeps:
    from family_cfo_model_manager.contracts import Evidence
    from family_cfo_model_manager.planning import ModelFacts, NodeHardware

    compose = compose or FakeCompose()
    runner = runner or _default_runner()
    http = http or FakeHttp()

    class StaticMetadata:
        def __init__(self) -> None:
            self._facts: dict[str, ModelFacts] = {}

        def set(self, repo_id: str, facts: ModelFacts) -> None:
            self._facts[repo_id] = facts

        def facts_for(self, repo_id: str) -> ModelFacts:
            if repo_id in self._facts:
                return self._facts[repo_id]
            multimodal = "vl" in repo_id.lower()
            return ModelFacts(
                repo_id=repo_id,
                revision="8b5e6c1f",
                architecture=(
                    "Qwen2VLForConditionalGeneration"
                    if multimodal
                    else "Qwen2ForCausalLM"
                ),
                resident_weight_bytes=8_000_000_000,
                multimodal=multimodal,
                model_context_limit=32768,
                kv_bytes_per_token=1_000_000,
                evidence=Evidence.VERIFIED,
            )

    class StaticHardware:
        def nodes(self):
            return (
                NodeHardware(
                    node="primary",
                    memory_gb=64.0,
                    cache_filesystem_gb=500.0,
                    disk_free_gb=200.0,
                    resident_managed_gb=None,
                    evidence=Evidence.VERIFIED,
                ),
            )

    metadata = metadata or StaticMetadata()
    hardware = hardware or StaticHardware()
    cache_probe = cache_probe or (lambda repo, revision: 0)
    return executor_module.ExecutorDeps(
        journal=journal or Journal(tmp_path / ".runtime-swap" / "v2"),
        compose=compose,
        http=http,
        runner=runner,
        profiles=profiles or make_profiles(),
        metadata=metadata,
        hardware=hardware,
        peer=peer,
        project_dir=str(tmp_path),
        cache_probe=cache_probe,
        clock=lambda: 0.0,
        sleep=lambda seconds: None,
        spawn=spawn,
    )


def service(
    name: str,
    *,
    running: bool = True,
    image_id: str | None = IMAGE_ID,
    scale: int = 1,
    env: dict[str, str] | None = None,
    command: str | None = None,
    restart_count: int = 0,
) -> deployment.ServiceInspection:
    return deployment.ServiceInspection(
        name=name,
        declared_image="vllm/vllm-openai:latest",
        image_id=image_id,
        running=running,
        scale=scale,
        restart_policy="unless-stopped",
        env=env or {},
        command=command,
        restart_count=restart_count,
    )


def record(**overrides) -> JobRecord:
    fields = {
        "schema_version": 2,
        "job_id": JOB_ID,
        "request_id": REQUEST_ID,
        "sequence": 1,
        "origin": JobOrigin.API,
        "runtime_key": "vllm+http://api:8000/v1",
        "request_hash": "ab" * 32,
        "state": JobState.ACCEPTED,
        "decision": JobDecision.UNDECIDED,
        "created_at": NOW,
        "updated_at": NOW,
    }
    fields.update(overrides)
    return JobRecord(**fields)


@pytest.fixture
def env_project(tmp_path):
    """A project dir with a synthetic .env (no real deployment anywhere)."""
    (tmp_path / ".env").write_text("SYNTHETIC_FIXTURE=1\n")
    os.environ.pop("FAMILY_CFO_MANAGER_CREDENTIAL", None)
    yield tmp_path
    os.environ.pop("FAMILY_CFO_MANAGER_CREDENTIAL", None)

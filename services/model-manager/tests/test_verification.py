"""Exact readiness verification (ADR 0081 S4): identity, served name, photo
routing, TP2 ranks, bounded synthetic probes, and the probe bounds."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any


from family_cfo_model_manager import deployment
from family_cfo_model_manager.contracts import PhotoMode, ReasonCode, Topology
from family_cfo_model_manager.planning import LaunchPlan
from family_cfo_model_manager.verification import (
    ATTEMPT_GAP_SECONDS,
    EVIDENCE_FRESHNESS_SECONDS,
    MAX_ATTEMPTS,
    PHOTO_DATA_URI,
    PHOTO_MAX_TOKENS,
    PROBE_TIMEOUT_SECONDS,
    SYNTHETIC_TOOL,
    TEXT_MAX_TOKENS,
    TOOL_MAX_TOKENS,
    ExpectedRuntime,
    Verifier,
    VerificationEvidence,
    VerificationRefusal,
)
from conftest import FakeHttp, IMAGE_ID, MAIN, VISION, service

MAIN_COMMAND = (
    f"exec vllm serve '{MAIN}' --tool-call-parser 'hermes' "
    "--gpu-memory-utilization '0.85' --max-model-len '8192'"
)
TP2_COMMAND = (
    f"exec vllm serve '{MAIN}' --tool-call-parser 'hermes' "
    "--tensor-parallel-size=2 --gpu-memory-utilization '0.85' --max-model-len '8192'"
)
VISION_COMMAND = (
    f"exec vllm serve --model '{VISION}' --gpu-memory-utilization '0.20'"
)


class RecordingHttp:
    """Delegates to a FakeHttp while recording each request's timeout."""

    def __init__(self, inner: FakeHttp) -> None:
        self.inner = inner
        self.timeouts: list[tuple[str, float]] = []

    def get_json(self, url: str, timeout: float) -> tuple[int, Any]:
        self.timeouts.append((url, timeout))
        return self.inner.get_json(url, timeout)

    def post_json(self, url: str, payload: dict[str, Any], timeout: float) -> tuple[int, Any]:
        self.timeouts.append((url, timeout))
        return self.inner.post_json(url, payload, timeout)


def ok_http(**overrides: tuple[int, Any]) -> FakeHttp:
    """Every probe answered positively; `overrides` replace specific responses
    matched by URL suffix. Unmatched requests stay healthy."""

    def ok_handler(method: str, url: str, payload: Any) -> tuple[int, Any] | None:
        if url.endswith("/health"):
            return (200, {"status": "ok"})
        if url.endswith("/v1/models"):
            return (
                200,
                {"data": [{"id": model} for model in (MAIN, VISION, "Qwen/Qwen2.5-7B-Instruct")]},
            )
        if url.endswith("/v1/chat/completions"):
            message: dict[str, Any] = {"role": "assistant", "content": "OK"}
            if isinstance(payload, dict) and payload.get("tools"):
                message["content"] = None
                message["tool_calls"] = [
                    {
                        "id": "probe-1",
                        "type": "function",
                        "function": {"name": "verify_noop", "arguments": '{"note": "probe"}'},
                    }
                ]
            return (200, {"choices": [{"message": message, "finish_reason": "stop"}]})
        return None

    def handler(method: str, url: str, payload: Any) -> tuple[int, Any] | None:
        for suffix, response in overrides.items():
            if url.endswith(suffix):
                return response
        return ok_handler(method, url, payload)

    return FakeHttp(handler=handler)


def make_services(
    *,
    photo_mode: PhotoMode = PhotoMode.OFF,
    main_image: str | None = IMAGE_ID,
    command: str = MAIN_COMMAND,
    restart_count: int = 0,
    api_env: dict[str, str] | None = None,
) -> tuple:
    services = [
        service(
            "vllm",
            running=True,
            image_id=main_image,
            command=command,
            restart_count=restart_count,
        )
    ]
    if photo_mode is PhotoMode.SEPARATE:
        services.append(
            service(
                "vllm-vision",
                running=True,
                image_id=IMAGE_ID,
                command=VISION_COMMAND,
            )
        )
    env = {"FAMILY_CFO_AI_SUPPORTS_VISION": "false", "FAMILY_CFO_AI_VISION_ENABLED": "false"}
    if photo_mode is PhotoMode.MAIN:
        env["FAMILY_CFO_AI_SUPPORTS_VISION"] = "true"
    if photo_mode is PhotoMode.SEPARATE:
        env["FAMILY_CFO_AI_VISION_ENABLED"] = "true"
        env["FAMILY_CFO_AI_VISION_MODEL"] = VISION
    if api_env is not None:
        env.update(api_env)
    services.append(service("api", running=True, image_id=IMAGE_ID, env=env))
    return tuple(services)


def make_expected(**overrides) -> ExpectedRuntime:
    fields = {
        "main_model": MAIN,
        "vision_model": None,
        "photo_mode": PhotoMode.OFF,
        "topology": Topology.SINGLE,
        "image_id": IMAGE_ID,
        "tool_call_parser": "hermes",
        "max_model_len": 8192,
        "plan_hash": "ab" * 32,
    }
    fields.update(overrides)
    return ExpectedRuntime(**fields)


def make_verifier(
    *,
    http: Any = None,
    services: tuple = (),
    services_factory: Any = None,
    peer: Any = None,
):
    clock_value = [0.0]
    sleeps: list[float] = []

    def compose_services():
        if services_factory is not None:
            return services_factory()
        return services

    verifier = Verifier(
        http=http if http is not None else ok_http(),
        compose_services=compose_services,
        peer=peer,
        sleep=sleeps.append,
        clock=lambda: clock_value[0],
    )
    return verifier, clock_value, sleeps


def test_bounds_match_the_spec():
    assert PROBE_TIMEOUT_SECONDS == 30.0
    assert MAX_ATTEMPTS == 3
    assert ATTEMPT_GAP_SECONDS == 5.0
    assert TEXT_MAX_TOKENS == 32
    assert TOOL_MAX_TOKENS == 128
    assert PHOTO_MAX_TOKENS == 64
    assert EVIDENCE_FRESHNESS_SECONDS == 30.0


def test_photo_data_uri_is_a_fixed_synthetic_png():
    assert PHOTO_DATA_URI.startswith("data:image/png;base64,")
    raw = base64.b64decode(PHOTO_DATA_URI.split(",", 1)[1])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")  # a real PNG header, fixed bytes


def test_passing_verification_records_evidence():
    verifier, _clock, sleeps = make_verifier(services=make_services())
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert outcome.passed
    evidence = outcome.evidence
    assert evidence is not None
    assert evidence.main_served_name == MAIN
    assert evidence.photo_mode is PhotoMode.OFF
    assert evidence.topology is Topology.SINGLE
    assert evidence.image_ids == {"vllm": IMAGE_ID, "api": IMAGE_ID}
    assert evidence.restart_counts == {"vllm": 0, "api": 0}
    assert evidence.plan_hash == "ab" * 32
    assert set(evidence.probes) == {"text", "tool"}
    assert sleeps == []  # first attempt passed; no gaps


def test_expected_runtime_from_plan_maps_the_frozen_fields():
    plan = LaunchPlan(
        main_model=MAIN,
        main_revision="8b5e6c1f",
        vision_model=VISION,
        vision_revision="4a2d9e07",
        photo_mode=PhotoMode.SEPARATE,
        topology=Topology.SINGLE,
        runtime_version="0.32.0",
        image_digest=IMAGE_ID,
        context_limit=8192,
        concurrency_limit=1,
        tool_call_parser="hermes",
        reasoning_parser=None,
        node_requirements=(),
    )
    expected = ExpectedRuntime.from_plan(plan, image_id=IMAGE_ID, peer_host="peer-1")
    assert expected.main_model == MAIN
    assert expected.vision_model == VISION
    assert expected.photo_mode is PhotoMode.SEPARATE
    assert expected.topology is Topology.SINGLE
    assert expected.image_id == IMAGE_ID
    assert expected.tool_call_parser == "hermes"
    assert expected.max_model_len == 8192
    assert expected.plan_hash == plan.digest()
    assert expected.peer_host == "peer-1"
    # The EXACT intended photo route (spec 07).
    assert expected.photo_base_url == "http://vllm-vision:8000"
    assert expected.main_base_url == "http://vllm:8000"


# --- identity ----------------------------------------------------------------------


def test_image_identity_mismatch_fails():
    verifier, _clock, _sleeps = make_verifier(
        services=make_services(main_image="sha256:" + "1" * 64)
    )
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert outcome.reason_code is ReasonCode.RUNTIME_INCOMPATIBLE
    assert "image" in outcome.message


def test_restart_counter_change_discards_the_attempt():
    # A restart is visible WITHIN each attempt: before 0, after 1.
    calls = [0]

    def flapping():
        calls[0] += 1
        return make_services(restart_count=calls[0] % 2)

    verifier, _clock, sleeps = make_verifier(services_factory=flapping)
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "restarted during verification" in outcome.message
    # Three complete attempts, 5 s apart.
    assert sleeps == [ATTEMPT_GAP_SECONDS, ATTEMPT_GAP_SECONDS]


def test_container_stopping_mid_sequence_discards_the_attempt():
    calls = [0]

    def flapping():
        calls[0] += 1
        if calls[0] % 2 == 1:
            return make_services()
        return (
            service("vllm", running=False, image_id=IMAGE_ID, command=MAIN_COMMAND),
            service("api", running=True, image_id=IMAGE_ID, env={}),
        )

    verifier, _clock, _sleeps = make_verifier(services_factory=flapping)
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "restarted during verification" in outcome.message


# --- served name + structured options ------------------------------------------------


def test_served_name_must_match_exactly():
    http = ok_http(
        **{"/v1/models": (200, {"data": [{"id": "Qwen/Qwen2.5-7B-Instruct"}]})}
    )
    verifier, _clock, _sleeps = make_verifier(http=http, services=make_services())
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "does not serve the exact requested main model" in outcome.message


def test_running_command_must_carry_the_frozen_options():
    # Missing the context limit.
    verifier, _clock, _sleeps = make_verifier(
        services=make_services(
            command=f"exec vllm serve '{MAIN}' --tool-call-parser 'hermes' "
            "--gpu-memory-utilization '0.85'"
        )
    )
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "different context limit" in outcome.message

    # Wrong tool parser.
    verifier2, _clock, _sleeps = make_verifier(
        services=make_services(
            command=f"exec vllm serve '{MAIN}' --tool-call-parser 'llama3' "
            "--gpu-memory-utilization '0.85' --max-model-len '8192'"
        )
    )
    outcome2 = verifier2.verify(make_expected(), deadline=100.0)
    assert not outcome2.passed
    assert "different tool parser" in outcome2.message

    # Unexpected tensor parallelism on a single-node plan.
    verifier3, _clock, _sleeps = make_verifier(
        services=make_services(command=TP2_COMMAND)
    )
    outcome3 = verifier3.verify(make_expected(), deadline=100.0)
    assert not outcome3.passed
    assert "tensor parallelism 2" in outcome3.message

    # A different main model in the command.
    verifier4, _clock, _sleeps = make_verifier(
        services=make_services(
            command="exec vllm serve 'Qwen/Qwen2.5-7B-Instruct' --tool-call-parser 'hermes' "
            "--gpu-memory-utilization '0.85' --max-model-len '8192'"
        )
    )
    outcome4 = verifier4.verify(make_expected(), deadline=100.0)
    assert not outcome4.passed
    assert "different main model" in outcome4.message


# --- photo modes ---------------------------------------------------------------------


def test_photo_mode_off_refuses_a_running_describer():
    services = make_services() + (
        service("vllm-vision", running=True, image_id=IMAGE_ID, command=VISION_COMMAND),
    )
    verifier, _clock, _sleeps = make_verifier(services=services)
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "photo mode off but a managed describer is running" in outcome.message


def test_photo_mode_off_refuses_enabled_application_routing():
    verifier, _clock, _sleeps = make_verifier(
        services=make_services(
            api_env={"FAMILY_CFO_AI_VISION_ENABLED": "true", "FAMILY_CFO_AI_VISION_MODEL": VISION}
        )
    )
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "photo mode off but application photo routing is enabled" in outcome.message


def test_photo_mode_main_requires_application_routing():
    verifier, _clock, _sleeps = make_verifier(services=make_services())
    outcome = verifier.verify(
        make_expected(photo_mode=PhotoMode.MAIN, vision_model=None), deadline=100.0
    )
    assert not outcome.passed
    assert "does not route photos to main" in outcome.message

    # With routing enabled, the photo probe runs on the MAIN route only.
    http = ok_http()
    verifier2, _clock, _sleeps = make_verifier(
        http=http,
        services=make_services(api_env={"FAMILY_CFO_AI_SUPPORTS_VISION": "true"}),
    )
    outcome2 = verifier2.verify(
        make_expected(photo_mode=PhotoMode.MAIN, vision_model=None), deadline=100.0
    )
    assert outcome2.passed
    urls = [call[1] for call in http.calls]
    assert not any("vllm-vision" in url for url in urls)
    assert set(outcome2.evidence.probes) == {"text", "tool", "photo"}


def test_photo_mode_separate_requires_describer_and_exact_routing():
    # Describer missing entirely.
    verifier, _clock, _sleeps = make_verifier(services=make_services())
    outcome = verifier.verify(
        make_expected(photo_mode=PhotoMode.SEPARATE, vision_model=VISION), deadline=100.0
    )
    assert not outcome.passed
    assert "describer is not running" in outcome.message

    # Routing aimed at a different model.
    verifier2, _clock, _sleeps = make_verifier(
        services=make_services(
            photo_mode=PhotoMode.SEPARATE,
            api_env={"FAMILY_CFO_AI_VISION_MODEL": "Qwen/Qwen2.5-VL-7B-Instruct"},
        )
    )
    outcome2 = verifier2.verify(
        make_expected(photo_mode=PhotoMode.SEPARATE, vision_model=VISION), deadline=100.0
    )
    assert not outcome2.passed
    assert "does not target the planned describer" in outcome2.message

    # Describer serving a different model than planned.
    services = list(make_services(photo_mode=PhotoMode.SEPARATE))
    services[1] = service(
        "vllm-vision",
        running=True,
        image_id=IMAGE_ID,
        command="exec vllm serve --model 'Qwen/Qwen2.5-VL-7B-Instruct' --gpu-memory-utilization '0.20'",
    )
    verifier3, _clock, _sleeps = make_verifier(services=tuple(services))
    outcome3 = verifier3.verify(
        make_expected(photo_mode=PhotoMode.SEPARATE, vision_model=VISION), deadline=100.0
    )
    assert not outcome3.passed
    assert "serves a different model than planned" in outcome3.message

    # Correct: photo probes hit the describer route exactly.
    http = ok_http()
    verifier4, _clock, _sleeps = make_verifier(
        http=http, services=make_services(photo_mode=PhotoMode.SEPARATE)
    )
    outcome4 = verifier4.verify(
        make_expected(photo_mode=PhotoMode.SEPARATE, vision_model=VISION), deadline=100.0
    )
    assert outcome4.passed
    urls = [call[1] for call in http.calls]
    assert "http://vllm-vision:8000/health" in urls
    assert "http://vllm-vision:8000/v1/chat/completions" in urls


# --- TP2: two expected GPU ranks, not reachability ------------------------------------


RAY_TWO_ALIVE = (
    "node_1:  1 nodes\n    1 node_c0a1b2c3 ALIVE\n"
    "node_2:  1 nodes\n    2 node_d4e5f6a7 ALIVE\n"
)
GPU_MEMORY = "1024, 8192\n1024, 8192\n"


def test_tp2_requires_two_alive_ranks():
    from conftest import FakePeer

    peer = FakePeer({"ray_status": "1 node_c0a1b2c3 ALIVE\n", "gpu_memory": GPU_MEMORY})
    verifier, _clock, _sleeps = make_verifier(
        services=make_services(command=TP2_COMMAND), peer=peer
    )
    outcome = verifier.verify(
        make_expected(topology=Topology.CLUSTER, peer_host="peer-1"), deadline=100.0
    )
    assert not outcome.passed
    assert "TP2 requires two" in outcome.message


def test_tp2_with_two_ranks_and_gpu_memory_passes():
    from conftest import FakePeer

    peer = FakePeer({"ray_status": RAY_TWO_ALIVE, "gpu_memory": GPU_MEMORY})
    verifier, _clock, _sleeps = make_verifier(
        services=make_services(command=TP2_COMMAND), peer=peer
    )
    outcome = verifier.verify(
        make_expected(topology=Topology.CLUSTER, peer_host="peer-1"), deadline=100.0
    )
    assert outcome.passed
    assert peer.probes == [("peer-1", "ray_status"), ("peer-1", "gpu_memory")]


def test_tp2_without_a_peer_inspector_fails():
    verifier, _clock, _sleeps = make_verifier(
        services=make_services(command=TP2_COMMAND), peer=None
    )
    outcome = verifier.verify(
        make_expected(topology=Topology.CLUSTER, peer_host="peer-1"), deadline=100.0
    )
    assert not outcome.passed
    assert "no peer inspector" in outcome.message


def test_tp2_peer_refusal_fails_the_attempt():
    class RefusingPeer:
        def probe(self, peer_host, probe):
            raise deployment.DeploymentRefusal(
                ReasonCode.CLUSTER_PEER_UNREACHABLE, "the peer did not answer"
            )

    verifier, _clock, _sleeps = make_verifier(
        services=make_services(command=TP2_COMMAND), peer=RefusingPeer()
    )
    outcome = verifier.verify(
        make_expected(topology=Topology.CLUSTER, peer_host="peer-1"), deadline=100.0
    )
    assert not outcome.passed
    assert "did not answer" in outcome.message


def test_tp2_unparseable_ray_status_fails():
    from conftest import FakePeer

    peer = FakePeer({"ray_status": "complete gibberish", "gpu_memory": GPU_MEMORY})
    verifier, _clock, _sleeps = make_verifier(
        services=make_services(command=TP2_COMMAND), peer=peer
    )
    outcome = verifier.verify(
        make_expected(topology=Topology.CLUSTER, peer_host="peer-1"), deadline=100.0
    )
    assert not outcome.passed
    assert "could not be parsed" in outcome.message


# --- probes --------------------------------------------------------------------------


def test_tool_probe_declares_the_synthetic_tool_and_never_executes():
    http = ok_http()
    verifier, _clock, _sleeps = make_verifier(http=http, services=make_services())
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert outcome.passed
    tool_posts = [
        payload
        for method, url, payload in http.calls
        if method == "POST" and isinstance(payload, dict) and "tools" in payload
    ]
    assert len(tool_posts) == 1
    assert tool_posts[0]["tools"] == [SYNTHETIC_TOOL]
    assert tool_posts[0]["tool_choice"] == "auto"
    assert tool_posts[0]["max_tokens"] == TOOL_MAX_TOKENS
    # Text probe bound respected; nothing else is ever posted.
    text_posts = [
        payload
        for method, url, payload in http.calls
        if method == "POST" and isinstance(payload, dict) and "tools" not in payload
    ]
    assert all(post["max_tokens"] == TEXT_MAX_TOKENS for post in text_posts)


def test_tool_probe_refuses_a_foreign_parsed_call():
    def foreign_call(method, url, payload):
        if url.endswith("/v1/chat/completions") and isinstance(payload, dict) and payload.get("tools"):
            return (
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "1",
                                        "type": "function",
                                        "function": {
                                            "name": "delete_everything",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return None

    http = ok_http()
    real_handler = http.handler
    http.handler = lambda method, url, payload: (
        foreign_call(method, url, payload) or real_handler(method, url, payload)
    )
    verifier, _clock, _sleeps = make_verifier(http=http, services=make_services())
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "not the synthetic no-op tool" in outcome.message


def test_tool_probe_refuses_unparseable_arguments():
    def broken_arguments(method, url, payload):
        if url.endswith("/v1/chat/completions") and isinstance(payload, dict) and payload.get("tools"):
            return (
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "1",
                                        "type": "function",
                                        "function": {
                                            "name": "verify_noop",
                                            "arguments": "not json at all",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return None

    http = ok_http()
    real_handler = http.handler
    http.handler = lambda method, url, payload: (
        broken_arguments(method, url, payload) or real_handler(method, url, payload)
    )
    verifier, _clock, _sleeps = make_verifier(http=http, services=make_services())
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "schema-valid JSON" in outcome.message


def test_text_probe_requires_nonempty_content():
    def empty_content(method, url, payload):
        if url.endswith("/v1/chat/completions") and isinstance(payload, dict) and not payload.get("tools"):
            return (200, {"choices": [{"message": {"role": "assistant", "content": "  "}}]})
        return None

    http = ok_http()
    real_handler = http.handler
    http.handler = lambda method, url, payload: (
        empty_content(method, url, payload) or real_handler(method, url, payload)
    )
    verifier, _clock, _sleeps = make_verifier(http=http, services=make_services())
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "no content" in outcome.message


def test_health_and_model_listing_failures_fail_the_attempt():
    http = ok_http(**{"/health": (503, None)})
    verifier, _clock, _sleeps = make_verifier(http=http, services=make_services())
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "health check failed" in outcome.message

    http2 = ok_http(**{"/v1/models": (500, None)})
    verifier2, _clock, _sleeps = make_verifier(http=http2, services=make_services())
    outcome2 = verifier2.verify(make_expected(), deadline=100.0)
    assert not outcome2.passed
    assert "model listing failed" in outcome2.message


def test_separate_photo_describer_health_failure_fails():
    http = ok_http(**{"http://vllm-vision:8000/health": (503, None)})
    verifier, _clock, _sleeps = make_verifier(
        http=http, services=make_services(photo_mode=PhotoMode.SEPARATE)
    )
    outcome = verifier.verify(
        make_expected(photo_mode=PhotoMode.SEPARATE, vision_model=VISION), deadline=100.0
    )
    assert not outcome.passed
    assert "photo describer is not ready" in outcome.message


# --- bounds --------------------------------------------------------------------------


def test_probe_timeout_is_clipped_to_the_remaining_budget():
    http = RecordingHttp(ok_http())
    verifier, clock_value, _sleeps = make_verifier(http=http, services=make_services())
    clock_value[0] = 97.0  # 3.0 s remain before the deadline at 100.0
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert outcome.passed
    assert http.timeouts, "the verifier never probed"
    assert all(timeout <= 3.0 for _url, timeout in http.timeouts)


def test_deadline_expiry_fails_before_any_probe():
    http = RecordingHttp(ok_http())
    verifier, clock_value, _sleeps = make_verifier(http=http, services=make_services())
    clock_value[0] = 100.0  # deadline already reached
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert "deadline expired" in outcome.message
    assert http.timeouts == []


def test_attempts_are_bounded_to_three_with_gaps():
    http = ok_http(**{"/health": (500, None)})  # everything fails, fast
    verifier, _clock, sleeps = make_verifier(http=http, services=make_services())
    outcome = verifier.verify(make_expected(), deadline=1000.0)
    assert not outcome.passed
    assert sleeps == [ATTEMPT_GAP_SECONDS, ATTEMPT_GAP_SECONDS]
    health_calls = [call for call in http.calls if call[1].endswith("/health")]
    assert len(health_calls) == MAX_ATTEMPTS


def test_gap_is_clipped_to_the_remaining_deadline():
    http = ok_http(**{"/health": (500, None)})
    verifier, clock_value, sleeps = make_verifier(http=http, services=make_services())
    # The deadline lands mid-sequence: the last gap must never exceed it.
    clock_value[0] = 99.0  # 1.0 s remain
    outcome = verifier.verify(make_expected(), deadline=100.0)
    assert not outcome.passed
    assert all(gap <= ATTEMPT_GAP_SECONDS for gap in sleeps)


# --- evidence ------------------------------------------------------------------------


def test_evidence_age_and_freshness_bound():
    verified_at = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
    evidence = VerificationEvidence(
        verified_at=verified_at,
        main_served_name=MAIN,
        photo_mode=PhotoMode.OFF,
        topology=Topology.SINGLE,
        image_ids={"vllm": IMAGE_ID},
        restart_counts={"vllm": 0},
        plan_hash=None,
        probes=("text", "tool"),
    )
    assert evidence.age_seconds(verified_at + timedelta(seconds=10)) == 10.0
    assert evidence.age_seconds(verified_at - timedelta(seconds=5)) == 0.0
    document = evidence.to_dict()
    assert document["verified_at"] == verified_at.isoformat()
    assert document["probes"] == ["text", "tool"]


def test_verification_refusal_carries_the_reason_code():
    refusal = VerificationRefusal(ReasonCode.PREFLIGHT_UNAVAILABLE, "docker is gone")
    assert refusal.reason_code is ReasonCode.PREFLIGHT_UNAVAILABLE
    assert refusal.message == "docker is gone"

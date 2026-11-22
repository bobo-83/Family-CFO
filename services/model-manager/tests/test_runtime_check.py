"""Installed-image checker adapters (issue #170 Item 3).

The checker is exercised through an injected fake ContainerRunner: no real
vLLM image is pulled in tests (python:3.12-slim is the only preloaded image,
and it is reserved for the flock gate). Every refusal here must fail closed
and never approve.
"""

from __future__ import annotations

import pytest

from family_cfo_model_manager.contracts import Evidence, ReasonCode
from family_cfo_model_manager.planning import ProfilePolicy
from family_cfo_model_manager.runtime_check import (
    CHECKER_CPUS,
    CHECKER_MEMORY_GB,
    CHECKER_SUBPROCESS_CEILING_SECONDS,
    CheckerContext,
    ContainerResult,
    ContainerSpec,
    MiniMaxVendorAdapter,
    RuntimeCheckRefusal,
    VLLM024Adapter,
    VLLM032Adapter,
    adapter_for,
    allow_remote_code,
    check_model_compatibility,
    clip_to_budget,
    safe_reason,
)

IMAGE_032 = "sha256:" + "1" * 64  # an immutable image ID, never a tag


def _context(**overrides) -> CheckerContext:
    fields = {
        "image_id": IMAGE_032,
        "runtime_version": "0.32.0",
        "repo_id": "nvidia/Qwen3.8-Flash-Next-NVFP4",
        "revision": "8b5e6c1f",
        "serve_options": {"max_model_len": 8192, "tool_call_parser": "hermes"},
        "profile": None,
    }
    fields.update(overrides)
    return CheckerContext(**fields)


class FakeRunner:
    def __init__(self, result: ContainerResult | Exception) -> None:
        self.result = result
        self.specs: list[ContainerSpec] = []
        self.timeouts: list[float] = []

    def run(self, spec: ContainerSpec, timeout: float) -> ContainerResult:
        self.specs.append(spec)
        self.timeouts.append(timeout)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _verdict(**overrides) -> ContainerResult:
    payload = {
        "supported": True,
        "backend": "vllm-native",
        "reason": None,
        "architectures": ["Qwen2ForCausalLM"],
        "transformers_fallback": False,
    }
    payload.update(overrides)
    import json

    return ContainerResult(exit_code=0, stdout=json.dumps(payload), stderr="")


# --- adapter selection ------------------------------------------------------------


def test_official_versions_map_to_their_adapter() -> None:
    assert isinstance(adapter_for(_context(runtime_version="0.24.0")), VLLM024Adapter)
    assert isinstance(adapter_for(_context(runtime_version="0.24.3")), VLLM024Adapter)
    assert isinstance(adapter_for(_context(runtime_version="0.32.0")), VLLM032Adapter)


def test_unsupported_version_refuses_actionably_never_a_static_list() -> None:
    with pytest.raises(RuntimeCheckRefusal) as excinfo:
        adapter_for(_context(runtime_version="0.28.1"))
    refusal = excinfo.value
    assert refusal.reason_code is ReasonCode.RUNTIME_INCOMPATIBLE
    # Actionable: names the installed version AND the reviewed coverage.
    assert "0.28.1" in refusal.message
    assert "0.24.x" in refusal.message and "0.32.x" in refusal.message
    # Never a static architecture allowlist masquerading as an adapter.
    assert "qwen4_exp" not in refusal.message


def test_unparsable_version_refuses() -> None:
    with pytest.raises(RuntimeCheckRefusal) as excinfo:
        adapter_for(_context(runtime_version="nightly-20261120"))
    assert excinfo.value.reason_code is ReasonCode.RUNTIME_INCOMPATIBLE


def test_vendor_build_approved_only_through_explicit_binding() -> None:
    # The explicitly tested vendor build gets its adapter...
    vendor = adapter_for(
        _context(image_repository="minimax/minimax-m2", runtime_version="9.9.9")
    )
    assert isinstance(vendor, MiniMaxVendorAdapter)
    # ...while any OTHER vendor build is refused even with a parsable version:
    # semantic-version guessing must never approve it.
    with pytest.raises(RuntimeCheckRefusal) as excinfo:
        adapter_for(
            _context(image_repository="example/vendor-vllm", runtime_version="0.32.0")
        )
    assert "vendor build" in excinfo.value.message


# --- container confinement ----------------------------------------------------------


def test_checker_container_is_confined_and_fixed_entry_point() -> None:
    runner = FakeRunner(_verdict())
    check_model_compatibility(_context(), runner, remaining_budget_seconds=120.0)
    (spec,) = runner.specs
    assert spec.image_id == IMAGE_032  # the EXACT installed immutable image ID
    assert spec.read_only_rootfs is True
    assert spec.privileged is False
    assert spec.cpus == CHECKER_CPUS == 2.0
    assert spec.memory_gb == CHECKER_MEMORY_GB == 4
    assert spec.entrypoint == ("python", "-c")  # fixed entry point, no caller shell
    # No Docker socket, no privileged devices, no host paths mounted.
    for _source, target, _mode in spec.volumes:
        assert "docker.sock" not in target
    # The generated script is the fixed checker; the request only shaped it.
    script = spec.command[0]
    assert "ModelConfig(" in script
    assert "trust_remote_code=False" in script  # remote code disabled by default
    assert "for parser in ('hermes', None):" in script
    assert "ToolParserManager.get_tool_parser(parser)" in script


def test_remote_code_runs_only_with_an_explicit_reviewed_profile() -> None:
    assert allow_remote_code(None) is False
    assert allow_remote_code(ProfilePolicy()) is False
    assert allow_remote_code(ProfilePolicy(remote_code=True)) is True
    runner = FakeRunner(_verdict())
    check_model_compatibility(
        _context(profile=ProfilePolicy(remote_code=True)), runner, 120.0
    )
    assert "trust_remote_code=True" in runner.specs[0].command[0]


def test_job_labels_travel_with_the_checker_container() -> None:
    runner = FakeRunner(_verdict())
    check_model_compatibility(
        _context(), runner, 120.0, labels={"family.cfo.job": "abc"}
    )
    assert runner.specs[0].labels == (("family.cfo.job", "abc"),)


# --- fail-closed verdicts ---------------------------------------------------------------


def test_supported_verdict_is_returned_with_evidence() -> None:
    runner = FakeRunner(
        _verdict(
            backend="transformers", transformers_fallback=True,
            architectures=["Qwen4ExpForCausalLM"],
        )
    )
    verdict = check_model_compatibility(_context(), runner, 120.0)
    assert verdict.supported is True
    assert verdict.backend == "transformers"
    assert verdict.transformers_fallback is True
    assert verdict.evidence is Evidence.VERIFIED


def test_unsupported_architecture_refuses_with_safe_reason() -> None:
    reason = "Model architectures ['qwen4_exp'] are not supported for now.\x1b[1m"
    runner = FakeRunner(_verdict(supported=False, reason=reason))
    with pytest.raises(RuntimeCheckRefusal) as excinfo:
        check_model_compatibility(_context(), runner, 120.0)
    assert excinfo.value.reason_code is ReasonCode.RUNTIME_INCOMPATIBLE
    assert "qwen4_exp" in excinfo.value.message
    assert "\x1b" not in excinfo.value.message  # ANSI stripped from diagnostics


def test_missing_tool_parser_refuses() -> None:
    runner = FakeRunner(
        _verdict(supported=False, reason="tool/reasoning parser unavailable: no such parser")
    )
    with pytest.raises(RuntimeCheckRefusal) as excinfo:
        check_model_compatibility(_context(), runner, 120.0)
    assert excinfo.value.reason_code is ReasonCode.RUNTIME_INCOMPATIBLE
    assert "parser" in excinfo.value.message


@pytest.mark.parametrize(
    "result",
    [
        ContainerResult(exit_code=1, stdout="", stderr="boom"),
        ContainerResult(exit_code=0, stdout="not json at all", stderr=""),
        ContainerResult(exit_code=0, stdout='{"no_supported_key": true}', stderr=""),
    ],
)
def test_container_or_output_failure_never_approves(result) -> None:
    with pytest.raises(RuntimeCheckRefusal) as excinfo:
        check_model_compatibility(_context(), FakeRunner(result), 120.0)
    assert excinfo.value.reason_code is ReasonCode.PREFLIGHT_UNAVAILABLE


def test_runner_infrastructure_failure_never_approves() -> None:
    with pytest.raises(RuntimeCheckRefusal) as excinfo:
        check_model_compatibility(_context(), FakeRunner(OSError("no docker")), 120.0)
    assert excinfo.value.reason_code is ReasonCode.PREFLIGHT_UNAVAILABLE


# --- budget clipping -------------------------------------------------------------------


def test_subprocess_ceiling_clips_to_the_remaining_budget() -> None:
    assert clip_to_budget(CHECKER_SUBPROCESS_CEILING_SECONDS, 120.0) == 60.0
    assert clip_to_budget(CHECKER_SUBPROCESS_CEILING_SECONDS, 10.0) == 10.0
    assert clip_to_budget(CHECKER_SUBPROCESS_CEILING_SECONDS, 0.5) == 0.5

    runner = FakeRunner(_verdict())
    check_model_compatibility(_context(), runner, remaining_budget_seconds=10.0)
    assert runner.timeouts == [10.0]  # never above the remaining budget


def test_exhausted_budget_refuses_before_running_the_checker() -> None:
    runner = FakeRunner(_verdict())
    with pytest.raises(RuntimeCheckRefusal) as excinfo:
        check_model_compatibility(_context(), runner, remaining_budget_seconds=0.0)
    assert excinfo.value.reason_code is ReasonCode.PREFLIGHT_UNAVAILABLE
    assert runner.specs == []  # the checker never ran


def test_safe_reason_bounds_and_strips() -> None:
    noisy = "\x1b[31m" + "x" * 3000
    cleaned = safe_reason(noisy)
    assert cleaned is not None
    assert len(cleaned) <= 2048
    assert "\x1b" not in cleaned
    assert safe_reason(None) is None
    assert safe_reason("") is None

"""Pure resolver matrix (issue #170 Item 2): placement normalization, admission
math, topology candidates, unknown-evidence refusal. Deterministic - no I/O,
no Docker, no active runtime mutation on refusal."""

from __future__ import annotations

import pytest

from family_cfo_model_manager.contracts import (
    Evidence,
    PhotoMode,
    ReasonCode,
    Topology,
)
from family_cfo_model_manager.planning import (
    GENERIC_CONCURRENCY_LIMIT,
    GENERIC_CONTEXT_LIMIT,
    MAIN_RANK_OVERHEAD_GB,
    ModelFacts,
    NodeHardware,
    PlanningRefusal,
    PlanRequest,
    ProfilePolicy,
    build_topology_candidates,
    disk_demand_gb,
    effective_limits,
    node_disk_verdict,
    node_memory_verdict,
    normalize_photo_mode,
    placement_demand,
    resolve_launch_plan,
)

MAIN = "Qwen/Qwen2.5-14B-Instruct"
VISION = "Qwen/Qwen2.5-VL-3B-Instruct"

_GB = 1_000_000_000.0


def _request(**overrides) -> PlanRequest:
    fields = {
        "request_id": "0f0e1d2c-3b4a-5968-7789-abcdef012345",
        "main_model": MAIN,
        "vision_model": None,
        "photo_mode": PhotoMode.AUTO,
        "topology": Topology.AUTO,
        "cluster_consent": False,
    }
    fields.update(overrides)
    return PlanRequest(**fields)


def _main_facts(**overrides):
    fields = {
        "repo_id": MAIN,
        "revision": "8b5e6c1f",
        "architecture": "qwen2",
        "resident_weight_bytes": int(8.0 * _GB),
        "multimodal": False,
        "model_context_limit": 32_768,
        "evidence": Evidence.ESTIMATED,
    }
    fields.update(overrides)
    return ModelFacts(**fields)


def _vision_facts(**overrides):
    fields = {
        "repo_id": VISION,
        "revision": "4a2d9e07",
        "architecture": "qwen2_vl",
        "resident_weight_bytes": int(2.0 * _GB),
        "multimodal": True,
        "model_context_limit": 32_768,
        "evidence": Evidence.ESTIMATED,
    }
    fields.update(overrides)
    return ModelFacts(**fields)


def _node(name: str = "primary", memory_gb: float = 20.0, **overrides) -> NodeHardware:
    fields = {
        "node": name,
        "memory_gb": memory_gb,
        "cache_filesystem_gb": 500.0,
        "disk_free_gb": 200.0,
        "resident_managed_gb": None,
        "evidence": Evidence.VERIFIED,
    }
    fields.update(overrides)
    return NodeHardware(**fields)


def _refusal(call, code: ReasonCode) -> PlanningRefusal:
    with pytest.raises(PlanningRefusal) as excinfo:
        call()
    assert excinfo.value.reason_code is code
    return excinfo.value


# --- photo placement normalization ------------------------------------------------


def test_auto_with_distinct_vision_is_separate() -> None:
    placement = normalize_photo_mode(_request(vision_model=VISION), _main_facts(), _vision_facts())
    assert (placement.main_model, placement.vision_model, placement.photo_mode) == (
        MAIN,
        VISION,
        PhotoMode.SEPARATE,
    )


def test_auto_without_vision_needs_verified_multimodal_else_off() -> None:
    assert normalize_photo_mode(_request(), _main_facts(multimodal=True), None).photo_mode is PhotoMode.MAIN
    assert normalize_photo_mode(_request(), _main_facts(multimodal=False), None).photo_mode is PhotoMode.OFF
    # Unknown support is never treated as multimodal.
    assert normalize_photo_mode(_request(), _main_facts(multimodal=None), None).photo_mode is PhotoMode.OFF


def test_equal_ids_normalize_to_one_instance() -> None:
    placement = normalize_photo_mode(
        _request(vision_model=MAIN, photo_mode=PhotoMode.AUTO),
        _main_facts(multimodal=True),
        _vision_facts(repo_id=MAIN, multimodal=True),
    )
    assert placement.photo_mode is PhotoMode.MAIN
    assert placement.vision_model is None


def test_equal_ids_reject_when_images_are_not_supported() -> None:
    _refusal(
        lambda: normalize_photo_mode(
            _request(vision_model=MAIN), _main_facts(multimodal=False), None
        ),
        ReasonCode.UNSUPPORTED_PHOTO_MODE,
    )
    # Unknown support cannot validate the main photo role either.
    _refusal(
        lambda: normalize_photo_mode(
            _request(vision_model=MAIN), _main_facts(multimodal=None), None
        ),
        ReasonCode.INSUFFICIENT_EVIDENCE,
    )


def test_explicit_modes_are_contracts_not_hints() -> None:
    assert normalize_photo_mode(_request(photo_mode=PhotoMode.OFF), _main_facts(), None).photo_mode is PhotoMode.OFF
    _refusal(
        lambda: normalize_photo_mode(
            _request(photo_mode=PhotoMode.OFF, vision_model=VISION), _main_facts(), _vision_facts()
        ),
        ReasonCode.UNSUPPORTED_PHOTO_MODE,
    )
    _refusal(
        lambda: normalize_photo_mode(
            _request(photo_mode=PhotoMode.MAIN, vision_model=VISION), _main_facts(), _vision_facts()
        ),
        ReasonCode.UNSUPPORTED_PHOTO_MODE,
    )
    _refusal(
        lambda: normalize_photo_mode(_request(photo_mode=PhotoMode.SEPARATE), _main_facts(), None),
        ReasonCode.UNSUPPORTED_PHOTO_MODE,
    )
    _refusal(
        lambda: normalize_photo_mode(
            _request(photo_mode=PhotoMode.SEPARATE, vision_model=VISION),
            _main_facts(),
            _vision_facts(multimodal=False),
        ),
        ReasonCode.UNSUPPORTED_PHOTO_MODE,
    )
    _refusal(
        lambda: normalize_photo_mode(
            _request(photo_mode=PhotoMode.SEPARATE, vision_model=VISION),
            _main_facts(),
            _vision_facts(multimodal=None),
        ),
        ReasonCode.INSUFFICIENT_EVIDENCE,
    )


# --- memory math ------------------------------------------------------------------


def test_demand_applies_margin_overhead_and_per_placement_photo() -> None:
    single = placement_demand(
        main_weight_bytes=int(8.0 * _GB),
        photo_weight_bytes=None,
        kv_bytes=int(1.0 * _GB),
        tensor_parallel_size=1,
    )
    # 8 GB * 1.15 margin + 1 GB KV + 4 GiB rank overhead.
    assert single.total_gb == pytest.approx(8 * 1.15 + 1.0 + MAIN_RANK_OVERHEAD_GB)

    with_photo = placement_demand(
        main_weight_bytes=int(8.0 * _GB),
        photo_weight_bytes=int(2.0 * _GB),
        kv_bytes=int(1.0 * _GB),
        tensor_parallel_size=1,
    )
    # The photo instance is a full additional placement (weights + its own
    # overhead) - never folded into a divided-by-two figure.
    assert with_photo.total_gb == pytest.approx(single.total_gb + 2 * 1.15 + 2.0)


def test_tensor_parallel_shards_weights_and_kv_but_not_rank_overhead() -> None:
    single = placement_demand(
        main_weight_bytes=int(8.0 * _GB), photo_weight_bytes=None,
        kv_bytes=int(1.0 * _GB), tensor_parallel_size=1,
    )
    tp2 = placement_demand(
        main_weight_bytes=int(8.0 * _GB), photo_weight_bytes=None,
        kv_bytes=int(1.0 * _GB), tensor_parallel_size=2,
    )
    assert tp2.weights_gb == pytest.approx(single.weights_gb / 2)
    assert tp2.kv_gb == pytest.approx(single.kv_gb / 2)
    assert tp2.overhead_gb == pytest.approx(single.overhead_gb)


def test_profiles_may_raise_overhead_never_lower_it() -> None:
    raised = placement_demand(
        main_weight_bytes=int(8.0 * _GB), photo_weight_bytes=None,
        kv_bytes=0, tensor_parallel_size=1,
        profile=ProfilePolicy(main_rank_overhead_gb=8.0),
    )
    assert raised.overhead_gb == 8.0
    lowered = placement_demand(
        main_weight_bytes=int(8.0 * _GB), photo_weight_bytes=None,
        kv_bytes=0, tensor_parallel_size=1,
        profile=ProfilePolicy(main_rank_overhead_gb=1.0),
    )
    assert lowered.overhead_gb == MAIN_RANK_OVERHEAD_GB


def test_memory_verdict_reserves_and_refuses() -> None:
    demand = placement_demand(
        main_weight_bytes=int(8.0 * _GB), photo_weight_bytes=None,
        kv_bytes=int(1.0 * _GB), tensor_parallel_size=1,
    )  # 14.2 GB
    fits, reason = node_memory_verdict(_node(memory_gb=32.0), demand)
    assert fits and reason is None
    # max(8 GiB, 8%) reserve holds independently: 12 GB available < 14.2.
    fits, reason = node_memory_verdict(_node(memory_gb=16.0), demand)
    assert not fits and reason == ReasonCode.INSUFFICIENT_MEMORY.value
    # Missing hardware evidence is never a fit.
    fits, reason = node_memory_verdict(_node(memory_gb=None), demand)
    assert not fits and reason == ReasonCode.INSUFFICIENT_EVIDENCE.value
    # A resident peer describer counts exactly once against its node.
    fits, reason = node_memory_verdict(
        _node(memory_gb=20.0, resident_managed_gb=6.0), demand
    )
    assert not fits and reason == ReasonCode.INSUFFICIENT_MEMORY.value


def test_disk_demand_and_verdict() -> None:
    requirement, refusal = disk_demand_gb(int(10.0 * _GB), _node(cache_filesystem_gb=500.0))
    assert refusal is None
    # 10 GB missing + 10% + max(5 GiB, 5% of 500) = 11 + 25.
    assert requirement == pytest.approx(11.0 + 25.0)
    # Small cache filesystem: the 5 GiB floor applies.
    requirement, _ = disk_demand_gb(int(1.0 * _GB), _node(cache_filesystem_gb=50.0))
    assert requirement == pytest.approx(1.1 + 5.0)
    # Missing evidence is never zero.
    assert disk_demand_gb(None, _node())[1] == ReasonCode.INSUFFICIENT_EVIDENCE.value
    assert disk_demand_gb(int(1.0 * _GB), _node(cache_filesystem_gb=None))[1] == (
        ReasonCode.INSUFFICIENT_EVIDENCE.value
    )
    fits, reason = node_disk_verdict(_node(disk_free_gb=10.0), int(10.0 * _GB))
    assert not fits and reason == ReasonCode.INSUFFICIENT_DISK.value
    fits, reason = node_disk_verdict(_node(disk_free_gb=None), int(1.0 * _GB))
    assert not fits and reason == ReasonCode.INSUFFICIENT_EVIDENCE.value


# --- limits -------------------------------------------------------------------------


def test_generic_caps_bound_uncatalogued_models() -> None:
    assert effective_limits(None, None) == (GENERIC_CONTEXT_LIMIT, GENERIC_CONCURRENCY_LIMIT)
    assert effective_limits(None, 4096) == (4096, 1)
    profile = ProfilePolicy(context_limit=16_384, concurrency_limit=4)
    assert effective_limits(profile, None) == (16_384, 4)
    # Bounded by the model's own limit when known.
    assert effective_limits(profile, 8_192) == (8_192, 4)


# --- topology candidates ---------------------------------------------------------------


def test_single_prohibits_escalation() -> None:
    candidates = build_topology_candidates(_request(topology=Topology.SINGLE), (_node(), _node()), None)
    assert [candidate.topology for candidate in candidates] == [Topology.SINGLE]


def test_cluster_requires_consent_and_two_nodes() -> None:
    _refusal(
        lambda: build_topology_candidates(_request(topology=Topology.CLUSTER), (_node(), _node()), None),
        ReasonCode.CLUSTER_CONSENT_REQUIRED,
    )
    _refusal(
        lambda: build_topology_candidates(
            _request(topology=Topology.CLUSTER, cluster_consent=True), (_node(),), None
        ),
        ReasonCode.CLUSTER_PEER_UNREACHABLE,
    )


def test_auto_prefers_single_and_only_considers_cluster_with_consent() -> None:
    auto = build_topology_candidates(_request(), (_node(), _node()), None)
    assert [candidate.topology for candidate in auto] == [Topology.SINGLE]
    with_consent = build_topology_candidates(
        _request(cluster_consent=True), (_node(), _node()), None
    )
    assert [candidate.topology for candidate in with_consent] == [
        Topology.SINGLE,
        Topology.CLUSTER,
    ]


def test_cluster_tier_profile_excludes_the_single_candidate() -> None:
    profile = ProfilePolicy(min_nodes=2)
    _refusal(
        lambda: build_topology_candidates(_request(topology=Topology.SINGLE), (_node(), _node()), profile),
        ReasonCode.HARDWARE_INCOMPATIBLE,
    )
    _refusal(
        lambda: build_topology_candidates(_request(), (_node(), _node()), profile),
        ReasonCode.CLUSTER_CONSENT_REQUIRED,
    )
    candidates = build_topology_candidates(
        _request(cluster_consent=True), (_node(), _node()), profile
    )
    assert [candidate.topology for candidate in candidates] == [Topology.CLUSTER]


# --- the resolver -----------------------------------------------------------------------


def _resolve(
    request: PlanRequest,
    *,
    hardware,
    main=None,
    vision=None,
    profile=None,
    runtime_version="0.32.0",
    **kwargs,
):
    return resolve_launch_plan(
        request,
        main_facts=main or _main_facts(),
        vision_facts=vision,
        hardware=hardware,
        profile=profile,
        runtime_version=runtime_version,
        image_digest="sha256:" + "0" * 64,
        kv_bytes=kwargs.pop("kv_bytes", int(1.0 * _GB)),
        missing_artifact_bytes=kwargs.pop("missing_artifact_bytes", 0),
    )


def test_happy_path_freezes_one_valid_single_node_plan() -> None:
    plan = _resolve(_request(), hardware=(_node(memory_gb=32.0),))
    assert plan.topology is Topology.SINGLE
    assert plan.photo_mode is PhotoMode.OFF
    assert plan.main_revision == "8b5e6c1f"
    assert plan.context_limit == GENERIC_CONTEXT_LIMIT
    assert plan.concurrency_limit == 1
    assert [item.node for item in plan.node_requirements] == ["primary"]
    assert plan.digest() == plan.digest()  # deterministic


def test_plan_digest_changes_with_the_plan() -> None:
    base = _resolve(_request(), hardware=(_node(memory_gb=32.0),))
    other = _resolve(
        _request(), hardware=(_node(memory_gb=32.0),), runtime_version="0.24.0"
    )
    assert base.digest() != other.digest()


def test_single_that_does_not_fit_uses_tp2_only_with_consent() -> None:
    # Per-node single demand (14.2 GB) exceeds the 20 GB node's 12 GB
    # available; the TP2 per-rank demand (9.1 GB + photo) fits.
    request = _request(
        vision_model=VISION,
        photo_mode=PhotoMode.SEPARATE,
        cluster_consent=True,
    )
    plan = _resolve(
        request,
        hardware=(_node("primary", memory_gb=24.0), _node("peer", memory_gb=24.0)),
        vision=_vision_facts(),
    )
    assert plan.topology is Topology.CLUSTER
    assert plan.photo_mode is PhotoMode.SEPARATE
    assert [item.node for item in plan.node_requirements] == ["primary", "peer"]


def test_combined_memory_never_implies_a_single_node_fit() -> None:
    # Two 20 GB nodes hold 40 GB combined, but the single-node demand does not
    # fit one node and TP2 needs consent - an AUTO request without consent
    # refuses instead of assuming.
    request = _request(vision_model=VISION, photo_mode=PhotoMode.SEPARATE)
    _refusal(
        lambda: _resolve(
            request,
            hardware=(_node("primary", memory_gb=20.0), _node("peer", memory_gb=20.0)),
            vision=_vision_facts(),
        ),
        ReasonCode.INSUFFICIENT_MEMORY,
    )


def test_unknown_evidence_never_fits() -> None:
    _refusal(
        lambda: _resolve(_request(), hardware=(_node(),), main=_main_facts(revision=None)),
        ReasonCode.INSUFFICIENT_EVIDENCE,
    )
    _refusal(
        lambda: _resolve(
            _request(), hardware=(_node(),), main=_main_facts(resident_weight_bytes=None)
        ),
        ReasonCode.INSUFFICIENT_EVIDENCE,
    )
    _refusal(
        lambda: _resolve(_request(), hardware=(_node(),), kv_bytes=None),
        ReasonCode.INSUFFICIENT_EVIDENCE,
    )
    _refusal(
        lambda: _resolve(
            _request(vision_model=VISION),
            hardware=(_node(memory_gb=32.0),),
            vision=_vision_facts(revision=None),
        ),
        ReasonCode.INSUFFICIENT_EVIDENCE,
    )


def test_refusal_never_mutates_anything() -> None:
    # The resolver is pure: a refusal raises, it never writes, launches, or
    # mutates its inputs.
    request = _request()
    facts = _main_facts(revision=None)
    node = _node()
    before = (request.photo_mode, request.topology, facts.revision, node.memory_gb)
    _refusal(
        lambda: _resolve(request, hardware=(node,), main=facts),
        ReasonCode.INSUFFICIENT_EVIDENCE,
    )
    assert (request.photo_mode, request.topology, facts.revision, node.memory_gb) == before

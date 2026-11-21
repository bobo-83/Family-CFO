"""Authoritative launch planning (ADR 0081 S2): pure resolution + admission.

No I/O and no side effects: model metadata, hardware facts, and reviewed
profiles arrive through the injected adapter interfaces (implemented by the
execution work item). The resolver is deterministic and total over its
inputs; every produced fact carries an Evidence classification, and unknown
required evidence refuses with insufficient_evidence - never zero, never
"fits", never a single-node assumption.

These are admission estimates, not an allocator simulator: exact inference
verification and recovery remain mandatory (ADR 0081 S3).

Importable without FastAPI/Pydantic (the CLI shares this engine).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from family_cfo_model_manager.contracts import (
    Evidence,
    NodeRequirement,
    PhotoMode,
    PlanSummary,
    ReasonCode,
    Topology,
)

# --- admission policy (plan: evidence classifications and memory policy) -------
WEIGHT_RESIDENCY_MARGIN = 0.15  # 15% safety margin on weight residency
MAIN_RANK_OVERHEAD_GB = 4.0  # per main rank overhead, minimum
PHOTO_INSTANCE_OVERHEAD_GB = 2.0  # per separate photo instance, minimum
NODE_RESERVE_FRACTION = 0.08  # reserve max(8 GiB, 8%) outside managed use
NODE_RESERVE_MIN_GB = 8.0
MAX_MANAGED_FRACTION = 0.92  # total managed fraction per node
DISK_DOWNLOAD_OVERHEAD = 0.10  # +10% on missing candidate artifacts
DISK_RESERVE_FRACTION = 0.05  # reserve max(5 GiB, 5% of the cache filesystem)
DISK_RESERVE_MIN_GB = 5.0
GENERIC_CONTEXT_LIMIT = 8192  # uncatalogued models: at most 8,192 tokens ...
GENERIC_CONCURRENCY_LIMIT = 1  # ... and one concurrent sequence

_GB = 1_000_000_000.0


class PlanningRefusal(Exception):
    """An actionable refusal naming the limiting resource or missing evidence."""

    def __init__(self, reason_code: ReasonCode, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


@dataclass(frozen=True, slots=True)
class ModelFacts:
    """Resolved metadata for one repository id (Hub/cache adapter output).

    resident_weight_bytes is the ACTIVE weight files of the selected
    format/index - never total repository storage, never a raw Hub U8 count
    read as uncompressed parameters. multimodal None means image-input
    support is unknown, not false.
    """

    repo_id: str
    revision: str | None = None
    architecture: str | None = None
    quantization: str | None = None
    resident_weight_bytes: int | None = None
    multimodal: bool | None = None
    model_context_limit: int | None = None
    evidence: Evidence = Evidence.UNKNOWN


@dataclass(frozen=True, slots=True)
class NodeHardware:
    """Observed facts for one node. Unified-memory hosts are ONE pool."""

    node: str
    memory_gb: float | None = None
    cache_filesystem_gb: float | None = None
    disk_free_gb: float | None = None
    # Memory already claimed by resident managed services on this node (e.g.
    # a live peer describer) - counted exactly once, on its own node.
    resident_managed_gb: float | None = None
    evidence: Evidence = Evidence.UNKNOWN


@dataclass(frozen=True, slots=True)
class ProfilePolicy:
    """One reviewed deployment profile (profiles.json).

    Overhead overrides may only RAISE the policy minimums; the resolver
    enforces that - a profile can never silently lower them.
    """

    repo_id: str | None = None
    architecture: str | None = None
    min_nodes: int = 1
    context_limit: int | None = None
    concurrency_limit: int | None = None
    tool_protocol: bool | None = None
    main_rank_overhead_gb: float | None = None
    photo_instance_overhead_gb: float | None = None


class ModelMetadataSource(Protocol):
    def facts_for(self, repo_id: str) -> ModelFacts: ...


class HardwareSource(Protocol):
    def nodes(self) -> tuple[NodeHardware, ...]: ...


class ProfileSource(Protocol):
    def policy_for(
        self, repo_id: str, architecture: str | None
    ) -> ProfilePolicy | None: ...


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """Normalized intent handed to the resolver (one user confirmation)."""

    request_id: str
    main_model: str
    vision_model: str | None
    photo_mode: PhotoMode
    topology: Topology
    cluster_consent: bool


@dataclass(frozen=True, slots=True)
class ResolvedPlacement:
    """Chat/photo placement after server-side normalization (never AUTO)."""

    main_model: str
    vision_model: str | None
    photo_mode: PhotoMode
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """The frozen, immutable resolution of one accepted request."""

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

    def summary(self) -> PlanSummary:
        return PlanSummary(
            main_model=self.main_model,
            main_revision=self.main_revision,
            vision_model=self.vision_model,
            vision_revision=self.vision_revision,
            photo_mode=self.photo_mode,
            topology=self.topology,
            runtime_version=self.runtime_version,
            image_digest=self.image_digest,
            context_limit=self.context_limit,
            concurrency_limit=self.concurrency_limit,
            node_requirements=self.node_requirements,
            warnings=self.warnings,
        )

    def digest(self) -> str:
        """Stable plan hash - the identity the receipt and journal freeze."""
        payload = {
            "main_model": self.main_model,
            "main_revision": self.main_revision,
            "vision_model": self.vision_model,
            "vision_revision": self.vision_revision,
            "photo_mode": self.photo_mode.value,
            "topology": self.topology.value,
            "runtime_version": self.runtime_version,
            "image_digest": self.image_digest,
            "context_limit": self.context_limit,
            "concurrency_limit": self.concurrency_limit,
            "node_requirements": [
                {
                    "node": item.node,
                    "memory_gb": item.memory_gb,
                    "memory_evidence": item.memory_evidence.value,
                    "disk_gb": item.disk_gb,
                    "disk_evidence": item.disk_evidence.value,
                }
                for item in self.node_requirements
            ],
            "warnings": list(self.warnings),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


# --- placement normalization ------------------------------------------------------


def _multimodal_refusal(role: str, facts: ModelFacts) -> PlanningRefusal:
    if facts.multimodal is False:
        return PlanningRefusal(
            ReasonCode.UNSUPPORTED_PHOTO_MODE,
            "'" + facts.repo_id + "' cannot accept image input (verified), so "
            "it cannot serve the " + role + " photo role",
        )
    return PlanningRefusal(
        ReasonCode.INSUFFICIENT_EVIDENCE,
        "image-input support of '" + facts.repo_id + "' is unknown, so the "
        + role + " photo role cannot be validated",
    )


def _resolved_main(request: PlanRequest) -> ResolvedPlacement:
    return ResolvedPlacement(
        main_model=request.main_model,
        vision_model=None,
        photo_mode=PhotoMode.MAIN,
    )


def normalize_photo_mode(
    request: PlanRequest,
    main_facts: ModelFacts,
    vision_facts: ModelFacts | None,
) -> ResolvedPlacement:
    """Server-resolved photo semantics (spec 04). Never an implicit default.

    Equal main/vision ids normalize to one instance with photo_mode=main;
    photo_mode=off with a named vision model is a contradiction, not a hint.
    Capability alone never overrides intent: the selected mode controls
    routing.
    """
    equal_ids = (
        request.vision_model is not None
        and request.vision_model == request.main_model
    )
    if equal_ids:
        if request.photo_mode is PhotoMode.OFF:
            # An explicit off wins over an equal-id photo request.
            return ResolvedPlacement(
                main_model=request.main_model,
                vision_model=None,
                photo_mode=PhotoMode.OFF,
            )
        if main_facts.multimodal is not True:
            raise _multimodal_refusal("main", main_facts)
        # Equal ids collapse to a single instance serving both roles.
        return _resolved_main(request)
    if request.photo_mode is PhotoMode.OFF:
        if request.vision_model is not None:
            raise PlanningRefusal(
                ReasonCode.UNSUPPORTED_PHOTO_MODE,
                "photo_mode=off contradicts a requested vision model",
            )
        return ResolvedPlacement(
            main_model=request.main_model,
            vision_model=None,
            photo_mode=PhotoMode.OFF,
        )
    if request.photo_mode is PhotoMode.MAIN:
        if request.vision_model is not None:
            raise PlanningRefusal(
                ReasonCode.UNSUPPORTED_PHOTO_MODE,
                "photo_mode=main contradicts a distinct requested vision model",
            )
        if main_facts.multimodal is not True:
            raise _multimodal_refusal("main", main_facts)
        return _resolved_main(request)
    if request.photo_mode is PhotoMode.SEPARATE:
        if request.vision_model is None:
            raise PlanningRefusal(
                ReasonCode.UNSUPPORTED_PHOTO_MODE,
                "a deliberate separate photo model requires a distinct "
                "vision model id",
            )
        if vision_facts is None or vision_facts.multimodal is not True:
            raise _multimodal_refusal("separate", vision_facts or main_facts)
        return ResolvedPlacement(
            main_model=request.main_model,
            vision_model=request.vision_model,
            photo_mode=PhotoMode.SEPARATE,
        )
    # AUTO: a distinct vision model is separate; none is main only when the
    # installed metadata VERIFIES multimodal support, else off.
    if request.vision_model is not None:
        if vision_facts is None or vision_facts.multimodal is not True:
            raise _multimodal_refusal("separate", vision_facts or main_facts)
        return ResolvedPlacement(
            main_model=request.main_model,
            vision_model=request.vision_model,
            photo_mode=PhotoMode.SEPARATE,
        )
    if main_facts.multimodal is True:
        return _resolved_main(request)
    return ResolvedPlacement(
        main_model=request.main_model,
        vision_model=None,
        photo_mode=PhotoMode.OFF,
    )


# --- admission math -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlacementDemand:
    """Managed memory demand of one placement on one node."""

    weights_gb: float
    overhead_gb: float
    kv_gb: float
    total_gb: float


def main_rank_overhead(profile: ProfilePolicy | None) -> float:
    if profile is None or profile.main_rank_overhead_gb is None:
        return MAIN_RANK_OVERHEAD_GB
    return max(MAIN_RANK_OVERHEAD_GB, profile.main_rank_overhead_gb)


def photo_instance_overhead(profile: ProfilePolicy | None) -> float:
    if profile is None or profile.photo_instance_overhead_gb is None:
        return PHOTO_INSTANCE_OVERHEAD_GB
    return max(PHOTO_INSTANCE_OVERHEAD_GB, profile.photo_instance_overhead_gb)


def placement_demand(
    *,
    main_weight_bytes: int,
    photo_weight_bytes: int | None,
    kv_bytes: int,
    tensor_parallel_size: int,
    profile: ProfilePolicy | None = None,
) -> PlacementDemand:
    """Per-placement memory demand - never a combined figure divided by two.

    Tensor parallelism shards weights and KV across ranks; rank overhead is
    replicated per rank. A separate photo instance is a full additional
    placement with its own overhead.
    """
    weights_gb = (main_weight_bytes / _GB) * (1.0 + WEIGHT_RESIDENCY_MARGIN)
    per_rank_weights = weights_gb / tensor_parallel_size
    per_rank_kv = (kv_bytes / _GB) / tensor_parallel_size
    overhead = main_rank_overhead(profile)
    photo = 0.0
    if photo_weight_bytes is not None:
        photo = (photo_weight_bytes / _GB) * (1.0 + WEIGHT_RESIDENCY_MARGIN)
        overhead += photo_instance_overhead(profile)
    return PlacementDemand(
        weights_gb=per_rank_weights,
        overhead_gb=overhead,
        kv_gb=per_rank_kv,
        total_gb=per_rank_weights + per_rank_kv + overhead + photo,
    )


def node_memory_verdict(
    node: NodeHardware, demand: PlacementDemand
) -> tuple[bool, str | None]:
    """(fits, refusal reason) for one node's unified memory pool.

    The node reserve must hold independently AND the managed fraction must
    stay <= 0.92 after rounding UP to the next percentage point; rounding
    that invalidates the plan is a refusal, not a fudge.
    """
    if node.memory_gb is None:
        return False, ReasonCode.INSUFFICIENT_EVIDENCE.value
    reserve = max(NODE_RESERVE_MIN_GB, NODE_RESERVE_FRACTION * node.memory_gb)
    resident = node.resident_managed_gb or 0.0
    available = node.memory_gb - reserve - resident
    if demand.total_gb > available:
        return False, ReasonCode.INSUFFICIENT_MEMORY.value
    fraction = demand.total_gb / node.memory_gb
    rounded = math.ceil(fraction * 100.0) / 100.0
    if rounded > MAX_MANAGED_FRACTION:
        return False, ReasonCode.INSUFFICIENT_MEMORY.value
    if rounded * node.memory_gb < demand.total_gb:
        return False, ReasonCode.INSUFFICIENT_MEMORY.value
    return True, None


def disk_demand_gb(
    missing_artifact_bytes: int | None, node: NodeHardware
) -> tuple[float | None, str | None]:
    """Required cache-disk headroom: missing artifacts +10%, plus the reserve.

    Returns (requirement, refusal); a None requirement means the evidence to
    even compute it was missing - fail closed, never zero.
    """
    if missing_artifact_bytes is None:
        return None, ReasonCode.INSUFFICIENT_EVIDENCE.value
    if node.cache_filesystem_gb is None:
        return None, ReasonCode.INSUFFICIENT_EVIDENCE.value
    reserve = max(
        DISK_RESERVE_MIN_GB, DISK_RESERVE_FRACTION * node.cache_filesystem_gb
    )
    return (
        (missing_artifact_bytes / _GB) * (1.0 + DISK_DOWNLOAD_OVERHEAD) + reserve,
        None,
    )


def node_disk_verdict(
    node: NodeHardware, missing_artifact_bytes: int | None
) -> tuple[bool, str | None]:
    requirement, refusal = disk_demand_gb(missing_artifact_bytes, node)
    if refusal is not None:
        return False, refusal
    assert requirement is not None
    if node.disk_free_gb is None:
        return False, ReasonCode.INSUFFICIENT_EVIDENCE.value
    if node.disk_free_gb < requirement:
        return False, ReasonCode.INSUFFICIENT_DISK.value
    return True, None


# --- limits ------------------------------------------------------------------------


def effective_limits(
    profile: ProfilePolicy | None, model_context_limit: int | None
) -> tuple[int, int]:
    """(context_limit, concurrency_limit), conservative for uncatalogued models.

    A model without a reviewed profile gets at most 8,192 context tokens and
    one concurrent sequence - without the cap the KV estimate (and therefore
    every memory verdict) is undefined. Reviewed profiles may specify other
    values, still bounded by the model's own limit when that is known.
    """
    context = GENERIC_CONTEXT_LIMIT
    concurrency = GENERIC_CONCURRENCY_LIMIT
    if profile is not None:
        if profile.context_limit is not None:
            context = profile.context_limit
        if profile.concurrency_limit is not None:
            concurrency = profile.concurrency_limit
    if model_context_limit is not None:
        context = min(context, model_context_limit)
    return context, concurrency


# --- topology candidates -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TopologyCandidate:
    topology: Topology
    nodes: tuple[NodeHardware, ...]
    tensor_parallel_size: int


def build_topology_candidates(
    request: PlanRequest,
    hardware: tuple[NodeHardware, ...],
    profile: ProfilePolicy | None,
) -> tuple[TopologyCandidate, ...]:
    """Candidate topologies in preference order (single preferred).

    AUTO prefers a valid single-node plan and only considers TP2-over-Ray
    when the API-supplied consent is true - topology never grants cluster
    permission. A reviewed min_nodes>=2 policy excludes the single-node
    candidate. An explicit single prohibits automatic escalation.
    """
    if len(hardware) < 1:
        raise PlanningRefusal(
            ReasonCode.HARDWARE_INCOMPATIBLE, "no managed node is available"
        )
    cluster_tier = profile is not None and profile.min_nodes >= 2
    single = TopologyCandidate(
        topology=Topology.SINGLE, nodes=(hardware[0],), tensor_parallel_size=1
    )
    cluster = TopologyCandidate(
        topology=Topology.CLUSTER,
        nodes=(hardware[0], hardware[-1]),
        tensor_parallel_size=2,
    )
    if request.topology is Topology.SINGLE:
        if cluster_tier:
            raise PlanningRefusal(
                ReasonCode.HARDWARE_INCOMPATIBLE,
                "the reviewed profile requires at least two nodes; a "
                "single-node plan is not a candidate",
            )
        return (single,)
    if request.topology is Topology.CLUSTER:
        if not request.cluster_consent:
            raise PlanningRefusal(
                ReasonCode.CLUSTER_CONSENT_REQUIRED,
                "cluster serving requires the household's cluster consent",
            )
        if len(hardware) < 2:
            raise PlanningRefusal(
                ReasonCode.CLUSTER_PEER_UNREACHABLE,
                "the declared peer is not available for a two-node plan",
            )
        return (cluster,)
    # AUTO: single preferred; TP2 considered only with consent.
    if cluster_tier:
        if not request.cluster_consent:
            raise PlanningRefusal(
                ReasonCode.CLUSTER_CONSENT_REQUIRED,
                "this model requires the two-node topology; enable the "
                "household's cluster consent first",
            )
        if len(hardware) < 2:
            raise PlanningRefusal(
                ReasonCode.CLUSTER_PEER_UNREACHABLE,
                "this model requires the two-node topology but the peer is "
                "not available",
            )
        return (cluster,)
    if request.cluster_consent and len(hardware) >= 2:
        return (single, cluster)
    return (single,)


# --- the resolver ---------------------------------------------------------------------


def resolve_launch_plan(
    request: PlanRequest,
    *,
    main_facts: ModelFacts,
    vision_facts: ModelFacts | None,
    hardware: tuple[NodeHardware, ...],
    profile: ProfilePolicy | None,
    runtime_version: str,
    image_digest: str,
    kv_bytes: int | None,
    missing_artifact_bytes: int | None = None,
) -> LaunchPlan:
    """Resolve and freeze ONE valid plan, or raise an actionable refusal.

    Never silently reduces context, drops photos, changes quantization, or
    upgrades an image to force a fit. Unknown required evidence refuses with
    insufficient_evidence.
    """
    if main_facts.revision is None:
        raise PlanningRefusal(
            ReasonCode.INSUFFICIENT_EVIDENCE,
            "no immutable revision could be resolved for '"
            + main_facts.repo_id
            + "'",
        )
    if main_facts.resident_weight_bytes is None:
        raise PlanningRefusal(
            ReasonCode.INSUFFICIENT_EVIDENCE,
            "no resident-weight evidence for '"
            + main_facts.repo_id
            + "'; memory fit cannot be estimated",
        )
    if kv_bytes is None:
        raise PlanningRefusal(
            ReasonCode.INSUFFICIENT_EVIDENCE,
            "no KV-cache estimate for the model's cache layout; memory fit "
            "cannot be estimated",
        )
    vision_revision: str | None = None
    if request.vision_model is not None:
        if vision_facts is None:
            raise PlanningRefusal(
                ReasonCode.MODEL_NOT_FOUND,
                "no metadata for the requested vision model '"
                + request.vision_model
                + "'",
            )
        if vision_facts.revision is None:
            raise PlanningRefusal(
                ReasonCode.INSUFFICIENT_EVIDENCE,
                "no immutable revision could be resolved for '"
                + vision_facts.repo_id
                + "'",
            )
        if vision_facts.resident_weight_bytes is None:
            raise PlanningRefusal(
                ReasonCode.INSUFFICIENT_EVIDENCE,
                "no resident-weight evidence for '" + vision_facts.repo_id + "'",
            )
        vision_revision = vision_facts.revision

    placement = normalize_photo_mode(request, main_facts, vision_facts)
    candidates = build_topology_candidates(request, hardware, profile)

    context_limit, concurrency_limit = effective_limits(
        profile, main_facts.model_context_limit
    )
    photo_weight_bytes: int | None = None
    if placement.photo_mode is PhotoMode.SEPARATE and vision_facts is not None:
        photo_weight_bytes = vision_facts.resident_weight_bytes
    refusals: list[str] = []
    for candidate in candidates:
        outcome = _evaluate_candidate(
            request=request,
            placement=placement,
            candidate=candidate,
            main_revision=main_facts.revision or "",
            main_weight_bytes=main_facts.resident_weight_bytes or 0,
            photo_weight_bytes=photo_weight_bytes,
            kv_bytes=kv_bytes,
            missing_artifact_bytes=missing_artifact_bytes,
            profile=profile,
            context_limit=context_limit,
            concurrency_limit=concurrency_limit,
            runtime_version=runtime_version,
            image_digest=image_digest,
            vision_revision=vision_revision,
        )
        if isinstance(outcome, LaunchPlan):
            return outcome
        refusals.append(outcome)
    if any(r == ReasonCode.INSUFFICIENT_MEMORY.value for r in refusals):
        raise PlanningRefusal(
            ReasonCode.INSUFFICIENT_MEMORY, "; ".join(refusals)
        )
    if any(r == ReasonCode.INSUFFICIENT_DISK.value for r in refusals):
        raise PlanningRefusal(ReasonCode.INSUFFICIENT_DISK, "; ".join(refusals))
    raise PlanningRefusal(
        ReasonCode.HARDWARE_INCOMPATIBLE,
        "; ".join(refusals) or "no valid topology candidate",
    )


def _evaluate_candidate(
    *,
    request: PlanRequest,
    placement: ResolvedPlacement,
    candidate: TopologyCandidate,
    main_revision: str,
    main_weight_bytes: int,
    photo_weight_bytes: int | None,
    kv_bytes: int,
    missing_artifact_bytes: int | None,
    profile: ProfilePolicy | None,
    context_limit: int,
    concurrency_limit: int,
    runtime_version: str,
    image_digest: str,
    vision_revision: str | None,
) -> LaunchPlan | str:
    """Evaluate one candidate; a LaunchPlan when every node admits, else the
    stable refusal reason."""
    demand = placement_demand(
        main_weight_bytes=main_weight_bytes,
        photo_weight_bytes=photo_weight_bytes,
        kv_bytes=kv_bytes,
        tensor_parallel_size=candidate.tensor_parallel_size,
        profile=profile,
    )
    requirements: list[NodeRequirement] = []
    for node in candidate.nodes:
        fits, reason = node_memory_verdict(node, demand)
        if not fits:
            assert reason is not None
            return reason
        disk_fits, disk_reason = node_disk_verdict(node, missing_artifact_bytes)
        if not disk_fits:
            assert disk_reason is not None
            return disk_reason
        disk_requirement, _ = disk_demand_gb(missing_artifact_bytes or 0, node)
        requirements.append(
            NodeRequirement(
                node=node.node,
                memory_gb=round(
                    demand.total_gb + (node.resident_managed_gb or 0.0), 3
                ),
                # The fit is an admission estimate by nature (predicted
                # weights, KV demand, overhead); only missing evidence
                # demotes it further, to unknown.
                memory_evidence=(
                    Evidence.UNKNOWN
                    if node.evidence is Evidence.UNKNOWN
                    else Evidence.ESTIMATED
                ),
                disk_gb=round(disk_requirement or 0.0, 3),
                disk_evidence=(
                    Evidence.UNKNOWN
                    if node.evidence is Evidence.UNKNOWN
                    else (
                        Evidence.ESTIMATED if missing_artifact_bytes else Evidence.VERIFIED
                    )
                ),
            )
        )
    return LaunchPlan(
        main_model=request.main_model,
        main_revision=main_revision,
        vision_model=placement.vision_model,
        vision_revision=vision_revision,
        photo_mode=placement.photo_mode,
        topology=candidate.topology,
        runtime_version=runtime_version,
        image_digest=image_digest,
        context_limit=context_limit,
        concurrency_limit=concurrency_limit,
        node_requirements=tuple(requirements),
        warnings=placement.warnings,
    )


# --- reviewed profiles -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfilesDocument:
    profiles: tuple[ProfilePolicy, ...]

    def policy_for(
        self, repo_id: str, architecture: str | None
    ) -> ProfilePolicy | None:
        """Exact repo-id match first, then architecture match."""
        for profile in self.profiles:
            if profile.repo_id is not None and profile.repo_id == repo_id:
                return profile
        if architecture is None:
            return None
        for profile in self.profiles:
            if profile.repo_id is None and profile.architecture == architecture:
                return profile
        return None


class JsonProfiles:
    """ProfileSource over a loaded ProfilesDocument (or profiles.json path)."""

    def __init__(self, document: ProfilesDocument) -> None:
        self._document = document

    def policy_for(
        self, repo_id: str, architecture: str | None
    ) -> ProfilePolicy | None:
        return self._document.policy_for(repo_id, architecture)


PROFILES_SCHEMA_VERSION = 1

_ALLOWED_PROFILE_KEYS = {
    "repo_id",
    "architecture",
    "min_nodes",
    "context_limit",
    "concurrency_limit",
    "tool_protocol",
    "main_rank_overhead_gb",
    "photo_instance_overhead_gb",
}


def load_profiles(path: Path | str) -> ProfilesDocument:
    """Load profiles.json; unknown keys or a wrong schema fail closed."""
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != PROFILES_SCHEMA_VERSION:
        raise ValueError("profiles.json has an unsupported schema version")
    entries = raw.get("profiles")
    if not isinstance(entries, list):
        raise TypeError("profiles.json must carry a profiles list")
    profiles: list[ProfilePolicy] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - _ALLOWED_PROFILE_KEYS:
            raise ValueError("profiles.json contains an unknown profile key")
        profiles.append(
            ProfilePolicy(
                repo_id=entry.get("repo_id"),
                architecture=entry.get("architecture"),
                min_nodes=int(entry.get("min_nodes", 1)),
                context_limit=entry.get("context_limit"),
                concurrency_limit=entry.get("concurrency_limit"),
                tool_protocol=entry.get("tool_protocol"),
                main_rank_overhead_gb=entry.get("main_rank_overhead_gb"),
                photo_instance_overhead_gb=entry.get("photo_instance_overhead_gb"),
            )
        )
    return ProfilesDocument(profiles=tuple(profiles))

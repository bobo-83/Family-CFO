"""Manager-side projections: dependency construction and job-status reads.

Kept separate from the FastAPI facade so the CLI shares the exact same
dependency wiring and read semantics (ADR 0081 S10). Importable without
FastAPI/Pydantic.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from family_cfo_model_manager import deployment
from family_cfo_model_manager import executor as executor_module
from family_cfo_model_manager.adapters import HubMetadataSource, LocalHardwareSource
from family_cfo_model_manager.contracts import (
    PhotoMode,
    SelectionSummary,
    Topology,
)
from family_cfo_model_manager.journal import Journal
from family_cfo_model_manager.planning import ProfilesDocument, load_profiles
from family_cfo_model_manager.contracts import JobStatus


def build_manager_deps(
    project_dir: str, *, journal: Journal | None = None
) -> executor_module.ExecutorDeps:
    """The real dependency set (Docker/Hub/SSH/HTTP), shared by the manager
    service and the host-side CLI."""
    from pathlib import Path

    package_root = Path(__file__).resolve().parent
    return executor_module.ExecutorDeps(
        journal=journal or Journal(os.path.join(project_dir, ".runtime-swap", "v2")),
        compose=deployment.DockerComposeAdapter(),
        http=executor_module.UrllibRuntimeHttp(),
        runner=executor_module.DockerContainerRunner(),
        profiles=_profiles(package_root),
        metadata=HubMetadataSource(),
        hardware=LocalHardwareSource(),
        peer=deployment.SshPeerInspector(),
        project_dir=project_dir,
        cache_probe=executor_module._docker_cache_probe,
    )


def _profiles(package_root: Any) -> Any:
    path = package_root / "profiles.json"
    document: ProfilesDocument = load_profiles(path)
    return document


def job_status_of(
    journal: Journal, job_id: str | None = None
) -> JobStatus | None:
    """The read-only job projection: an explicit job, the current pointer's
    job, else the latest retained record. None means no job at all."""
    record = None
    if job_id is not None:
        record = journal.read_job_record(job_id)
    else:
        pointer = journal.read_current()
        if pointer.job_id is not None:
            record = journal.read_job_record(pointer.job_id)
        if record is None:
            records = journal.list_job_records()
            record = records[-1] if records else None
    if record is None:
        return None

    target = journal.read_target_plan(record.job_id) or {}
    previous = journal.read_previous_snapshot(record.job_id)
    evidence = journal.read_verification_evidence(record.job_id) or {}
    requested = target.get("request_selection") or {}
    plan = target.get("plan") or {}

    resolved = None
    if plan:
        from family_cfo_model_manager.contracts import (
            NodeRequirement,
            PlanSummary,
        )
        from family_cfo_model_manager.contracts import Evidence

        resolved = PlanSummary(
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
            node_requirements=tuple(
                NodeRequirement(
                    node=item["node"],
                    memory_gb=float(item["memory_gb"]),
                    memory_evidence=Evidence(item["memory_evidence"]),
                    disk_gb=float(item["disk_gb"]),
                    disk_evidence=Evidence(item["disk_evidence"]),
                )
                for item in plan.get("node_requirements", [])
            ),
            warnings=tuple(plan.get("warnings", ())),
        )

    previous_selection = None
    if previous:
        previous_selection = SelectionSummary(
            main_model=previous.get("main_model") or "",
            vision_model=previous.get("vision_model"),
            photo_mode=PhotoMode(previous.get("photo_mode", "off")),
            topology=Topology(previous.get("topology", "single")),
        )

    verified_at = None
    raw_verified = evidence.get("verified_at")
    if raw_verified:
        try:
            verified_at = datetime.fromisoformat(raw_verified)
        except ValueError:
            verified_at = None

    return JobStatus(
        job_id=record.job_id,
        request_id=record.request_id,
        sequence=record.sequence,
        state=record.state,
        decision=record.decision,
        origin=record.origin,
        requested_selection=_requested_selection(requested, plan),
        resolved_plan=resolved,
        previous_selection=previous_selection,
        created_at=record.created_at,
        updated_at=record.updated_at,
        deadline_at=record.phase_deadline_at,
        reason_code=record.failure_code,
        message=record.safe_message,
        recovery_available=record.state.recoverable,
        verified_at=verified_at,
        # The frozen identity + initiator attribution (issue #170 item 4).
        runtime_key=record.runtime_key,
        target_plan_hash=record.target_plan_hash,
        initiator_household_id=record.initiator_household_id,
        initiator_user_id=record.initiator_user_id,
    )


def _requested_selection(requested: dict[str, Any], plan: dict[str, Any]) -> SelectionSummary | None:
    if not requested and not plan:
        return None
    if requested:
        # The NORMALIZED selection (contracts.py): photo_mode is resolved —
        # never AUTO — once a plan exists; topology stays the requested value.
        photo_mode = requested.get("photo_mode", "auto")
        if plan.get("photo_mode"):
            photo_mode = plan["photo_mode"]
        return SelectionSummary(
            main_model=requested.get("main_model", ""),
            vision_model=requested.get("vision_model"),
            photo_mode=PhotoMode(photo_mode),
            topology=Topology(requested.get("topology", "auto")),
        )
    # Fall back to the resolved plan's selection when only it is retained.
    return SelectionSummary(
        main_model=plan.get("main_model", ""),
        vision_model=plan.get("vision_model"),
        photo_mode=PhotoMode(plan.get("photo_mode", "off")),
        topology=Topology(plan.get("topology", "single")),
    )

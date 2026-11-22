"""Wire-contract compatibility (ADR 0074 Decision 4; ADR 0081 S6).

Manager-side replay: the promoted protocol-2 contract fixture replays against
the current manager with every fixture field preserved (additive evolution is
allowed, removal is not). The v1 MUTATION surface is deliberately broken: a
previous API against a v2 manager gets 426 upgrade-required and performs NO
mutation. v1 READ surfaces keep their shape across rolling deploys and
rollback.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from family_cfo_model_manager import main as manager
from conftest import FakeCompose
from family_cfo_model_manager.contracts import JobDecision, JobState
from conftest import make_deps, previous_runtime_services, verify_ok_http, write_previous_runtime_env

_ROOT = Path(__file__).resolve().parents[3]
V2: dict[str, Any] = json.loads(
    (_ROOT / "shared/schemas/model-manager-v2-contract.json").read_text()
)["operations"]
V1: dict[str, Any] = json.loads(
    (_ROOT / "shared/schemas/model-manager-v1-contract.json").read_text()
)["operations"]

CREDENTIAL = "synthetic-deployment-credential"
HEADER = {"X-Manager-Credential": CREDENTIAL}


def _assert_previous_shape(actual: Any, previous: Any) -> None:
    """Current responses may add fields but must preserve old fields and types."""
    if isinstance(previous, dict):
        assert isinstance(actual, dict), f"expected an object, got {type(actual)}"
        for key, value in previous.items():
            assert key in actual, f"contract field {key!r} disappeared"
            _assert_previous_shape(actual[key], value)
    elif previous is not None:
        assert type(actual) is type(previous)


@pytest.fixture
def replay(env_project, monkeypatch):
    """The manager app wired to synthetic engine fakes (no real Docker)."""
    os.environ["FAMILY_CFO_MANAGER_CREDENTIAL"] = CREDENTIAL
    # The fixture scenario replaces a running previous deployment, so stage
    # exactly that: a verified 7B+vision baseline the admission can adopt.
    write_previous_runtime_env(env_project)
    deps = make_deps(
        env_project,
        compose=FakeCompose(services=previous_runtime_services()),
        http=verify_ok_http(),
    )
    spawned: list[tuple[str, int]] = []
    object.__setattr__(
        deps, "spawn", lambda deps_, job_id, fd, **kw: spawned.append((job_id, fd))
    )
    monkeypatch.setattr(manager, "PROJECT_DIR", str(env_project))
    monkeypatch.setattr(manager, "_deps", lambda: deps)
    with patch.object(manager, "_run_recovery_scan", lambda: None):
        with TestClient(manager.app) as test_client:
            yield test_client, deps, spawned


def test_v2_contract_replays(replay) -> None:
    client, deps, spawned = replay

    # Capabilities: protocol + journal schema advertised.
    capabilities = V2["capabilities"]
    response = client.request(capabilities["method"], capabilities["path"])
    assert response.status_code == 200
    _assert_previous_shape(response.json(), capabilities["response"])

    # Admission through the real engine (synthetic fakes).
    admit = V2["admit_job"]
    response = client.request(
        admit["method"], admit["path"], json=admit["request"], headers=HEADER
    )
    assert response.status_code == 202
    _assert_previous_shape(response.json(), admit["response"])
    job_id = response.json()["job_id"]

    # The executor durably stamps the phase deadline when it starts staging;
    # the fake spawn skips that, so stage it exactly where the executor would.
    with deps.journal.locked():
        stored = deps.journal.read_job_record(job_id)
        deps.journal.write_job_record(
            dataclasses.replace(stored, phase_deadline_at=stored.created_at)
        )

    # Read surfaces.
    get_job = V2["get_job"]
    response = client.get(f"/v2/jobs/{job_id}")
    assert response.status_code == 200
    _assert_previous_shape(response.json(), get_job["response"])

    current = V2["current_job"]
    response = client.get(current["path"])
    assert response.status_code == 200
    _assert_previous_shape(response.json(), current["response"])

    # Durable, monotonic decision.
    decision = V2["decision"]
    response = client.post(
        f"/v2/jobs/{job_id}/decision", json=decision["request"], headers=HEADER
    )
    assert response.status_code == 200
    _assert_previous_shape(response.json(), decision["response"])

    # Acknowledgement records the receipt idempotently. The executor
    # finalization runs out of band, so the test durably moves the job to
    # commit_pending first - exactly where the real executor leaves it.
    with deps.journal.locked():
        stored = deps.journal.read_job_record(job_id)
        deps.journal.write_job_record(
            dataclasses.replace(
                stored, state=JobState.COMMIT_PENDING, decision=JobDecision.COMMIT
            )
        )
    ack = V2["ack"]
    body = dict(ack["request"])
    body["job_id"] = job_id
    body["receipt_job_id"] = job_id
    response = client.post(f"/v2/jobs/{job_id}/ack", json=body, headers=HEADER)
    assert response.status_code == 200
    _assert_previous_shape(response.json(), ack["response"])

    # Recovery: a deliberately failed job returns to restoring. (The fake
    # spawn records the lock fd the route already closed in its finally, so
    # the deployment lock is free for the recover call to take.)
    with deps.journal.locked():
        stored = deps.journal.read_job_record(job_id)
        deps.journal.write_job_record(
            dataclasses.replace(
                stored, state=JobState.RECOVERY_FAILED, decision=JobDecision.RESTORE
            )
        )
    recover = V2["recover"]
    response = client.post(f"/v2/jobs/{job_id}/recover", headers=HEADER)
    assert response.status_code == 200
    _assert_previous_shape(response.json(), recover["response"])


def test_v1_mutation_is_deliberately_broken(replay, env_project) -> None:
    """POST /swap from a previous API: 426, and NOTHING is mutated."""
    client, deps, spawned = replay
    before_env = (env_project / ".env").read_bytes()
    before_journal = sorted(
        p.name for p in (env_project / ".runtime-swap" / "v2").rglob("*")
    )
    swap = V1["swap"]
    response = client.request(swap["method"], swap["path"], json=swap["request"])
    assert response.status_code == 426
    assert response.json()["error"]["code"] == "manager_upgrade_required"
    assert (env_project / ".env").read_bytes() == before_env
    assert (
        sorted(p.name for p in (env_project / ".runtime-swap" / "v2").rglob("*"))
        == before_journal
    )
    assert deps.journal.list_job_records() == ()


def test_v1_read_surfaces_keep_their_shape(replay) -> None:
    client, deps, _spawned = replay

    # /status replays the v1 shape from a succeeded job (a prior apply won).
    with deps.journal.locked():
        from conftest import JOB_ID, MAIN, record

        deps.journal.write_job_record(record(job_id=JOB_ID, state=JobState.SUCCEEDED))
        deps.journal.write_target_plan(
            JOB_ID,
            {"request_selection": {"main_model": MAIN, "vision_model": None}},
        )
        deps.journal.set_current_job(JOB_ID)
    status = V1["status"]
    response = client.request(status["method"], status["path"])
    assert response.status_code == 200
    _assert_previous_shape(response.json(), status["response"])

    # /logs replays the v1 shape.
    logs = V1["logs"]
    process = type(
        "Result",
        (),
        {"returncode": 0, "stdout": logs["response"]["lines"], "stderr": ""},
    )()
    with patch.object(manager.subprocess, "run", return_value=process):
        response = client.request(logs["method"], logs["path"], params=logs["query"])
    assert response.status_code == 200
    _assert_previous_shape(response.json(), logs["response"])

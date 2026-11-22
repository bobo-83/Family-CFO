"""v2 manager routes (issue #170 Item 3): admission, reads, decision/ack/
recover, credential enforcement, legacy refusal, projection, metrics."""

from __future__ import annotations

import json
import dataclasses
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from family_cfo_model_manager import main as manager
from family_cfo_model_manager import executor as executor_module
from family_cfo_model_manager.contracts import JobDecision, JobState
from conftest import (
    FakeCompose,
    FakeHttp,
    FakeRunner,
    JOB_ID,
    MAIN,
    REQUEST_ID,
    VISION,
    make_deps,
    record,
    service,
)

CREDENTIAL = "synthetic-deployment-credential"
HEADER = {"X-Manager-Credential": CREDENTIAL}


def _admission_body(**overrides):
    body = {
        "request_id": REQUEST_ID,
        "main_model": MAIN,
        "vision_model": None,
        "photo_mode": "auto",
        "topology": "auto",
        "cluster_consent": False,
        "origin": "api",
        "runtime_key": "vllm+http://api:8000/v1",
        "initiator_household_id": "0b0a0908-0706-0504-0302-0100ffeeddcc",
        "initiator_user_id": "0b0a0908-0706-0504-0302-0100ffeedccb",
    }
    body.update(overrides)
    return body


@pytest.fixture
def client(env_project, monkeypatch):
    os.environ["FAMILY_CFO_MANAGER_CREDENTIAL"] = CREDENTIAL
    deps = make_deps(env_project)
    spawned = []
    # Never spawn a real executor process from the HTTP tests.
    object.__setattr__(deps, "spawn", lambda deps_, job_id, fd, **kw: spawned.append((job_id, fd)))
    monkeypatch.setattr(manager, "PROJECT_DIR", str(env_project))
    monkeypatch.setattr(manager, "_deps", lambda: deps)
    with patch.object(manager, "_run_recovery_scan", lambda: None):
        client = TestClient(manager.app)
    yield client


# --- capabilities ---------------------------------------------------------------


def test_capabilities_reports_protocol_major_and_schema(client):
    response = client.get("/v2/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["protocol_major"] == 2
    assert body["journal_schema"] == 2
    assert isinstance(body["build"], str)


# --- admission -------------------------------------------------------------------


def test_admission_requires_the_deployment_credential(client):
    response = client.post("/v2/jobs", json=_admission_body())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert client.post(
        "/v2/jobs", json=_admission_body(), headers={"X-Manager-Credential": "wrong"}
    ).status_code == 401


def test_unconfigured_credential_fails_closed(client, monkeypatch):
    monkeypatch.delenv("FAMILY_CFO_MANAGER_CREDENTIAL")
    response = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
    assert response.status_code == 401


def test_admission_rejects_malformed_model_ids(client):
    for value in ("../../etc/passwd", "no-slash", "a/b; rm -rf /", "a b/c"):
        response = client.post(
            "/v2/jobs", json=_admission_body(main_model=value), headers=HEADER
        )
        assert response.status_code == 422, value
        assert response.json()["error"]["code"] == "invalid_model_id"


def test_admission_rejects_a_malformed_request_id(client):
    response = client.post(
        "/v2/jobs", json=_admission_body(request_id="not-a-uuid"), headers=HEADER
    )
    assert response.status_code == 422


def test_cli_origin_jobs_carry_no_household_identity(client):
    response = client.post(
        "/v2/jobs",
        json=_admission_body(origin="cli", initiator_household_id="0b0a0908-0706-0504-0302-0100ffeeddcc"),
        headers=HEADER,
    )
    assert response.status_code == 422


def test_admission_validates_resolves_and_starts_the_executor(client, env_project):
    # A consistent, verified previous deployment: .env and the running
    # services agree, and the runtime answers the baseline probe sequence.
    (env_project / ".env").write_text(
        "SYNTHETIC_FIXTURE=1\n"
        "VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct\n"
        "VLLM_TOOL_PARSER=hermes\n"
        "VLLM_REASONING_PARSER=qwen3\n"
        "VLLM_GPU_FRACTION=0.60\n"
        "VLLM_VISION_GPU_FRACTION=0.20\n"
        "VLLM_VISION_MODEL=Qwen/Qwen2.5-VL-3B-Instruct\n"
        "VLLM_MAX_MODEL_LEN=32768\n"
        "FAMILY_CFO_AI_SUPPORTS_VISION=false\n"
        "FAMILY_CFO_AI_VISION_ENABLED=true\n"
    )
    running = (
        service(
            "vllm",
            command="exec vllm serve 'Qwen/Qwen2.5-7B-Instruct' --enable-auto-tool-choice "
            "--tool-call-parser 'hermes' --reasoning-parser 'qwen3' "
            "--gpu-memory-utilization '0.60' --max-model-len '32768'",
        ),
        service(
            "vllm-vision",
            command="--model 'Qwen/Qwen2.5-VL-3B-Instruct' --gpu-memory-utilization '0.20' --max-model-len '8192'",
        ),
        service(
            "api",
            env={
                "FAMILY_CFO_AI_MODEL": "Qwen/Qwen2.5-7B-Instruct",
                "FAMILY_CFO_AI_SUPPORTS_VISION": "false",
                "FAMILY_CFO_AI_VISION_ENABLED": "true",
                "FAMILY_CFO_AI_VISION_MODEL": "Qwen/Qwen2.5-VL-3B-Instruct",
            },
        ),
    )

    def http_handler(method: str, url: str, payload):
        if url.endswith("/health"):
            return 200, {"status": "ok"}
        if url.endswith("/v1/models"):
            return 200, {"data": [{"id": "Qwen/Qwen2.5-7B-Instruct"}]}
        if method == "POST" and isinstance(payload, dict):
            if "tools" in payload:
                return 200, {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "verify_noop",
                                            "arguments": "{\"note\": \"ok\"}",
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            if isinstance(payload.get("messages", [{}])[0].get("content"), list):
                return 200, {"choices": [{"message": {"content": "A tiny image."}}]}
            return 200, {"choices": [{"message": {"content": "OK"}}]}
        return None

    runner = FakeRunner()
    runner.results = [
        type("R", (), {"exit_code": 0, "stdout": json.dumps({"version": "0.32.0"}), "stderr": ""})(),
        type("R", (), {"exit_code": 0, "stdout": json.dumps({"supported": True, "backend": "vllm-native", "architectures": ["Qwen2ForCausalLM"]}), "stderr": ""})(),
    ]
    deps = make_deps(
        env_project,
        runner=runner,
        compose=FakeCompose(services=running),
        http=FakeHttp(handler=http_handler),
    )
    with patch.object(manager, "_deps", lambda: deps):
        response = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["state"] == "accepted"
    assert body["decision"] == "undecided"
    assert body["requested_selection"]["main_model"] == MAIN
    assert body["previous_selection"]["main_model"] == "Qwen/Qwen2.5-7B-Instruct"
    assert body["previous_selection"]["photo_mode"] == "separate"
    # The checker ran from the exact image (never a mutable tag).
    assert any("ModelConfig(" in spec.command[0] for spec in runner.specs)


def test_admission_is_idempotent_by_request_id(client):
    first = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
    assert first.status_code == 202
    job_id = first.json()["job_id"]
    replay = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
    assert replay.status_code == 202
    assert replay.json()["job_id"] == job_id
    conflicting = client.post(
        "/v2/jobs", json=_admission_body(main_model=VISION), headers=HEADER
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "request_id_conflict"


def test_unfinished_job_blocks_admission(client):
    assert client.post("/v2/jobs", json=_admission_body(), headers=HEADER).status_code == 202
    second = client.post(
        "/v2/jobs",
        json=_admission_body(request_id="0f0e1d2c-3b4a-5968-7789-abcdef012346"),
        headers=HEADER,
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "swap_in_progress"


def test_recovery_required_blocks_admission(client, env_project):
    deps = make_deps(env_project)
    journal = deps.journal
    journal.write_job_record(
        record(state=JobState.RECOVERY_FAILED, decision=JobDecision.RESTORE)
    )
    with patch.object(manager, "_deps", lambda: deps):
        response = client.post(
            "/v2/jobs",
            json=_admission_body(request_id="0f0e1d2c-3b4a-5968-7789-abcdef012346"),
            headers=HEADER,
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "recovery_required"


# --- reads ---------------------------------------------------------------------


def test_get_job_by_id_and_current(client):
    first = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
    job_id = first.json()["job_id"]
    assert client.get(f"/v2/jobs/{job_id}").status_code == 200
    assert client.get("/v2/jobs/current").json()["job_id"] == job_id
    unknown = client.get("/v2/jobs/2a1b1c1d-1e1f-2a2b-3c3d-4e4f5a5b5c5d")
    assert unknown.status_code == 404
    # Reads are read-only: no credential needed, no mutation.
    assert client.get(f"/v2/jobs/{job_id}").json()["sequence"] == first.json()["sequence"]


# --- decision / ack / recover ------------------------------------------------------


def test_decision_is_monotonic_and_idempotent(client):
    first = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
    job_id = first.json()["job_id"]
    deps = manager._deps()
    with deps.journal.locked():
        stored = deps.journal.read_job_record(job_id)
        deps.journal.write_job_record(
            dataclasses.replace(
                stored, state=JobState.COMMIT_PENDING, decision=JobDecision.COMMIT
            )
        )
    replay = client.post(f"/v2/jobs/{job_id}/decision", json={"decision": "commit"}, headers=HEADER)
    assert replay.status_code == 200
    opposing = client.post(f"/v2/jobs/{job_id}/decision", json={"decision": "restore"}, headers=HEADER)
    assert opposing.status_code == 409


def test_ack_records_the_receipt_idempotently(client):
    first = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
    job_id = first.json()["job_id"]
    deps = manager._deps()
    with deps.journal.locked():
        stored = deps.journal.read_job_record(job_id)
        deps.journal.write_job_record(
            dataclasses.replace(
                stored, state=JobState.COMMIT_PENDING, decision=JobDecision.COMMIT
            )
        )
    body = {"job_id": job_id, "receipt_job_id": job_id, "plan_hash": "ab" * 32}
    first_ack = client.post(f"/v2/jobs/{job_id}/ack", json=body, headers=HEADER)
    assert first_ack.status_code == 200
    replay = client.post(f"/v2/jobs/{job_id}/ack", json=body, headers=HEADER)
    assert replay.status_code == 200
    assert manager._deps().journal.read_job_record(job_id).config_receipt_id == job_id
    # A different receipt conflicts; a restore decision cannot be acked.
    wrong = client.post(
        f"/v2/jobs/{job_id}/ack",
        json={**body, "receipt_job_id": "2a1b1c1d-1e1f-2a2b-3c3d-4e4f5a5b5c5d"},
        headers=HEADER,
    )
    assert wrong.status_code == 409


def test_recover_reruns_the_frozen_direction(client, env_project):
    deps = make_deps(env_project)
    journal = deps.journal
    journal.write_job_record(
        record(job_id=JOB_ID, state=JobState.RECOVERY_FAILED, decision=JobDecision.RESTORE)
    )
    spawned = []
    with (
        patch.object(manager, "_deps", lambda: deps),
        patch.object(executor_module, "_spawn_executor", lambda deps, job_id, fd, **kw: spawned.append(job_id)),
    ):
        response = client.post(f"/v2/jobs/{JOB_ID}/recover", headers=HEADER)
    assert response.status_code == 200
    assert response.json()["state"] == "restoring"
    assert spawned == [JOB_ID]
    # A healthy job is not recoverable.
    healthy = client.post(f"/v2/jobs/{JOB_ID}/recover", headers=HEADER)
    assert healthy.status_code == 409


# --- legacy refusal ----------------------------------------------------------------


def test_legacy_swap_returns_upgrade_required_and_mutates_nothing(client, env_project):
    before = (env_project / ".env").read_bytes()
    journal_before = sorted(p.name for p in (env_project / ".runtime-swap" / "v2").rglob("*"))
    response = client.post("/swap", json={"main_model": MAIN, "vision_model": None})
    assert response.status_code == 426
    assert response.json()["error"]["code"] == "manager_upgrade_required"
    assert (env_project / ".env").read_bytes() == before
    assert sorted(p.name for p in (env_project / ".runtime-swap" / "v2").rglob("*")) == journal_before


# --- legacy projection + metrics ------------------------------------------------------


def test_status_projects_the_new_lifecycle(client, env_project):
    # No job: idle.
    assert client.get("/status").json()["state"] == "idle"
    deps = manager._deps()
    with deps.journal.locked():
        deps.journal.write_job_record(
            record(job_id=JOB_ID, state=JobState.VERIFYING)
        )
        deps.journal.write_target_plan(
            JOB_ID,
            {
                "request_selection": {
                    "main_model": MAIN,
                    "vision_model": None,
                    "photo_mode": "auto",
                    "topology": "auto",
                },
            },
        )
        deps.journal.set_current_job(JOB_ID)
    running = client.get("/status").json()
    assert running["state"] == "running"
    assert running["main_model"] == MAIN
    # commit_pending also projects to running (the Item 2 resolution).
    with deps.journal.locked():
        stored = deps.journal.read_job_record(JOB_ID)
        deps.journal.write_job_record(
            dataclasses.replace(stored, state=JobState.COMMIT_PENDING)
        )
    assert client.get("/status").json()["state"] == "running"
    # restored projects to failed.
    with deps.journal.locked():
        stored = deps.journal.read_job_record(JOB_ID)
        deps.journal.write_job_record(
            dataclasses.replace(stored, state=JobState.RESTORED)
        )
    assert client.get("/status").json()["state"] == "failed"


def test_metrics_carry_the_apply_state_gauge(client):
    body = client.get("/metrics").text
    assert 'family_cfo_apply_state{state="idle",origin="none"} 1' in body
    assert "family_cfo_apply_blocked 0" in body
    deps = manager._deps()
    with deps.journal.locked():
        deps.journal.write_job_record(record(job_id=JOB_ID, state=JobState.STAGING))
        deps.journal.set_current_job(JOB_ID)
    body = client.get("/metrics").text
    assert 'family_cfo_apply_state{state="staging",origin="api"} 1' in body
    assert "family_cfo_apply_blocked 1" in body


def test_journal_corruption_fails_closed_on_status(client, env_project):
    corrupt = env_project / ".runtime-swap" / "v2" / "current.json"
    corrupt.write_text("{not json")
    response = client.get("/status")
    assert response.status_code == 503

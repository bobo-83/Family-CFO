"""The deployment seam: .env edits, drift, snapshots, launch values, restore,
peer inspection allowlist, and the structural parses (ADR 0081 S2/S3)."""

from __future__ import annotations

import os
import stat
import subprocess

import pytest

from family_cfo_model_manager import deployment
from family_cfo_model_manager.contracts import (
    Evidence,
    NodeRequirement,
    PhotoMode,
    ReasonCode,
    Topology,
)
from family_cfo_model_manager.deployment import (
    DeploymentRefusal,
    EnvFile,
    assert_no_drift,
    capture_snapshot,
    compose_files_for,
    empty_baseline_restore,
    image_repository_of,
    launch_values,
    parse_gpu_memory,
    parse_ray_status,
    restore_runtime,
    runtime_key_hash,
    runtime_values_from_running,
    scale_for,
)
from family_cfo_model_manager.planning import LaunchPlan
from conftest import FakeCompose, IMAGE_ID, MAIN, VISION, service

RAY_TWO_ALIVE = (
    "node_1:  1 nodes\n    1 node_c0a1b2c3 ALIVE\n"
    "node_2:  1 nodes\n    2 node_d4e5f6a7 ALIVE\n"
)


# --- EnvFile ------------------------------------------------------------------------


def test_envfile_reads_values_comments_and_quotes(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# a comment\n"
        "VLLM_MODEL='Qwen/Qwen2.5-14B-Instruct'\n"
        'VLLM_MAX_MODEL_LEN="8192"\n'
        "VLLM_GPU_FRACTION=0.85\n"
        "UNRELATED_KEY=keep\n"
        "\n"
    )
    env = EnvFile.read(env_path)
    assert env.value("VLLM_MODEL") == "Qwen/Qwen2.5-14B-Instruct"
    assert env.value("VLLM_MAX_MODEL_LEN") == "8192"
    assert env.value("VLLM_GPU_FRACTION") == "0.85"
    assert env.value("MISSING") is None
    assert "UNRELATED_KEY" in env.keys()


def test_envfile_missing_file_is_empty(tmp_path):
    env = EnvFile.read(tmp_path / "absent.env")
    assert env.lines == ()
    assert env.runtime_values() == {key: None for key in deployment.RUNTIME_ENV_KEYS}


def test_write_runtime_keys_updates_adds_and_preserves_everything_else(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# top comment\n"
        "VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct\n"
        "UNRELATED=keepme\n"
        "VLLM_VISION_MODEL=Qwen/Qwen2.5-VL-3B-Instruct\n"
    )
    env = EnvFile.read(env_path)
    env.write_runtime_keys(
        env_path,
        {
            "VLLM_MODEL": MAIN,  # updated in place
            "VLLM_GPU_FRACTION": "0.85",  # appended
            "VLLM_VISION_MODEL": None,  # removed
        },
    )
    content = env_path.read_text()
    lines = content.splitlines()
    assert lines[0] == "# top comment"  # comments preserved
    assert "UNRELATED=keepme" in lines  # unrelated keys preserved
    assert lines.index(f"VLLM_MODEL={MAIN}") < lines.index("UNRELATED=keepme")
    assert "VLLM_GPU_FRACTION=0.85" in lines  # added at the end
    assert not any(line.startswith("VLLM_VISION_MODEL=") for line in lines)  # removed
    assert content.count("VLLM_MODEL=") == 1  # never duplicated
    # The in-memory file is stale after an external write; re-read for checks.
    assert EnvFile.read(env_path).value("VLLM_VISION_MODEL") is None


def test_write_runtime_keys_preserves_mode_and_ownership(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("VLLM_MODEL=old\n")
    os.chmod(env_path, 0o640)
    if os.geteuid() != 0:  # chown to a sibling uid only works as root
        env = EnvFile.read(env_path)
        env.write_runtime_keys(env_path, {"VLLM_MODEL": MAIN})
        mode = stat.S_IMODE(env_path.stat().st_mode)
        assert mode == 0o640
        # Ownership preserved trivially (same user); assert uid unchanged.
        assert env_path.stat().st_uid == os.geteuid()
    else:
        os.chown(env_path, 1000, 1000)
        env = EnvFile.read(env_path)
        env.write_runtime_keys(env_path, {"VLLM_MODEL": MAIN})
        assert env_path.stat().st_uid == 1000
        assert env_path.stat().st_gid == 1000


def test_write_runtime_keys_is_atomic_no_temp_litter(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("VLLM_MODEL=old\n")
    env = EnvFile.read(env_path)
    env.write_runtime_keys(env_path, {"VLLM_MODEL": MAIN})
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != ".env"]
    assert leftovers == []  # the temp file was replaced, not left behind
    assert EnvFile.read(env_path).value("VLLM_MODEL") == MAIN


def test_runtime_key_hash_is_order_stable_and_credential_free():
    a = runtime_key_hash({"VLLM_MODEL": MAIN, "VLLM_GPU_FRACTION": "0.85"})
    b = runtime_key_hash({"VLLM_GPU_FRACTION": "0.85", "VLLM_MODEL": MAIN})
    assert a == b
    assert a != runtime_key_hash({"VLLM_MODEL": MAIN})
    assert a != runtime_key_hash({"VLLM_MODEL": MAIN, "VLLM_GPU_FRACTION": "0.90"})
    # No credential can enter: the allowlist has none (spot-check the keys).
    assert not any("PASSWORD" in key or "TOKEN" in key or "SECRET" in key for key in deployment.RUNTIME_ENV_KEYS)


# --- drift --------------------------------------------------------------------------


def test_drift_detection_refuses_mismatched_and_lost_keys():
    base = {key: None for key in deployment.RUNTIME_ENV_KEYS}
    base.update({"VLLM_MODEL": MAIN})
    # Running value differs from the file.
    env_file = EnvFile(lines=("VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct\n",))
    with pytest.raises(DeploymentRefusal) as exc_info:
        assert_no_drift(env_file, base)
    assert exc_info.value.reason_code is ReasonCode.DEPLOYMENT_DRIFT

    # A key in .env but absent from the running deployment (file ahead is only
    # legitimate for revision pins).
    env_file2 = EnvFile(lines=("VLLM_GPU_FRACTION=0.85\n",))
    with pytest.raises(DeploymentRefusal):
        assert_no_drift(env_file2, {key: None for key in deployment.RUNTIME_ENV_KEYS})

    # Revision pins ARE allowed to be ahead of an old deployment.
    env_file3 = EnvFile(lines=("VLLM_REVISION=8b5e6c1f\n",))
    assert_no_drift(env_file3, {key: None for key in deployment.RUNTIME_ENV_KEYS})

    # Agreement passes.
    env_file4 = EnvFile(lines=(f"VLLM_MODEL={MAIN}\n",))
    assert_no_drift(env_file4, base)

    # A key ABSENT from .env is never drift: the compose files render their
    # own ${VAR:-default} fallback for an unset key (this is exactly the
    # state a photo-off swap leaves the describer keys in).
    assert_no_drift(
        EnvFile(lines=(f"VLLM_MODEL={MAIN}\n",)),
        {**base, "VLLM_VISION_MODEL": "Qwen/Qwen2.5-VL-7B-Instruct"},
    )


# --- running-deployment mapping -------------------------------------------------------


def test_runtime_values_from_running_parses_the_known_command_shapes():
    services = (
        service(
            "vllm",
            running=True,
            command=(
                f"exec vllm serve '{MAIN}' --tool-call-parser 'hermes' "
                "--reasoning-parser 'deepseek_r1' --gpu-memory-utilization '0.85' "
                "--max-model-len '8192' --max-num-seqs '4' --served-model-name extra"
            ),
        ),
        service(
            "vllm-vision",
            running=True,
            scale=1,
            command=f"exec vllm serve --model '{VISION}' --gpu-memory-utilization '0.20'",
        ),
        service("api", running=True, env={"FAMILY_CFO_AI_SUPPORTS_VISION": "true"}),
    )
    values = runtime_values_from_running(services)
    assert values["VLLM_MODEL"] == MAIN
    assert values["VLLM_TOOL_PARSER"] == "hermes"
    assert values["VLLM_REASONING_PARSER"] == "deepseek_r1"
    assert values["VLLM_GPU_FRACTION"] == "0.85"
    assert values["VLLM_MAX_MODEL_LEN"] == "8192"
    assert values["VLLM_MAX_NUM_SEQS"] == "4"
    assert values["VLLM_VISION_MODEL"] == VISION
    assert values["VLLM_VISION_GPU_FRACTION"] == "0.20"
    assert values["FAMILY_CFO_AI_VISION_ENABLED"] == "true"
    assert values["FAMILY_CFO_AI_SUPPORTS_VISION"] == "true"


def test_runtime_values_from_running_leaves_the_seq_cap_unparsed_when_absent():
    # A deployment predating the seq cap parses None - never a guessed value.
    values = runtime_values_from_running(
        (service("vllm", running=True, command=f"exec vllm serve '{MAIN}' --max-model-len '8192'"),)
    )
    assert values["VLLM_MAX_NUM_SEQS"] is None


# --- snapshots ------------------------------------------------------------------------


def test_snapshot_round_trip_preserves_identity(tmp_path):
    compose = FakeCompose(
        services=(
            service(
                "vllm",
                running=True,
                image_id=IMAGE_ID,
                command=f"exec vllm serve '{MAIN}' --gpu-memory-utilization '0.85'",
                env={"HF_HOME": "/models"},
            ),
            service("api", running=True, image_id=IMAGE_ID, env={}),
        )
    )
    env_file = EnvFile(lines=("VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct\n",))
    snapshot = capture_snapshot(
        compose,
        str(tmp_path),
        env_file,
        topology=Topology.SINGLE,
        photo_mode=PhotoMode.OFF,
    )
    # The env file's value is authoritative when both exist.
    assert snapshot.main_model == "Qwen/Qwen2.5-7B-Instruct"
    document = snapshot.to_dict()
    restored = deployment.DeploymentSnapshot.from_dict(document)
    assert restored == snapshot
    assert restored.service("vllm").image_id == IMAGE_ID
    assert restored.service("missing") is None
    assert restored.selection_summary() == (snapshot.main_model, snapshot.vision_model)


def test_snapshot_env_takes_precedence_over_running_values(tmp_path):
    compose = FakeCompose(
        services=(
            service(
                "vllm",
                running=True,
                image_id=IMAGE_ID,
                command="exec vllm serve 'Qwen/Qwen2.5-7B-Instruct' --gpu-memory-utilization '0.85'",
            ),
        )
    )
    env_file = EnvFile(lines=(f"VLLM_MODEL={MAIN}\n",))
    snapshot = capture_snapshot(
        compose, str(tmp_path), env_file, topology=Topology.SINGLE, photo_mode=PhotoMode.OFF
    )
    assert snapshot.main_model == MAIN


def test_cluster_topology_requires_the_overlay(tmp_path):
    with pytest.raises(DeploymentRefusal) as exc_info:
        compose_files_for(str(tmp_path), Topology.CLUSTER)
    assert exc_info.value.reason_code is ReasonCode.CLUSTER_PEER_UNREACHABLE
    (tmp_path / "docker-compose.cluster.yml").write_text("services: {}\n")
    files = compose_files_for(str(tmp_path), Topology.CLUSTER)
    assert files == ("docker-compose.yml", "docker-compose.cluster.yml")


def test_compose_files_follow_the_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPOSE_FILES", "docker-compose.yml docker-compose.cluster.yml")
    files = compose_files_for(str(tmp_path), Topology.SINGLE)
    assert files == ("docker-compose.yml", "docker-compose.cluster.yml")
    monkeypatch.delenv("COMPOSE_FILES")
    assert compose_files_for(str(tmp_path), Topology.SINGLE) == ("docker-compose.yml",)


# --- launch values + scale -------------------------------------------------------------


def _plan(**overrides) -> LaunchPlan:
    fields = {
        "main_model": MAIN,
        "main_revision": "8b5e6c1f",
        "vision_model": None,
        "vision_revision": None,
        "photo_mode": PhotoMode.OFF,
        "topology": Topology.SINGLE,
        "runtime_version": "0.32.0",
        "image_digest": IMAGE_ID,
        "context_limit": 8192,
        "concurrency_limit": 1,
        "tool_call_parser": "hermes",
        "reasoning_parser": None,
        "node_requirements": (
            NodeRequirement(
                node="primary",
                memory_gb=21.392,
                memory_evidence=Evidence.ESTIMATED,
                disk_gb=33.8,
                disk_evidence=Evidence.ESTIMATED,
            ),
        ),
    }
    fields.update(overrides)
    return LaunchPlan(**fields)


def test_launch_values_install_the_frozen_plan():
    values = launch_values(
        _plan(), node_memory_gb=64.0, photo_node_memory_gb=None, profile=None
    )
    assert values["VLLM_MODEL"] == MAIN
    assert values["VLLM_MAX_MODEL_LEN"] == "8192"
    assert values["VLLM_TOOL_PARSER"] == "hermes"
    assert values["FAMILY_CFO_AI_SUPPORTS_VISION"] == "false"
    assert values["FAMILY_CFO_AI_VISION_ENABLED"] == "false"
    assert values["VLLM_VISION_MODEL"] is None
    assert values["VLLM_MAX_NUM_SEQS"] == "1"  # the generic admission cap
    assert values["VLLM_EXTRA_ARGS"] is None
    assert values["VLLM_GPU_FRACTION"] is not None  # assigned from the node memory


def test_launch_values_freeze_the_planned_concurrency_cap():
    # The Qwen profile's reviewed cap (4 seqs): the launch must pin exactly
    # the concurrency the admission memory estimate assumed.
    values = launch_values(
        _plan(concurrency_limit=4), node_memory_gb=64.0, photo_node_memory_gb=None, profile=None
    )
    assert values["VLLM_MAX_NUM_SEQS"] == "4"


def test_launch_values_separate_photo_installs_the_describer_keys():
    plan = _plan(
        photo_mode=PhotoMode.SEPARATE,
        vision_model=VISION,
        vision_revision="4a2d9e07",
        vision_demand_gb=6.0,
    )
    values = launch_values(
        plan, node_memory_gb=64.0, photo_node_memory_gb=32.0, profile=None
    )
    assert values["VLLM_VISION_MODEL"] == VISION
    assert values["VLLM_VISION_REVISION"] == "4a2d9e07"
    assert values["VLLM_VISION_GPU_FRACTION"] is not None
    assert values["FAMILY_CFO_AI_VISION_ENABLED"] == "true"


def test_launch_values_cluster_adds_tp2_and_single_strips_it():
    cluster_values = launch_values(
        _plan(topology=Topology.CLUSTER),
        node_memory_gb=64.0,
        photo_node_memory_gb=None,
        profile=None,
    )
    assert "--tensor-parallel-size=2" in cluster_values["VLLM_EXTRA_ARGS"]
    assert "--distributed-executor-backend=ray" in cluster_values["VLLM_EXTRA_ARGS"]

    single_values = launch_values(
        _plan(),
        node_memory_gb=64.0,
        photo_node_memory_gb=None,
        profile=None,
    )
    assert single_values["VLLM_EXTRA_ARGS"] is None

    # A single-node plan must never inherit a TP2 argument from a profile.
    from family_cfo_model_manager.planning import ProfilePolicy

    profile = ProfilePolicy(
        repo_id=MAIN, min_nodes=1, extra_args="--tensor-parallel-size=2 --seed 42"
    )
    stripped = launch_values(
        _plan(), node_memory_gb=64.0, photo_node_memory_gb=None, profile=profile
    )
    assert "--tensor-parallel-size" not in (stripped["VLLM_EXTRA_ARGS"] or "")
    assert "--seed 42" in stripped["VLLM_EXTRA_ARGS"]


def test_scale_for_follows_the_photo_mode():
    assert scale_for(_plan(photo_mode=PhotoMode.OFF)) == {"vllm": 1, "vllm-vision": 0}
    assert scale_for(_plan(photo_mode=PhotoMode.MAIN)) == {"vllm": 1, "vllm-vision": 0}
    assert scale_for(_plan(photo_mode=PhotoMode.SEPARATE)) == {"vllm": 1, "vllm-vision": 1}


# --- launch + restore ------------------------------------------------------------------


def test_launch_target_recreates_only_the_managed_services(tmp_path):
    compose = FakeCompose()
    plan = _plan(photo_mode=PhotoMode.OFF)
    deployment.launch_target(compose, str(tmp_path), plan, ("docker-compose.yml",))
    assert compose.up_calls == [(("vllm", "vllm-vision"), {"vllm": 1, "vllm-vision": 0}, False)]


def test_restore_runtime_writes_keys_including_removals_and_recreates(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"VLLM_MODEL={MAIN}\nVLLM_GPU_FRACTION=0.85\nVLLM_VISION_MODEL={VISION}\n"
    )
    env_file = EnvFile.read(env_path)
    snapshot = deployment.DeploymentSnapshot(
        runtime_env={key: None for key in deployment.RUNTIME_ENV_KEYS}
        | {"VLLM_MODEL": "Qwen/Qwen2.5-7B-Instruct"},
        services=(
            service("vllm", running=True, image_id=IMAGE_ID, scale=1),
            service("vllm-vision", running=False, image_id=IMAGE_ID, scale=0),
        ),
        compose_files=("docker-compose.yml",),
        topology=Topology.SINGLE,
        photo_mode=PhotoMode.OFF,
        main_model="Qwen/Qwen2.5-7B-Instruct",
        vision_model=None,
        model_cache_mount="model_cache",
        declared_peer=None,
    )
    compose = FakeCompose()
    restore_runtime(compose, str(tmp_path), snapshot, env_file, env_path)
    content = EnvFile.read(env_path)
    assert content.value("VLLM_MODEL") == "Qwen/Qwen2.5-7B-Instruct"
    assert content.value("VLLM_GPU_FRACTION") is None  # snapshot removal restored
    assert content.value("VLLM_VISION_MODEL") is None
    assert compose.up_calls == [(("vllm", "vllm-vision"), {"vllm": 1, "vllm-vision": 0}, False)]


def test_empty_baseline_restore_stops_services_and_removes_added_keys(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct\nUNRELATED=keep\n")
    env_file = EnvFile.read(env_path)
    compose = FakeCompose()
    empty_baseline_restore(
        compose,
        str(tmp_path),
        ("docker-compose.yml",),
        ("VLLM_MODEL", "VLLM_GPU_FRACTION"),
        env_file,
        env_path,
    )
    assert compose.stop_calls == [("vllm", "vllm-vision")]
    content = EnvFile.read(env_path)
    assert content.value("VLLM_MODEL") is None  # added key removed
    assert content.value("UNRELATED") == "keep"  # untouched


# --- image repository ------------------------------------------------------------------


def test_image_repository_of_strips_tag_digest_and_keeps_registry_ports():
    assert image_repository_of("vllm/vllm-openai:0.32.0") == "vllm/vllm-openai"
    assert image_repository_of("vllm/vllm-openai@sha256:" + "0" * 64) == "vllm/vllm-openai"
    assert image_repository_of("localhost:5000/vllm/vllm-openai:latest") == "localhost:5000/vllm/vllm-openai"
    assert image_repository_of("vllm/vllm-openai") == "vllm/vllm-openai"


# --- peer inspection -------------------------------------------------------------------


def test_ssh_peer_inspector_allowlist_and_malformed_hosts(monkeypatch):
    inspector = deployment.SshPeerInspector(connect_timeout=5)
    with pytest.raises(DeploymentRefusal) as exc_info:
        inspector.probe("peer-1", "rm -rf /")
    assert exc_info.value.reason_code is ReasonCode.HARDWARE_INCOMPATIBLE

    with pytest.raises(DeploymentRefusal) as exc_info:
        inspector.probe("bad host; shutdown", "ray_status")
    assert exc_info.value.reason_code is ReasonCode.CLUSTER_PEER_UNREACHABLE


def test_ssh_peer_inspector_runs_only_allowlisted_commands(monkeypatch):
    seen = {}

    def fake_run(command, capture_output, text, timeout):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout=RAY_TWO_ALIVE, stderr="")

    monkeypatch.setattr(deployment.subprocess, "run", fake_run)
    inspector = deployment.SshPeerInspector(connect_timeout=7)
    output = inspector.probe("peer-1.example", "ray_status")
    assert output == RAY_TWO_ALIVE
    assert seen["command"][0] == "ssh"
    assert "-o" in seen["command"] and "BatchMode=yes" in seen["command"]
    assert "StrictHostKeyChecking=yes" in seen["command"]
    assert "ConnectTimeout=7" in seen["command"]
    assert seen["command"][-2] == "peer-1.example"
    assert seen["command"][-1] == "ray status"


def test_ssh_peer_inspector_maps_failures_to_refusals(monkeypatch):
    def timeout_run(command, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(command, timeout)

    inspector = deployment.SshPeerInspector()
    monkeypatch.setattr(deployment.subprocess, "run", timeout_run)
    with pytest.raises(DeploymentRefusal) as exc_info:
        inspector.probe("peer-1", "gpu_memory")
    assert "timed out" in exc_info.value.message

    def failing_run(command, capture_output, text, timeout):
        return subprocess.CompletedProcess(command, 255, stdout="", stderr="no route")

    monkeypatch.setattr(deployment.subprocess, "run", failing_run)
    with pytest.raises(DeploymentRefusal) as exc_info:
        inspector.probe("peer-1", "gpu_memory")
    assert "exit 255" in exc_info.value.message


# --- structural parses ------------------------------------------------------------------


def test_parse_ray_status_counts_alive_and_total():
    assert parse_ray_status(RAY_TWO_ALIVE) == {"alive": 2, "total": 2}
    assert parse_ray_status("1 node_abc ALIVE\n1 node_def DEAD\n") == {"alive": 1, "total": 2}
    assert parse_ray_status("2 node(s) alive") == {"alive": 2, "total": 2}
    with pytest.raises(DeploymentRefusal):
        parse_ray_status("gibberish")


def test_parse_gpu_memory_returns_per_gpu_pairs():
    assert parse_gpu_memory("1024, 8192\n512, 8192\n") == ((1024, 8192), (512, 8192))
    with pytest.raises(DeploymentRefusal):
        parse_gpu_memory("no numbers here")
    with pytest.raises(DeploymentRefusal):
        parse_gpu_memory("1000, notanumber\n")

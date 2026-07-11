from unittest.mock import patch

from fastapi.testclient import TestClient

from family_cfo_model_manager import main as manager


def _client() -> TestClient:
    manager._status = manager.SwapStatus(state="idle")
    return TestClient(manager.app)


def test_rejects_malformed_repo_ids() -> None:
    client = _client()
    bad = ["../../etc/passwd", "no-slash", "a/b; rm -rf /", "a b/c", "-x/y"]
    for value in bad:
        resp = client.post("/swap", json={"main_model": value})
        assert resp.status_code == 422, value


def test_swap_runs_script_with_validated_args() -> None:
    client = _client()
    with patch.object(manager.threading, "Thread") as thread:
        resp = client.post(
            "/swap",
            json={"main_model": "Qwen/Qwen2.5-14B-Instruct", "vision_model": "Qwen/Qwen2.5-VL-3B-Instruct"},
        )
        assert resp.status_code == 202
        assert resp.json()["state"] == "running"
        args = thread.call_args.kwargs["args"]
        assert args == ("Qwen/Qwen2.5-14B-Instruct", "Qwen/Qwen2.5-VL-3B-Instruct")


def test_concurrent_swap_conflicts() -> None:
    client = _client()
    with patch.object(manager.threading, "Thread"):
        assert client.post("/swap", json={"main_model": "Qwen/Qwen2.5-7B-Instruct"}).status_code == 202
        assert client.post("/swap", json={"main_model": "Qwen/Qwen2.5-7B-Instruct"}).status_code == 409


def test_run_swap_records_success_and_log() -> None:
    manager._status = manager.SwapStatus(state="running", main_model="m")
    fake = type("R", (), {"returncode": 0, "stdout": "swapped ok", "stderr": ""})()
    with patch.object(manager.subprocess, "run", return_value=fake) as run:
        manager._run_swap("Qwen/Qwen2.5-14B-Instruct", None)
        cmd = run.call_args.args[0]
        assert cmd == ["bash", "scripts/swap-model.sh", "Qwen/Qwen2.5-14B-Instruct", "none"]
    assert manager._status.state == "succeeded"
    assert "swapped ok" in manager._status.log_tail


def test_vision_capable_main_gets_no_second_arg() -> None:
    fake = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    with patch.object(manager.subprocess, "run", return_value=fake) as run:
        manager._run_swap("Qwen/Qwen2.5-VL-32B-Instruct", None)
        assert run.call_args.args[0] == ["bash", "scripts/swap-model.sh", "Qwen/Qwen2.5-VL-32B-Instruct"]


# --- M50: read-only log access -----------------------------------------------


def test_logs_rejects_unknown_services() -> None:
    client = TestClient(manager.app)
    assert client.get("/logs", params={"service": "db"}).status_code == 422
    assert client.get("/logs", params={"service": "vllm; rm -rf /"}).status_code == 422


def test_logs_runs_compose_logs_for_allowlisted_service() -> None:
    fake = type("R", (), {"returncode": 0, "stdout": "line1\nline2", "stderr": ""})()
    with patch.object(manager.subprocess, "run", return_value=fake) as run:
        client = TestClient(manager.app)
        resp = client.get("/logs", params={"service": "vllm", "tail": 500})
        assert resp.status_code == 200
        assert resp.json()["lines"] == "line1\nline2"
        cmd = run.call_args.args[0]
        # Tail clamped to the max; only the fixed, allowlisted service name.
        assert cmd == ["docker", "compose", "logs", "--no-color", "--tail", "200", "vllm"]


def test_metrics_renders_container_families() -> None:
    """M67: up gauge + restarts counter per compose service, Prometheus text."""
    ps = type("R", (), {"returncode": 0, "stdout": "abc123\ndef456\n", "stderr": ""})()
    inspect = type(
        "R",
        (),
        {
            "returncode": 0,
            "stdout": "vllm|true|0\nvllm-vision|false|7\n",
            "stderr": "",
        },
    )()
    with patch.object(manager.subprocess, "run", side_effect=[ps, inspect]):
        client = TestClient(manager.app)
        resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert 'family_cfo_container_up{service="vllm"} 1' in body
    assert 'family_cfo_container_up{service="vllm-vision"} 0' in body
    assert 'family_cfo_container_restarts{service="vllm-vision"} 7' in body
    assert resp.headers["content-type"].startswith("text/plain")


def test_metrics_503_when_docker_fails() -> None:
    fail = type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()
    with patch.object(manager.subprocess, "run", return_value=fail):
        client = TestClient(manager.app)
        assert client.get("/metrics").status_code == 503

"""Real swap-script/manager integration, offline except the explicit Docker gate.

Run: apps/api/.venv/bin/python -m pytest scripts/tests/test_swap_model.py

The engine (services/model-manager) is exercised END TO END through the
operator entry point: `scripts/swap-model.sh` delegates to the shared
Python engine, which talks to a stub `docker` emulating the exact
compose/inspect/run/exec surface the engine uses. A local HTTP server
plays the managed runtime (health/models/probes) and the application api
health; the manager sidecar runs as a real uvicorn subprocess and takes
the API's role of committing + acknowledging. The only real-Docker test
is the bind-mount flock gate (preloaded python:3.12-slim; CI pulls it
explicitly and sets FAMILY_CFO_REQUIRE_DOCKER=1).
"""

import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[2]
CREDENTIAL = "synthetic-manager-credential"
MAIN_SHA = "a" * 40
NEW_SHA = "b" * 40
VISION_SHA = "c" * 40
DEFAULT_VISION_SHA = "d" * 40
DEFAULT_VISION_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
IMAGE_ID = "sha256:" + "1" * 64


def eventually(predicate, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(0.05)
    raise AssertionError("timed out waiting for synthetic swap")


# --- offline model-cache fixture ---------------------------------------------------------


def _seed_model(cache_root: Path, repo_id: str, sha: str, vision: bool, total_size: int) -> None:
    snapshot = cache_root / ("models--" + repo_id.replace("/", "--")) / "snapshots" / sha
    snapshot.mkdir(parents=True, exist_ok=True)
    config = {
        "architectures": ["SyntheticArchForCausalLM"],
        "num_hidden_layers": 2,
        "num_key_value_heads": 2,
        "head_dim": 64,
        "hidden_size": 256,
        "num_attention_heads": 4,
        "torch_dtype": "bfloat16",
        "max_position_embeddings": 32768,
    }
    if vision:
        config["vision_config"] = {"hidden_size": 64}
    (snapshot / "config.json").write_text(json.dumps(config))
    (snapshot / "model.safetensors.index.json").write_text(
        json.dumps({"metadata": {"total_size": total_size}, "weight_map": {}})
    )


def seed_model_cache(project: Path) -> None:
    """Small synthetic checkpoints so admission fits ANY host (a 0.5 GB
    main/describer), plus a vision-capable main, the retired script's
    default describer, and one model that cannot fit anywhere. With
    HF_HUB_OFFLINE the cache is the verified metadata source. Every model
    the suite swaps to is seeded; only the deliberate not-found case is
    absent."""
    hub = project / "hf-home" / "hub"
    _seed_model(hub, "synthetic/Old-7B", MAIN_SHA, False, 500_000_000)
    _seed_model(hub, "synthetic/New-7B", NEW_SHA, False, 500_000_000)
    _seed_model(hub, "synthetic/Vision-3B", VISION_SHA, True, 300_000_000)
    _seed_model(hub, DEFAULT_VISION_MODEL, DEFAULT_VISION_SHA, True, 300_000_000)
    _seed_model(hub, "synthetic/Main-VL-7B", NEW_SHA, True, 500_000_000)
    _seed_model(hub, "synthetic/Huge-999B", NEW_SHA, False, 999_000_000_000)
    # Per-scenario identities: distinct names keep the recorded calls and
    # .env assertions unambiguous across the contention/retry matrix.
    for name in (
        "Dashboard-7B",
        "CLI-7B",
        "Loser-7B",
        "Concurrent1-7B",
        "Concurrent2-7B",
        "Fail-7B",
        "Retry-7B",
        "RetryDashboard-7B",
        "Interrupted-7B",
    ):
        _seed_model(hub, f"synthetic/{name}", NEW_SHA, False, 500_000_000)


INITIAL_ENV = """\
SYNTHETIC_FIXTURE=1
VLLM_MODEL=synthetic/Old-7B
VLLM_TOOL_PARSER=hermes
VLLM_REASONING_PARSER=qwen3
VLLM_GPU_FRACTION=0.60
VLLM_MAX_MODEL_LEN=8192
VLLM_EXTRA_ARGS=
VLLM_VISION_MODEL=synthetic/Vision-3B
VLLM_VISION_GPU_FRACTION=0.20
FAMILY_CFO_AI_SUPPORTS_VISION=false
FAMILY_CFO_AI_VISION_ENABLED=false
"""


# --- the docker stub (the exact surface the engine uses) ---------------------------------


DOCKER_STUB = """\
import json, os, pathlib, sys, time

project = pathlib.Path(os.environ["SWAP_TEST_PROJECT"])
args = sys.argv[1:]
DEFAULT = "Qwen/Qwen2.5-VL-7B-Instruct"
IMAGE_ID = "sha256:" + "1" * 64

def env(key, default):
    for line in (project / ".env").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return default

def record(line):
    with (project / "calls").open("a") as out:
        out.write(line + "\\n")

def revision(key):
    value = env(key, "")
    return " --revision '" + value + "'" if value else ""

def seqs_cap():
    value = env("VLLM_MAX_NUM_SEQS", "")
    return " --max-num-seqs '" + value + "'" if value else ""

def vllm_command():
    return (
        "exec vllm serve '" + env("VLLM_MODEL", "Qwen/Qwen2.5-32B-Instruct") + "'"
        + revision("VLLM_REVISION")
        + " --enable-auto-tool-choice"
        " --tool-call-parser '" + env("VLLM_TOOL_PARSER", "hermes") + "'"
        " --reasoning-parser '" + env("VLLM_REASONING_PARSER", "qwen3") + "'"
        " --gpu-memory-utilization '" + env("VLLM_GPU_FRACTION", "0.60") + "'"
        " --max-model-len '" + env("VLLM_MAX_MODEL_LEN", "32768") + "'"
        + seqs_cap()
        + " " + env("VLLM_EXTRA_ARGS", "")
    ).rstrip()

def vision_command():
    return (
        "exec python -m vllm.entrypoints.openai.api_server"
        " --model '" + env("VLLM_VISION_MODEL", DEFAULT) + "'"
        + revision("VLLM_VISION_REVISION")
        + " --gpu-memory-utilization '" + env("VLLM_VISION_GPU_FRACTION", "0.20") + "'"
        " --max-model-len '8192'"
    )

def config_document():
    vision_enabled = env("FAMILY_CFO_AI_VISION_ENABLED", "false") == "true"
    return {
        "services": {
            "vllm": {
                "image": "vllm/vllm-openai:latest",
                "entrypoint": ["/bin/sh", "-c"],
                "command": vllm_command(),
                "environment": {
                    "HUGGING_FACE_HUB_TOKEN": "stub-hf-token-must-never-leak",
                    "VLLM_USE_DEEP_GEMM": "0",
                },
                "volumes": ["model_cache:/root/.cache/huggingface"],
            },
            "vllm-vision": {
                "image": "vllm/vllm-openai:latest",
                "entrypoint": ["/bin/sh", "-c"],
                "command": vision_command(),
                "environment": {"HUGGING_FACE_HUB_TOKEN": "stub-hf-token-must-never-leak"},
                "volumes": ["model_cache:/root/.cache/huggingface"],
            },
            "api": {
                "image": "ghcr.io/bobo-83/family-cfo-api:dev",
                "environment": {
                    "FAMILY_CFO_AI_MODEL": env("VLLM_MODEL", "Qwen/Qwen2.5-32B-Instruct"),
                    "FAMILY_CFO_AI_SUPPORTS_VISION": env("FAMILY_CFO_AI_SUPPORTS_VISION", "false"),
                    "FAMILY_CFO_AI_VISION_ENABLED": env("FAMILY_CFO_AI_VISION_ENABLED", "true"),
                    "FAMILY_CFO_AI_VISION_MODEL": env("VLLM_VISION_MODEL", DEFAULT),
                    "FAMILY_CFO_MASTER_KEY": "stub-master-key-must-never-leak",
                },
            },
            "worker": {"image": "ghcr.io/bobo-83/family-cfo-api:dev", "environment": {}},
        },
        "vision_enabled": vision_enabled,
    }

def running_ids():
    running = {"vllm", "api", "worker"}
    if env("FAMILY_CFO_AI_VISION_ENABLED", "false") == "true":
        running.add("vllm-vision")
    return running

def labels(argv):
    found = {}
    for index, value in enumerate(argv):
        if value == "--label" and index + 1 < len(argv):
            key, _, val = argv[index + 1].partition("=")
            found[key] = val
    return found

if args[:1] == ["compose"]:
    rest = args[1:]
    if rest[:1] == ["-f"]:
        rest = rest[2:]
    if rest[:2] == ["config", "--format"]:
        print(json.dumps(config_document()))
    elif rest[:1] == ["ps"]:
        service = rest[-1]
        if service in running_ids():
            print("stubcontainer-" + service)
    elif rest[:1] == ["up"]:
        record(" ".join(args))
        (project / "entered").write_text(str(os.getpid()))
        # A compose failure is a ONE-SHOT event (the scenario: the launch
        # fails, the restoration of the previous runtime then succeeds with
        # the same command). The fail marker is consumed by the first up.
        failed = (project / "fail").exists()
        if failed:
            (project / "fail").unlink()
        while (project / "hold").exists():
            time.sleep(0.02)
        raise SystemExit(7 if failed else 0)
    elif rest[:1] == ["stop"]:
        record(" ".join(args))
    else:
        sys.stderr.write("unexpected compose args: " + " ".join(args) + "\\n")
        raise SystemExit(64)
elif args[:1] == ["inspect"]:
    fmt = args[2]
    if fmt == "{{.Id}}":
        print(IMAGE_ID)
    elif "{{.State.Running}}" in fmt:
        for _target in args[3:]:
            print("true|%s|0" % IMAGE_ID)
    else:
        sys.stderr.write("unexpected inspect format: " + fmt + "\\n")
        raise SystemExit(64)
elif args[:1] == ["run"]:
    found = labels(args)
    if found.get("family.cfo.probe") == "runtime-version":
        print(json.dumps({"version": "0.32.0"}))
    elif found.get("family.cfo.apply") == "checker":
        print(json.dumps({
            "supported": True,
            "backend": "vllm-native",
            "reason": None,
            "architectures": ["SyntheticArchForCausalLM"],
            "transformers_fallback": False,
            "parser_available": True,
        }))
    elif found.get("family.cfo.apply") == "staging":
        pass  # staging touches only the cache: never a recorded mutation
    else:
        sys.stderr.write("unexpected run args: " + " ".join(args) + "\\n")
        raise SystemExit(64)
elif args[:1] == ["exec"]:
    script = " ".join(args)
    if "df" in args:
        print("Filesystem 1024-blocks Used Available Capacity Mounted")
        print("/dev/unit 100000000 1000000 90000000 1% /root/.cache/huggingface")
    elif "exists" in script:
        print(json.dumps({"exists": True}))
    else:
        sys.stderr.write("unexpected exec args: " + script + "\\n")
        raise SystemExit(64)
elif args[:1] == ["ps"]:
    pass  # host-side URL resolution is deliberately unused in these tests
else:
    sys.stderr.write("unexpected docker args: " + " ".join(args) + "\\n")
    raise SystemExit(64)
"""

# --- fixtures -----------------------------------------------------------------------------
def _write_stub(binaries: Path, name: str, body: str) -> None:
    path = binaries / name
    path.write_text("#!" + sys.executable + "\n" + body)
    path.chmod(0o755)


@pytest.fixture
def deployment(tmp_path):
    project = tmp_path / "deployment"
    (project / "scripts/lib").mkdir(parents=True)
    for relative in ("scripts/swap-model.sh", "scripts/lib/swap_lock.py"):
        shutil.copy2(ROOT / relative, project / relative)
    (project / ".env").write_text(INITIAL_ENV)
    seed_model_cache(project)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    _write_stub(binaries, "docker", DOCKER_STUB)
    shutil.copy2(binaries / "docker", binaries / "docker-compose")
    # The shell/helper must use the same Python installation as the test.
    (binaries / "python3").symlink_to(sys.executable)

    base_env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "SWAP_TEST_PROJECT": str(project),
        "HF_HOME": str(project / "hf-home"),
        "HF_HUB_OFFLINE": "1",
        "PYTHONPATH": str(ROOT / "services/model-manager/src"),
        "FAMILY_CFO_COMMIT_REVERIFY_SECONDS": "1",
        # Deterministic admission evidence: the manager and CLI subprocesses
        # must not depend on the runner's RAM (a ~7 GB CI runner refuses
        # even the 0.5 GB synthetic models at the 8 GiB reserve floor).
        "FAMILY_CFO_NODE_MEMORY_GB": "64.0",
    }
    base_env.pop("FAMILY_CFO_SWAP_LOCK_FD", None)
    for variable in (
        "FAMILY_CFO_MAIN_RUNTIME_URL",
        "FAMILY_CFO_PHOTO_RUNTIME_URL",
        "FAMILY_CFO_API_HEALTH_URL",
    ):
        base_env.pop(variable, None)
    processes: list[subprocess.Popen] = []

    def cli(*args):
        process = subprocess.Popen(
            ["bash", str(project / "scripts/swap-model.sh"), *args],
            env=base_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(process)
        return process

    yield project, base_env, cli, processes
    (project / "hold").unlink(missing_ok=True)
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


# --- the managed-runtime HTTP stub ---------------------------------------------------------


def _env_value(project: Path, key: str, default: str) -> str:
    for line in (project / ".env").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return default


@pytest.fixture
def runtime_server(deployment):
    """OpenAI-compatible answers for every verification probe, reflecting
    the CURRENT .env so baseline and target verification both pass."""
    project, _env, _cli, _processes = deployment

    class Handler(BaseHTTPRequestHandler):

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.endswith("/health"):
                self._json(200, {"status": "ok"})
            elif self.path.endswith("/v1/models"):
                served = [_env_value(project, "VLLM_MODEL", "")]
                if _env_value(project, "FAMILY_CFO_AI_VISION_ENABLED", "false") == "true":
                    served.append(_env_value(project, "VLLM_VISION_MODEL", ""))
                self._json(200, {"data": [{"id": name} for name in served if name]})
            else:
                self._json(404, {"error": "unknown path"})

        def do_POST(self) -> None:
            if not self.path.endswith("/v1/chat/completions"):
                self._json(404, {"error": "unknown path"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if payload.get("tools"):
                self._json(
                    200,
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "type": "function",
                                            "function": {
                                                "name": "verify_noop",
                                                "arguments": "{}",
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                )
            else:
                self._json(
                    200,
                    {"choices": [{"message": {"content": "OK"}}]},
                )

        def log_message(self, *_args) -> None:  # keep test output readable
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def runtime_urls(deployment, runtime_server):
    """Point the engine's runtime HTTP probes at the local stub server."""
    project, env, _cli, _processes = deployment
    env["FAMILY_CFO_MAIN_RUNTIME_URL"] = runtime_server
    env["FAMILY_CFO_PHOTO_RUNTIME_URL"] = runtime_server
    env["FAMILY_CFO_API_HEALTH_URL"] = runtime_server + "/api/v1/health"
    return env


@pytest.fixture
def manager(deployment, runtime_urls):
    """A real manager sidecar subprocess: v2 admission/reads/ack, plus the
    API's reconciliation role (the test acks through it)."""
    project, env, _cli, processes = deployment
    env["FAMILY_CFO_MANAGER_CREDENTIAL"] = CREDENTIAL
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        code = (
            "import uvicorn; import family_cfo_model_manager.main as m; "
            f"m.PROJECT_DIR={str(project)!r}; "
            f"uvicorn.run(m.app, fd={listener.fileno()}, log_level='error')"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            env=env,
            pass_fds=(listener.fileno(),),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    processes.append(process)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(
        path="/status",
        body: dict | None = None,
        method: str | None = None,
        credential: bool = False,
    ):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["X-Manager-Credential"] = CREDENTIAL
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=data,
            headers=headers,
            method=method or ("POST" if data is not None else "GET"),
        )
        try:
            with opener.open(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def ready():
        assert process.poll() is None, "manager exited before readiness"
        try:
            return request("/health")[0] == 200
        except urllib.error.URLError:
            return False

    eventually(ready, timeout=30)
    return request


# --- shared assertions -----------------------------------------------------------------------


def completed(process, expected=0):
    stdout, stderr = process.communicate(timeout=60)
    assert process.returncode == expected, stdout + stderr
    return stdout + stderr


def current_status(manager):
    status, body = manager("/v2/jobs/current")
    return body if status == 200 and "state" in body else None


def wait_for_state(manager, state, timeout=45):
    def check():
        status = current_status(manager)
        if status is not None and status.get("state") == state:
            return status
        return None

    return eventually(check, timeout=timeout)


def terminal(manager, state="succeeded", timeout=45):
    result = wait_for_state(manager, state, timeout=timeout)
    assert result["state"] == state, result
    return result


def submit_job(manager, model, *, photo_mode="off", vision_model=None, topology="single"):
    import uuid

    return manager(
        "/v2/jobs",
        {
            "request_id": str(uuid.uuid4()),
            "main_model": model,
            "vision_model": vision_model,
            "photo_mode": photo_mode,
            "topology": topology,
            "cluster_consent": False,
            "origin": "api",
            "runtime_key": "vllm",
        },
        credential=True,
    )


def acknowledge(manager, job_id):
    status, body = manager(
        f"/v2/jobs/{job_id}/ack",
        {
            "job_id": job_id,
            "receipt_job_id": "synthetic-receipt-1",
            "plan_hash": "synthetic",
        },
        credential=True,
    )
    assert status == 200, body
    return body


def wait_and_ack(manager, *, state="succeeded"):
    current = terminal(manager, state="commit_pending")
    acknowledge(manager, current["job_id"])
    return terminal(manager, state=state)


def assert_env(project: Path, **expected: str) -> None:
    for key, value in expected.items():
        assert f"{key}={value}\n" in (project / ".env").read_text(), (
            key,
            value,
            (project / ".env").read_text(),
        )

LAUNCH_CALL = (
    "compose -f docker-compose.yml up -d --scale vllm=1 --scale vllm-vision=0 vllm vllm-vision"
)
FINALIZE_CALL = "compose -f docker-compose.yml up -d --no-deps api worker"


def test_cli_swap_completes_with_acknowledgement(deployment, manager):
    project, _env, cli, _processes = deployment
    swap = cli("synthetic/New-7B", "none")
    wait_and_ack(manager)
    completed(swap)
    assert_env(
        project,
        VLLM_MODEL="synthetic/New-7B",
        VLLM_REVISION=NEW_SHA,
        VLLM_TOOL_PARSER="hermes",
        VLLM_REASONING_PARSER="qwen3",
        VLLM_MAX_MODEL_LEN="8192",
        VLLM_MAX_NUM_SEQS="1",
        FAMILY_CFO_AI_SUPPORTS_VISION="false",
        FAMILY_CFO_AI_VISION_ENABLED="false",
    )
    assert "VLLM_VISION_MODEL=" not in (project / ".env").read_text()
    calls = (project / "calls").read_text().splitlines()
    assert LAUNCH_CALL in calls and FINALIZE_CALL in calls
    # Legacy clients keep decoding the coarse projection.
    assert manager()[1]["state"] == "succeeded"
    assert manager()[1]["main_model"] == "synthetic/New-7B"
    # Snapshots hold routing facts, never credentials (the stub injects both).
    previous = (project / ".runtime-swap/v2/jobs")
    for job_directory in previous.iterdir():
        blob = (job_directory / "previous.json").read_text() if (
            job_directory / "previous.json"
        ).exists() else ""
        assert "stub-hf-token-must-never-leak" not in blob
        assert "stub-master-key-must-never-leak" not in blob


def test_dashboard_swap_uses_v2_admission(deployment, manager):
    project, _env, _cli, _processes = deployment
    status, body = submit_job(manager, "synthetic/Dashboard-7B")
    assert status == 202, body
    assert body["state"] == "accepted"
    assert body["origin"] == "api"
    wait_and_ack(manager)
    assert_env(project, VLLM_MODEL="synthetic/Dashboard-7B", VLLM_REVISION=NEW_SHA)
    calls = (project / "calls").read_text().splitlines()
    assert LAUNCH_CALL in calls and FINALIZE_CALL in calls


def test_cli_first_contention(deployment, manager):
    project, _env, cli, _processes = deployment
    (project / "hold").touch()
    first = cli("synthetic/CLI-7B", "none")
    eventually(lambda: (project / "entered").exists())
    before = (project / ".env").read_bytes()
    calls_before = (project / "calls").read_text().splitlines()
    # The manager never mutates while the CLI owns the directory flock.
    status, body = submit_job(manager, "synthetic/Loser-7B")
    assert status == 409, body
    assert body["error"]["code"] == "swap_in_progress"
    # Status is not ownership, but the durable journal DOES show the CLI job.
    assert manager()[1]["state"] == "running"
    assert manager()[1]["main_model"] == "synthetic/CLI-7B"
    assert (project / ".env").read_bytes() == before
    # The rejected submission added no deployment mutation of its own.
    assert (project / "calls").read_text().splitlines() == calls_before
    (project / "hold").unlink()
    wait_and_ack(manager)
    completed(first)
    # Once the lock is free the manager can admit normally.
    status, _body = submit_job(manager, "synthetic/Retry-7B")
    assert status == 202
    wait_and_ack(manager)
    assert_env(project, VLLM_MODEL="synthetic/Retry-7B")


def test_dashboard_first_contention(deployment, manager):
    project, _env, cli, _processes = deployment
    (project / "hold").touch()
    status, _body = submit_job(manager, "synthetic/Dashboard-7B")
    assert status == 202
    eventually(lambda: (project / "entered").exists())
    before = (project / ".env").read_bytes()
    calls_before = (project / "calls").read_text().splitlines()
    assert "already in progress" in completed(cli("synthetic/Loser-7B", "none"), 1)
    assert (project / ".env").read_bytes() == before
    # The refused CLI added no deployment mutation of its own.
    assert (project / "calls").read_text().splitlines() == calls_before
    (project / "hold").unlink()
    wait_and_ack(manager)
    assert_env(project, VLLM_MODEL="synthetic/Dashboard-7B")


def test_simultaneous_dashboard_requests(deployment, manager):
    project, _env, _cli, _processes = deployment
    (project / "hold").touch()
    barrier = threading.Barrier(2)

    def submit(index):
        barrier.wait(timeout=5)
        return submit_job(manager, f"synthetic/Concurrent{index}-7B")[0]

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        outcomes = dict(zip((1, 2), pool.map(submit, (1, 2))))
    # Exactly one admission wins the flock; which one is scheduling, not
    # identity -- the assertion follows the actual winner.
    assert sorted(outcomes.values()) == [202, 409]
    winner = next(index for index, status in outcomes.items() if status == 202)
    eventually(lambda: (project / "entered").exists())
    (project / "hold").unlink()
    wait_and_ack(manager)
    assert_env(project, VLLM_MODEL=f"synthetic/Concurrent{winner}-7B")


@pytest.mark.parametrize("owner", ["cli", "dashboard"])
def test_retry_after_compose_failure(deployment, manager, owner):
    project, _env, cli, _processes = deployment
    (project / "fail").touch()
    if owner == "cli":
        # The launch failed AFTER the runtime values were installed, so the
        # executor restores the previous runtime and the CLI reports failure.
        output = completed(cli("synthetic/Fail-7B", "none"), 1)
        assert "restored" in output
    else:
        assert submit_job(manager, "synthetic/Fail-7B")[0] == 202
        terminal(manager, "restored")
    assert_env(project, VLLM_MODEL="synthetic/Old-7B")
    (project / "fail").unlink(missing_ok=True)  # the stub consumed it
    retry = cli("synthetic/Retry-7B", "none")
    wait_and_ack(manager)
    completed(retry)
    assert submit_job(manager, "synthetic/RetryDashboard-7B")[0] == 202
    wait_and_ack(manager)
    assert_env(project, VLLM_MODEL="synthetic/RetryDashboard-7B")


def swap_lock_is_free(project: Path) -> bool:
    """True only when the deployment directory flock can actually be taken.

    ADR 0081 S5: a terminal record whose lock is still held keeps admission
    at 409 until ownership actually closes. The restored record becomes
    visible while the orphaned executor is still winding down (terminal write
    -> current-pointer clear -> process exit releases the inherited flock),
    so a retry must wait for THIS, not race the record's visibility."""
    fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def test_retry_after_cli_interruption(deployment, manager):
    project, _env, cli, _processes = deployment
    (project / "hold").touch()
    first = cli("synthetic/Interrupted-7B", "none")
    eventually(lambda: (project / "entered").exists())
    first.send_signal(signal.SIGTERM)
    # The #167 supervisor reports the interruption; the separately supervised
    # executor survives it and performs the durable restoration.
    completed(first, 128 + signal.SIGTERM)
    (project / "hold").unlink()
    restored = wait_for_state(manager, "restored")
    assert restored["decision"] == "restore"
    assert restored["origin"] == "cli"
    assert_env(project, VLLM_MODEL="synthetic/Old-7B")
    # The launch had already pinned VLLM_REVISION; the restore must remove
    # keys that were absent from the snapshot, not just rewrite values.
    assert "VLLM_REVISION" not in (project / ".env").read_text()
    # The 409 wind-down window is by design (ADR 0081 S5): wait for the
    # executor to actually close the flock instead of racing its exit.
    eventually(lambda: swap_lock_is_free(project))
    status, _body = submit_job(manager, "synthetic/Retry-7B")
    assert status == 202
    wait_and_ack(manager)
    assert_env(project, VLLM_MODEL="synthetic/Retry-7B")


@pytest.mark.parametrize(
    "args, message",
    [
        (("synthetic/Huge-999B", "none"), "insufficient_memory"),
        (("synthetic/Does-Not-Exist", "none"), "was not found"),
        (("--cluster", "synthetic/New-7B", "none"), "peer"),
    ],
)
def test_validation_refusals_before_env_write(deployment, args, message):
    project, _env, cli, _processes = deployment
    before = (project / ".env").read_bytes()
    assert message in completed(cli(*args), 1)
    assert (project / ".env").read_bytes() == before
    assert not (project / "calls").exists()


@pytest.mark.parametrize(
    "args, env_expectation",
    [
        # Vision-capable main, no argument: it describes photos itself.
        (
            ("synthetic/Main-VL-7B",),
            {
                "FAMILY_CFO_AI_SUPPORTS_VISION": "true",
                "FAMILY_CFO_AI_VISION_ENABLED": "false",
            },
        ),
        # Non-vision main, no argument: the retired script's default describer.
        (
            ("synthetic/New-7B",),
            {
                "FAMILY_CFO_AI_SUPPORTS_VISION": "false",
                "FAMILY_CFO_AI_VISION_ENABLED": "true",
                "VLLM_VISION_MODEL": DEFAULT_VISION_MODEL,
            },
        ),
        # An explicit describer alongside a vision-capable main is a deliberate
        # separate placement (the retired script refused; the engine decides).
        (
            ("synthetic/Main-VL-7B", "synthetic/Vision-3B"),
            {
                "FAMILY_CFO_AI_SUPPORTS_VISION": "false",
                "FAMILY_CFO_AI_VISION_ENABLED": "true",
                "VLLM_VISION_MODEL": "synthetic/Vision-3B",
            },
        ),
    ],
)
def test_photo_intent_mapping(deployment, manager, args, env_expectation):
    project, _env, cli, _processes = deployment
    swap = cli(*args)
    wait_and_ack(manager)
    completed(swap)
    assert_env(project, VLLM_MODEL=args[0], **env_expectation)
    calls = (project / "calls").read_text().splitlines()
    vision_scale = "--scale vllm-vision=1" if env_expectation.get(
        "FAMILY_CFO_AI_VISION_ENABLED"
    ) == "true" else "--scale vllm-vision=0"
    assert vision_scale in calls[0]


def test_manager_credential_and_legacy_surface(deployment, manager):
    project, _env, _cli, _processes = deployment
    # A v2 mutator without the deployment-scoped credential fails closed.
    import uuid

    status, body = manager(
        "/v2/jobs",
        {
            "request_id": str(uuid.uuid4()),
            "main_model": "synthetic/New-7B",
            "vision_model": None,
            "photo_mode": "off",
            "topology": "single",
            "cluster_consent": False,
            "origin": "api",
            "runtime_key": "vllm",
        },
    )
    assert status == 401, body
    # The legacy mutation surface is deliberately broken: 426, no mutation.
    status, body = manager("/swap", {"main_model": "synthetic/Legacy-7B"})
    assert status == 426, body
    assert body["error"]["code"] == "manager_upgrade_required"
    before = (project / ".env").read_bytes()
    assert (project / ".env").read_bytes() == before
    assert not (project / "calls").exists()
    assert manager("/v2/capabilities")[1]["protocol_major"] == 2

def test_docker_bind_mount_directory_flock(tmp_path):
    """Real host/container identity in BOTH orders; never pull inside a test."""
    docker = shutil.which("docker")
    if docker is None:
        if os.environ.get("FAMILY_CFO_REQUIRE_DOCKER") == "1":
            pytest.fail("Docker is required by FAMILY_CFO_REQUIRE_DOCKER=1")
        pytest.skip("Docker is not installed; CI requires it")
    info = subprocess.run(
        [docker, "info", "--format", "{{.OperatingSystem}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert info.returncode == 0, "Docker is installed but daemon is unavailable"
    if sys.platform == "darwin" and "docker desktop" in info.stdout.lower():
        pytest.skip(
            "macOS Docker Desktop virtiofs does not propagate host/VM flock; "
            "do not run host CLI and container manager swaps concurrently"
        )
    image = "python:3.12-slim"
    command = [
        docker,
        "run",
        "--rm",
        "--pull=never",
        "-i",
        "--network=none",
        "--mount",
        f"type=bind,source={tmp_path},target=/deployment",
        image,
        "python",
        "-u",
        "-c",
    ]
    acquire = """import fcntl, os, sys
fd = os.open('/deployment', os.O_RDONLY | os.O_DIRECTORY)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(23)
print('locked', flush=True)
"""
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        loser = subprocess.run(
            command + [acquire], capture_output=True, text=True, timeout=30
        )
        assert loser.returncode == 23, loser.stdout + loser.stderr
    finally:
        os.close(fd)
    owner = subprocess.Popen(
        command + [acquire + "sys.stdin.read()\n"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([owner.stdout], [], [], 30)[0], (
            "container readiness timed out"
        )
        assert owner.stdout.readline().strip() == "locked"
        fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    finally:
        stdout, stderr = owner.communicate(input="", timeout=30)
    assert owner.returncode == 0, stdout + stderr
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)

"""Real swap-script/manager integration, offline except the explicit Docker gate.

Run: apps/api/.venv/bin/python -m pytest scripts/tests/test_swap_model.py
Docker gate uses a preloaded python:3.12-slim image (CI pulls it explicitly).
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
import time
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[2]


def eventually(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(0.02)
    raise AssertionError("timed out waiting for synthetic swap")


@pytest.fixture
def deployment(tmp_path):
    project = tmp_path / "deployment"
    (project / "scripts/lib").mkdir(parents=True)
    for relative in ("scripts/swap-model.sh", "scripts/lib/swap_lock.py"):
        shutil.copy2(ROOT / relative, project / relative)
    # New keys avoid depending on GNU sed for normal applies. A portable sed
    # adapter below also exercises retries/updates on macOS's BSD-sed hosts.
    (project / ".env").write_text("SYNTHETIC_FIXTURE=1\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()

    def stub(name, body):
        path = binaries / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)

    stub("curl", "raise SystemExit(22)\n")  # explicitly choose offline probe
    stub("awk", "print(128)\n")  # deterministic synthetic 128-GB host
    stub(
        "sed",
        """import pathlib, sys
assert sys.argv[1] == '-i'
_, pattern, replacement, _ = sys.argv[2].split('|')
key = pattern.removeprefix('^').removesuffix('=.*')
p = pathlib.Path(sys.argv[3])
p.write_text(''.join(replacement + '\\n' if line.startswith(key + '=') else line
                     for line in p.read_text().splitlines(keepends=True)))
""",
    )
    stub(
        "docker",
        """import os, pathlib, sys, time
p = pathlib.Path(os.environ['SWAP_TEST_PROJECT'])
assert sys.argv[1:2] == ['compose'], sys.argv
assert 'up' in sys.argv and '-d' in sys.argv, sys.argv
with (p / 'calls').open('a') as out:
    out.write(' '.join(sys.argv[1:]) + '\\n')
(p / 'entered').write_text(str(os.getpid()))
while (p / 'hold').exists():
    time.sleep(0.02)
raise SystemExit(7 if (p / 'fail').exists() else 0)
""",
    )
    shutil.copy2(binaries / "docker", binaries / "docker-compose")
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "SWAP_TEST_PROJECT": str(project),
    }
    env.pop("FAMILY_CFO_SWAP_LOCK_FD", None)
    # The shell/helper must use the same Python installation as the test.
    (binaries / "python3").symlink_to(sys.executable)
    processes = []

    def cli(*args):
        process = subprocess.Popen(
            ["bash", str(project / "scripts/swap-model.sh"), *args],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(process)
        return process

    yield project, env, cli, processes
    (project / "hold").unlink(missing_ok=True)
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


@pytest.fixture
def manager(deployment):
    project, env, _, processes = deployment
    # Bind once and pass the listening socket, avoiding a find-free-port race.
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

    def request(path="/status", model=None):
        data = None if model is None else json.dumps({"main_model": model}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(req, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def ready():
        assert process.poll() is None, "manager exited before readiness"
        try:
            return request("/health")[0] == 200
        except urllib.error.URLError:
            return False

    eventually(ready)
    return request


def completed(process, expected=0):
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == expected, stdout + stderr
    return stdout + stderr


def terminal(manager, state="succeeded"):
    result = eventually(lambda: (s := manager()[1])["state"] != "running" and s)
    assert result["state"] == state, result


def test_cli_success_manager_disabled(deployment):
    project, _, cli, _ = deployment
    # No manager process exists; Docker stub accepts only recreation, not status.
    completed(cli("synthetic/Main-7B", "none"))
    assert "VLLM_MODEL=synthetic/Main-7B\n" in (project / ".env").read_text()
    assert (project / "calls").read_text().splitlines() == [
        "compose -f docker-compose.yml up -d --scale vllm-vision=0 vllm vllm-vision api worker"
    ]


def test_dashboard_success_inherits_ownership(deployment, manager):
    project, _, _, _ = deployment
    assert manager("/swap", "synthetic/Dashboard-7B")[0] == 202
    terminal(manager)
    assert "VLLM_MODEL=synthetic/Dashboard-7B\n" in (project / ".env").read_text()
    assert len((project / "calls").read_text().splitlines()) == 1


def test_cli_first_contention(deployment, manager):
    project, _, cli, _ = deployment
    (project / "hold").touch()
    first = cli("synthetic/CLI-7B", "none")
    eventually(lambda: (project / "entered").exists())
    before = (project / ".env").read_bytes()
    assert manager("/swap", "synthetic/Loser-7B")[0] == 409
    assert manager()[1]["state"] == "idle"  # status is not lock ownership
    assert (project / ".env").read_bytes() == before
    assert len((project / "calls").read_text().splitlines()) == 1
    (project / "hold").unlink()
    completed(first)
    assert manager("/swap", "synthetic/Retry-7B")[0] == 202
    terminal(manager)


def test_dashboard_first_contention(deployment, manager):
    project, _, cli, _ = deployment
    (project / "hold").touch()
    assert manager("/swap", "synthetic/Dashboard-7B")[0] == 202
    eventually(lambda: (project / "entered").exists())
    before = (project / ".env").read_bytes()
    assert "retry" in completed(cli("synthetic/Loser-7B", "none"), 1)
    assert (project / ".env").read_bytes() == before
    assert len((project / "calls").read_text().splitlines()) == 1
    (project / "hold").unlink()
    terminal(manager)
    completed(cli("synthetic/Retry-7B", "none"))


def test_simultaneous_dashboard_requests(deployment, manager):
    project, _, _, _ = deployment
    (project / "hold").touch()
    import threading

    barrier = threading.Barrier(2)

    def submit(index):
        barrier.wait(timeout=5)
        return manager("/swap", f"synthetic/Concurrent{index}-7B")[0]

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(submit, (1, 2))) == [202, 409]
    eventually(lambda: (project / "entered").exists())
    assert len((project / "calls").read_text().splitlines()) == 1
    (project / "hold").unlink()
    terminal(manager)


@pytest.mark.parametrize("owner", ["cli", "dashboard"])
def test_retry_after_compose_failure(deployment, manager, owner):
    project, _, cli, _ = deployment
    (project / "fail").touch()
    if owner == "cli":
        completed(cli("synthetic/Fail-7B", "none"), 7)
    else:
        assert manager("/swap", "synthetic/Fail-7B")[0] == 202
        terminal(manager, "failed")
    (project / "fail").unlink()
    completed(cli("synthetic/Retry-7B", "none"))
    assert manager("/swap", "synthetic/RetryDashboard-7B")[0] == 202
    terminal(manager)


def test_retry_after_cli_interruption(deployment, manager):
    project, _, cli, _ = deployment
    (project / "hold").touch()
    first = cli("synthetic/Interrupted-7B", "none")
    eventually(lambda: (project / "entered").exists())
    docker_pid = int((project / "entered").read_text())
    first.send_signal(signal.SIGTERM)
    completed(first, 128 + signal.SIGTERM)

    # A live compose child would still own the inherited fd. Check it is not
    # executing (Linux may temporarily retain an init-owned zombie).
    def stopped():
        try:
            result = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(docker_pid)],
                capture_output=True,
                text=True,
                check=False,
            )
            return not result.stdout.strip() or result.stdout.strip().startswith("Z")
        except ProcessLookupError:
            return True

    eventually(stopped)
    (project / "hold").unlink()
    assert manager("/swap", "synthetic/Retry-7B")[0] == 202
    terminal(manager)


@pytest.mark.parametrize(
    "args, message",
    [
        (("synthetic/Main-VL-7B", "synthetic/Vision-3B"), "Do not pass"),
        (("synthetic/Huge-999B", "none"), "Won't fit"),
        (("synthetic/Main-40B", "synthetic/Vision-20B"), "Won't fit"),
        (("--cluster", "synthetic/Main-7B", "none"), "needs two nodes"),
    ],
)
def test_validation_before_env_write(deployment, args, message):
    project, _, cli, _ = deployment
    before = (project / ".env").read_bytes()
    assert message in completed(cli(*args), 1)
    assert (project / ".env").read_bytes() == before
    assert not (project / "calls").exists()
    completed(cli("synthetic/Retry-7B", "none"))


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

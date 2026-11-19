"""Issue #167 unit tests: real directory flock, mocked swap processes only."""

import errno
import fcntl
import os
import signal
import subprocess
import threading
from contextlib import contextmanager, suppress
from unittest.mock import Mock, call, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from family_cfo_model_manager import main as manager

MAIN = "Qwen/Qwen2.5-14B-Instruct"
VISION = "Qwen/Qwen2.5-VL-3B-Instruct"


@pytest.fixture(autouse=True)
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "PROJECT_DIR", str(tmp_path))
    manager._status = manager.SwapStatus(state="idle")
    yield tmp_path
    manager._status = manager.SwapStatus(state="idle")


@contextmanager
def directory_fd(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield fd
    finally:
        os.close(fd)


def assert_contended(directory):
    with directory_fd(directory) as fd:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def assert_unlocked(directory):
    with directory_fd(directory) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.fixture
def pending_worker():
    with patch.object(manager.threading, "Thread") as thread:
        yield thread
        # The mocked worker did not take ownership in admission-only tests.
        for invocation in thread.call_args_list:
            with suppress(OSError):
                os.close(invocation.kwargs["args"][-1])


def test_admission_locks_before_start_and_passes_validated_args(deployment, pending_worker):
    def start():
        assert manager._status.state == "running"
        assert_contended(deployment)

    pending_worker.return_value.start.side_effect = start
    client = TestClient(manager.app)
    response = client.post("/swap", json={"main_model": MAIN, "vision_model": VISION})
    assert response.status_code == 202
    assert response.json()["state"] == "running"
    args = pending_worker.call_args.kwargs["args"]
    assert args[:3] == (MAIN, VISION, False)
    assert os.path.samestat(os.fstat(args[3]), os.stat(deployment))


def test_cli_first_rejects_dashboard_even_when_status_idle(deployment, pending_worker):
    with directory_fd(deployment) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = TestClient(manager.app).post("/swap", json={"main_model": MAIN})
        assert response.status_code == 409
        assert manager.status().state == "idle"
        pending_worker.assert_not_called()
    assert_unlocked(deployment)
    assert TestClient(manager.app).post("/swap", json={"main_model": MAIN}).status_code == 202


def test_dashboard_first_rejects_cli_and_second_dashboard(deployment, pending_worker):
    client = TestClient(manager.app)
    assert client.post("/swap", json={"main_model": MAIN}).status_code == 202
    assert_contended(deployment)
    assert client.post("/swap", json={"main_model": MAIN}).status_code == 409
    assert pending_worker.call_count == 1


def test_restart_idle_does_not_bypass_inherited_owner(deployment, pending_worker):
    client = TestClient(manager.app)
    assert client.post("/swap", json={"main_model": MAIN}).status_code == 202
    fd = pending_worker.call_args.kwargs["args"][-1]
    inherited = os.dup(fd)
    # Drop the manager copy and simulate process-local status reset. The
    # inherited copy still owns the same open file description, without UNLOCK.
    try:
        os.close(fd)
        manager._status = manager.SwapStatus(state="idle")
        assert client.post("/swap", json={"main_model": MAIN}).status_code == 409
        assert manager.status().state == "idle"
    finally:
        os.close(inherited)
    assert_unlocked(deployment)


def test_simultaneous_dashboard_admission_starts_one_worker(deployment, monkeypatch):
    barrier = threading.Barrier(3)
    finish = threading.Event()
    worker_finished = threading.Event()
    starts = []

    def worker(main, vision, cluster, fd):
        starts.append(main)
        try:
            assert finish.wait(5)
        finally:
            os.close(fd)
            worker_finished.set()

    monkeypatch.setattr(manager, "_run_swap", worker)
    results = []

    def submit():
        barrier.wait(timeout=5)
        try:
            manager.swap(manager.SwapRequest(main_model=MAIN))
            results.append(202)
        except HTTPException as exc:
            results.append(exc.status_code)

    submissions = [threading.Thread(target=submit) for _ in range(2)]
    try:
        for thread in submissions:
            thread.start()
        barrier.wait(timeout=5)
        for thread in submissions:
            thread.join(timeout=5)
            assert not thread.is_alive()
        assert sorted(results) == [202, 409]
        assert_contended(deployment)
    finally:
        finish.set()
        assert worker_finished.wait(5)
    assert starts == [MAIN]
    assert_unlocked(deployment)


@pytest.mark.parametrize("error", [OSError(errno.EACCES, "synthetic"), RuntimeError("synthetic")])
def test_thread_start_failure_is_terminal_and_releases(deployment, pending_worker, error):
    pending_worker.return_value.start.side_effect = error
    client = TestClient(manager.app)
    assert client.post("/swap", json={"main_model": MAIN}).status_code == 503
    assert manager.status().state == "failed"
    assert "could not start" in manager.status().log_tail
    assert_unlocked(deployment)
    pending_worker.return_value.start.side_effect = None
    assert client.post("/swap", json={"main_model": MAIN}).status_code == 202


def test_lock_error_fails_closed_without_starting(deployment, pending_worker):
    with patch.object(manager.fcntl, "flock", side_effect=OSError(errno.EIO, "synthetic")):
        response = TestClient(manager.app).post("/swap", json={"main_model": MAIN})
    assert response.status_code == 503
    pending_worker.assert_not_called()
    assert manager.status().state == "idle"
    assert_unlocked(deployment)


@pytest.mark.parametrize(
    "main,vision,cluster,expected",
    [
        (MAIN, VISION, False, [MAIN, VISION]),
        (MAIN, None, False, [MAIN, "none"]),
        (MAIN, None, True, ["--cluster", MAIN, "none"]),
        ("Qwen/Qwen2.5-VL-32B-Instruct", None, False, ["Qwen/Qwen2.5-VL-32B-Instruct"]),
    ],
)
@pytest.mark.parametrize("returncode", [0, 1])
def test_worker_inherits_fd_and_releases_only_after_completion(
    deployment, main, vision, cluster, expected, returncode
):
    fd = os.open(deployment, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    process = Mock(returncode=returncode)

    def communicate(**kwargs):
        assert kwargs == {"timeout": manager.SWAP_TIMEOUT_SECONDS}
        assert_contended(deployment)
        return "swapped ok", "stderr tail"

    process.communicate.side_effect = communicate
    with patch.object(manager.subprocess, "Popen", return_value=process) as popen:
        manager._run_swap(main, vision, cluster, fd)
    assert popen.call_args.args[0] == ["bash", "scripts/swap-model.sh", *expected]
    kwargs = popen.call_args.kwargs
    assert kwargs["cwd"] == str(deployment)
    assert kwargs["start_new_session"] is True
    assert kwargs["pass_fds"] == (fd,)
    assert kwargs["env"]["FAMILY_CFO_SWAP_LOCK_FD"] == str(fd)
    assert kwargs["env"]["PATH"] == os.environ["PATH"]
    assert manager.status().state == ("succeeded" if returncode == 0 else "failed")
    assert "swapped ok\nstderr tail" in manager.status().log_tail
    with pytest.raises(OSError):
        os.fstat(fd)
    assert_unlocked(deployment)


def test_launch_oserror_records_terminal_failure_and_retry(deployment):
    fd = os.open(deployment, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manager._status = manager.SwapStatus(state="running")
    with patch.object(manager.subprocess, "Popen", side_effect=OSError("synthetic")):
        manager._run_swap(MAIN, None, False, fd)
    assert manager.status().state == "failed"
    assert "could not run" in manager.status().log_tail
    assert_unlocked(deployment)


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("swap", 1), KeyboardInterrupt()])
@pytest.mark.parametrize("group_survives_grace", [True, False])
def test_timeout_or_interrupt_gives_descendants_full_grace_then_reaps(
    deployment, failure, group_survives_grace
):
    fd = os.open(deployment, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    process = Mock(pid=1234, returncode=None)
    events = []
    delivered = []

    def communicate(timeout=None):
        assert_contended(deployment)
        events.append(("communicate", timeout))
        if len(events) == 1:
            raise failure
        process.returncode = -9 if group_survives_grace else -15
        return "", ""

    def killpg(pid, sig):
        assert_contended(deployment)
        assert process.returncode is None  # No reap / reusable PGID before signaling finishes.
        events.append(("signal", sig))
        assert pid == process.pid
        if sig == signal.SIGKILL and not group_survives_grace:
            raise ProcessLookupError
        delivered.append(sig)

    def grace(seconds):
        assert seconds == manager.SWAP_TERMINATE_GRACE_SECONDS
        assert_contended(deployment)
        # The shell may exit immediately on TERM; do NOT reap it or end its
        # descendants' grace early. No wait/communicate has occurred since TERM.
        assert process.communicate.call_count == 1
        assert process.returncode is None
        events.append(("grace", seconds))

    process.communicate.side_effect = communicate
    with (
        patch.object(manager.subprocess, "Popen", return_value=process),
        patch.object(manager.os, "killpg", side_effect=killpg),
        patch.object(manager.time, "sleep", side_effect=grace),
    ):
        manager._run_swap(MAIN, None, False, fd)
    assert events == [
        ("communicate", manager.SWAP_TIMEOUT_SECONDS),
        ("signal", signal.SIGTERM),
        ("grace", manager.SWAP_TERMINATE_GRACE_SECONDS),
        ("signal", signal.SIGKILL),
        ("communicate", None),
    ]
    assert delivered == [signal.SIGTERM] + ([signal.SIGKILL] if group_survives_grace else [])
    assert manager.status().state == "failed"
    assert_unlocked(deployment)


def test_group_already_exited_is_still_reaped(deployment):
    fd = os.open(deployment, os.O_RDONLY | os.O_DIRECTORY)
    process = Mock(pid=1234, returncode=None)
    process.communicate.side_effect = [subprocess.TimeoutExpired("swap", 1), ("", "")]
    with (
        patch.object(manager.subprocess, "Popen", return_value=process),
        patch.object(manager.os, "killpg", side_effect=ProcessLookupError) as killpg,
        patch.object(manager.time, "sleep") as sleep,
    ):
        manager._run_swap(MAIN, None, False, fd)
    killpg.assert_called_once_with(process.pid, signal.SIGTERM)
    sleep.assert_not_called()
    assert process.communicate.call_args_list == [
        call(timeout=manager.SWAP_TIMEOUT_SECONDS),
        call(),
    ]
    assert manager.status().state == "failed"
    assert_unlocked(deployment)


def test_already_reaped_child_is_not_signalled():
    process = Mock(pid=1234, returncode=0)
    with patch.object(manager.os, "killpg") as killpg:
        manager._terminate_swap(process)
    killpg.assert_not_called()
    process.communicate.assert_called_once_with()

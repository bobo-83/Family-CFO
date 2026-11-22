"""Issue #167 lock discipline on the v2 engine (real directory flock, mocked
executor processes only).

Preserved semantics under the new admission/spawn seams:
- the deployment flock is taken BEFORE the executor is spawned and the child
  inherits the exact descriptor;
- an inherited (orphaned) fd keeps owning the lock across a manager restart;
- simultaneous admissions produce exactly one executor;
- a spawn failure is terminal, durably failed, and releases the lock;
- lock errors fail closed without starting anything;
- terminate_executor keeps the #167 reap discipline (SIGTERM -> full grace ->
  SIGKILL -> reap, leader never reaped early).
"""

from __future__ import annotations

import errno
import fcntl
import os
import signal
import threading
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from family_cfo_model_manager import executor as executor_module
from family_cfo_model_manager import main as manager
from family_cfo_model_manager.contracts import JobState, ReasonCode
from conftest import JOB_ID, MAIN, REQUEST_ID, make_deps

CREDENTIAL = "synthetic-deployment-credential"
HEADER = {"X-Manager-Credential": CREDENTIAL}


@pytest.fixture(autouse=True)
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "PROJECT_DIR", str(tmp_path))
    os.environ["FAMILY_CFO_MANAGER_CREDENTIAL"] = CREDENTIAL
    monkeypatch.setattr(manager, "_run_recovery_scan", lambda: None)
    yield tmp_path
    os.environ.pop("FAMILY_CFO_MANAGER_CREDENTIAL", None)


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
    }
    body.update(overrides)
    return body


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """The engine behind the app, with a spawn seam that KEEPS the lock fd."""
    deps = make_deps(tmp_path)
    spawned: list[tuple[str, int]] = []

    def spawn(deps_, job_id, fd, **kw):
        # Called while admission owns the deployment lock: contended here.
        assert_contended(tmp_path)
        # The real child inherits the descriptor; emulate that with a dup so
        # the route closing its own copy never releases the lock early.
        spawned.append((job_id, os.dup(fd)))
        return Mock(pid=4242, returncode=0)

    object.__setattr__(deps, "spawn", spawn)
    monkeypatch.setattr(manager, "_deps", lambda: deps)
    client = TestClient(manager.app)
    return client, deps, spawned


def _close_all(spawned):
    for _job_id, fd in spawned:
        try:
            os.close(fd)
        except OSError:
            pass


def test_admission_locks_before_spawn_and_hands_the_fd_to_the_executor(engine):
    client, deps, spawned = engine
    response = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
    assert response.status_code == 202
    assert response.json()["state"] == "accepted"
    assert len(spawned) == 1
    job_id, fd = spawned[0]
    # The descriptor handed to the executor is one the manager opened on the
    # deployment directory itself.
    assert os.path.samestat(os.fstat(fd), os.stat(deployment := manager.PROJECT_DIR))
    # The child still owns the lock after the HTTP call returns.
    assert_contended(deployment)
    _close_all(spawned)
    assert_unlocked(deployment)


def test_executor_spawn_wiring_inherits_the_lock_and_its_own_session(tmp_path, monkeypatch):
    """The REAL spawn seam: fixed command, own session, inherited fd, project env."""
    deps = make_deps(tmp_path)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with patch.object(executor_module.subprocess, "Popen") as popen:
            executor_module.spawn_executor(deps, JOB_ID, fd)
        args, kwargs = popen.call_args
        assert args[0] == executor_module.executor_command(JOB_ID)
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["start_new_session"] is True
        assert kwargs["pass_fds"] == (fd,)
        assert kwargs["env"]["FAMILY_CFO_SWAP_LOCK_FD"] == str(fd)
        assert kwargs["env"]["FAMILY_CFO_PROJECT_DIR"] == str(tmp_path)
        assert kwargs["env"]["PATH"] == os.environ["PATH"]

        with patch.object(
            executor_module.subprocess,
            "Popen",
            side_effect=OSError(errno.EACCES, "synthetic"),
        ):
            with pytest.raises(executor_module.ExecutorRefusal) as exc_info:
                executor_module.spawn_executor(deps, JOB_ID, fd)
        assert exc_info.value.reason_code is ReasonCode.EXECUTOR_START_FAILED
    finally:
        os.close(fd)


def test_executor_command_is_fixed_and_host_side_capable():
    module_form = executor_module.executor_command(JOB_ID)
    assert module_form[1:] == ["-m", "family_cfo_model_manager.executor", "--job-id", JOB_ID]
    bootstrap_form = executor_module.executor_command(
        JOB_ID, python="/usr/bin/python3", package_src="/srv/app/src"
    )
    assert bootstrap_form[0] == "/usr/bin/python3"
    assert bootstrap_form[1] == "-c"
    assert "--job-id" in bootstrap_form and JOB_ID in bootstrap_form


def test_cli_first_rejects_the_dashboard_even_when_idle(engine):
    client, deps, spawned = engine
    with directory_fd(manager.PROJECT_DIR) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "swap_in_progress"
        assert deps.journal.list_job_records() == ()
        assert spawned == []
    # Lock released: the same request now admits.
    assert client.post("/v2/jobs", json=_admission_body(), headers=HEADER).status_code == 202
    assert len(spawned) == 1
    _close_all(spawned)


def test_dashboard_first_rejects_the_cli_and_a_second_dashboard(engine):
    client, deps, spawned = engine
    assert client.post("/v2/jobs", json=_admission_body(), headers=HEADER).status_code == 202
    assert_contended(deployment := manager.PROJECT_DIR)
    assert client.post("/v2/jobs", json=_admission_body(), headers=HEADER).status_code == 409
    assert len(spawned) == 1
    _close_all(spawned)
    assert_unlocked(deployment)


def test_restart_does_not_bypass_the_inherited_owner(engine, tmp_path, monkeypatch):
    client, deps, spawned = engine
    assert client.post("/v2/jobs", json=_admission_body(), headers=HEADER).status_code == 202
    job_id, fd = spawned[0]
    inherited = os.dup(fd)
    try:
        # Drop the manager's own copy and rebuild the app as a restart would:
        # fresh deps over the SAME journal, in-process state gone.
        os.close(fd)
        spawned.clear()
        restarted = make_deps(tmp_path)
        object.__setattr__(restarted, "spawn", lambda d, j, f, **kw: Mock(pid=1))
        monkeypatch.setattr(manager, "_deps", lambda: restarted)
        restarted_client = TestClient(manager.app)
        response = restarted_client.post(
            "/v2/jobs", json=_admission_body(), headers=HEADER
        )
        assert response.status_code == 409
        # The inherited copy still owns the same open file description.
        assert_contended(tmp_path)
    finally:
        os.close(inherited)
    assert_unlocked(tmp_path)
    _close_all(spawned)


def test_simultaneous_admissions_spawn_one_executor(engine, tmp_path):
    client, deps, spawned = engine
    barrier = threading.Barrier(2)
    release = threading.Event()
    first_entered = threading.Event()
    results: list[int] = []

    def blocking_spawn(deps_, job_id, fd, **kw):
        first_entered.set()
        assert release.wait(5)
        spawned.append((job_id, os.dup(fd)))  # the child's inherited copy
        return Mock(pid=4242, returncode=0)

    object.__setattr__(deps, "spawn", blocking_spawn)

    def submit():
        barrier.wait(timeout=5)
        # One TestClient per thread: the portal is not shared across threads.
        thread_client = TestClient(manager.app)
        try:
            results.append(
                thread_client.post("/v2/jobs", json=_admission_body(), headers=HEADER).status_code
            )
        except Exception as exc:  # noqa: BLE001
            results.append(getattr(exc, "status_code", 500))

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    # The lock winner blocks inside its spawn; release it, then collect.
    assert first_entered.wait(5)
    release.set()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sorted(results) == [202, 409]
    assert len(spawned) == 1
    assert_contended(tmp_path)
    _close_all(spawned)
    assert_unlocked(tmp_path)


@pytest.mark.parametrize(
    "error", [executor_module.ExecutorRefusal(ReasonCode.EXECUTOR_START_FAILED, "no"), RuntimeError("synthetic")]
)
def test_spawn_failure_is_terminal_and_releases_the_lock(engine, tmp_path, error):
    client, deps, spawned = engine

    def failing_spawn(deps_, job_id, fd, **kw):
        os.close(fd)  # a failed spawn leaves nothing holding the lock
        raise error

    object.__setattr__(deps, "spawn", failing_spawn)
    response = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "executor_start_failed"
    # Durably failed, never resumable as-is, and the pointer released.
    record = deps.journal.read_job_record(response.json().get("job_id") or _last_job(deps))
    assert record is not None and record.state is JobState.FAILED
    assert deps.journal.read_current().job_id is None
    assert_unlocked(tmp_path)
    # A retry with a fresh request id admits normally.
    object.__setattr__(
        deps, "spawn", lambda d, j, f, **kw: spawned.append((j, os.dup(f))) or Mock(pid=1)
    )
    from conftest import rearm_runner

    rearm_runner(deps)  # the failed admission consumed the canned checker results
    retry = client.post(
        "/v2/jobs",
        json=_admission_body(request_id="0f0e1d2c-3b4a-5968-7789-abcdef012346"),
        headers=HEADER,
    )
    assert retry.status_code == 202
    _close_all(spawned)


def _last_job(deps):
    records = deps.journal.list_job_records()
    assert records
    return records[-1].job_id


def test_lock_error_fails_closed_without_starting(engine, tmp_path, monkeypatch):
    client, deps, spawned = engine
    with patch.object(fcntl, "flock", side_effect=OSError(errno.EIO, "synthetic")):
        response = client.post("/v2/jobs", json=_admission_body(), headers=HEADER)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "journal_unavailable"
    assert deps.journal.list_job_records() == ()
    assert_unlocked(tmp_path)


# --- terminate_executor: the #167 reap discipline -----------------------------------


def test_terminate_gives_descendants_full_grace_then_reaps():
    process = Mock(pid=1234, returncode=None)
    events: list[tuple] = []
    delivered: list[int] = []

    def killpg(pid, sig):
        assert pid == process.pid
        assert process.wait.call_count == 0  # the leader is never reaped early
        events.append(("signal", sig))
        delivered.append(sig)

    def grace(seconds):
        assert seconds == executor_module.EXECUTOR_TERMINATE_GRACE_SECONDS == 5.0
        assert process.wait.call_count == 0  # no reap during the grace
        events.append(("grace", seconds))

    process.wait.side_effect = lambda: events.append(("reap",))
    with (
        patch.object(executor_module.os, "killpg", side_effect=killpg),
        patch.object(executor_module.time, "sleep", side_effect=grace),
    ):
        executor_module.terminate_executor(process)
    assert events == [
        ("signal", signal.SIGTERM),
        ("grace", 5.0),
        ("signal", signal.SIGKILL),
        ("reap",),
    ]
    assert delivered == [signal.SIGTERM, signal.SIGKILL]


def test_terminate_group_already_gone_on_sigterm_is_still_reaped():
    process = Mock(pid=1234, returncode=None)
    with patch.object(executor_module.os, "killpg", side_effect=ProcessLookupError) as killpg:
        executor_module.terminate_executor(process)
    killpg.assert_called_once_with(process.pid, signal.SIGTERM)
    process.sleep_asserted = None
    process.wait.assert_called_once_with()


def test_terminate_sigkill_group_gone_is_suppressed():
    """A group that exits during the grace still gets its final reap."""
    process = Mock(pid=1234, returncode=None)

    def killpg(pid, sig):
        if sig == signal.SIGKILL:
            raise ProcessLookupError

    with (
        patch.object(executor_module.os, "killpg", side_effect=killpg),
        patch.object(executor_module.time, "sleep") as sleep,
    ):
        executor_module.terminate_executor(process)
    sleep.assert_called_once_with(executor_module.EXECUTOR_TERMINATE_GRACE_SECONDS)
    process.wait.assert_called_once_with()


def test_already_reaped_child_is_not_signalled():
    process = Mock(pid=1234, returncode=0)
    with patch.object(executor_module.os, "killpg") as killpg:
        executor_module.terminate_executor(process)
    killpg.assert_not_called()
    process.wait.assert_called_once_with()

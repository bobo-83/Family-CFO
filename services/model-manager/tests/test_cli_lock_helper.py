"""Unit coverage of the CLI's fcntl/supervision seam; no real swap or Docker."""

import fcntl
import importlib.util
import os
import signal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

_HELPER = Path(__file__).resolve().parents[3] / "scripts/lib/swap_lock.py"
_spec = importlib.util.spec_from_file_location("swap_lock", _HELPER)
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)


@pytest.fixture
def locked_directory(tmp_path):
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield tmp_path, fd
    finally:
        os.close(fd)


def test_inherited_fd_reflocks_same_description_without_unlocking(locked_directory):
    directory, fd = locked_directory
    assert helper.main(["--check", str(directory), str(fd)]) == 0
    # A second inherited copy can re-flock too, but an independent opener loses.
    inherited = os.dup(fd)
    contender = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        helper.check_lock(str(directory), inherited)
        with pytest.raises(BlockingIOError):
            helper.check_lock(str(directory), contender)
    finally:
        os.close(inherited)
        os.close(contender)


@pytest.mark.parametrize("marker", ["", "garbage", "-1", "999999999999999999999999999999999999"])
def test_invalid_marker_fails_closed(tmp_path, marker, capsys):
    assert helper.main(["--check", str(tmp_path), marker]) == 1
    assert "Could not lock" in capsys.readouterr().err


def test_closed_descriptor_fails_closed(tmp_path):
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    os.close(fd)
    assert helper.main(["--check", str(tmp_path), str(fd)]) == 1


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_wrong_inode_fails_closed(tmp_path, kind):
    wrong = tmp_path / "wrong"
    if kind == "file":
        wrong.write_text("synthetic")
    else:
        wrong.mkdir()
    fd = os.open(wrong, os.O_RDONLY)
    try:
        assert helper.main(["--check", str(tmp_path), str(fd)]) == 1
    finally:
        os.close(fd)


def test_marker_is_not_a_contention_bypass(locked_directory, capsys):
    directory, _owner = locked_directory
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert helper.main(["--check", str(directory), str(fd)]) == 1
        assert "retry" in capsys.readouterr().err
    finally:
        os.close(fd)


def test_standalone_acquires_holds_inherits_and_closes(tmp_path):
    process = Mock(pid=1234, returncode=0)
    owned = []

    def popen(args, **kwargs):
        (fd,) = kwargs["pass_fds"]
        owned.append(fd)
        assert kwargs["start_new_session"] is True
        assert kwargs["env"]["FAMILY_CFO_SWAP_LOCK_FD"] == str(fd)
        assert args == ["bash", str(tmp_path / "scripts/swap-model.sh"), "synthetic/Model", "none"]
        assert os.path.samestat(os.fstat(fd), os.stat(tmp_path))
        return process

    def wait():
        fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        return 0

    process.wait.side_effect = wait
    with patch.object(helper.subprocess, "Popen", side_effect=popen):
        assert helper.main([str(tmp_path), "synthetic/Model", "none"]) == 0
    with pytest.raises(OSError):
        os.fstat(owned[0])
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        helper.check_lock(str(tmp_path), fd)
    finally:
        os.close(fd)


def test_standalone_contention_never_launches(locked_directory):
    directory, _owner = locked_directory
    with patch.object(helper.subprocess, "Popen") as popen:
        assert helper.main([str(directory), "synthetic/Model"]) == 1
    popen.assert_not_called()


@pytest.mark.parametrize("group_survives_grace", [True, False])
@pytest.mark.parametrize("during_spawn", [True, False])
@pytest.mark.parametrize("interrupt", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_main_interrupt_keeps_lock_through_full_grace_and_reap_then_admits_retry(
    tmp_path, group_survives_grace, during_spawn, interrupt
):
    process = Mock(pid=1234, returncode=None)
    events = []
    handlers = {}
    owned = []
    repeated = False

    def assert_contended():
        contender = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(BlockingIOError):
                helper.check_lock(str(tmp_path), contender)
        finally:
            os.close(contender)

    def install(sig, handler):
        nonlocal repeated
        if handler == signal.SIG_IGN and not repeated:
            repeated = True
            # Another signal BEFORE SIG_IGN is installed must not raise again
            # and bypass cleanup. Exercise that exact non-reentrancy window.
            handlers[interrupt](interrupt, None)
        handlers[sig] = handler
        return signal.SIG_DFL

    def spawn(*args, **kwargs):
        owned.extend(kwargs["pass_fds"])
        assert_contended()
        if during_spawn:
            handlers[interrupt](interrupt, None)
        return process

    def wait():
        assert_contended()
        events.append(("wait", None))
        if not during_spawn and len(events) == 1:
            handlers[interrupt](interrupt, None)
        process.returncode = -9 if group_survives_grace else -15
        return process.returncode

    def killpg(pid, sig):
        assert_contended()
        assert process.returncode is None  # Leader is NOT reaped before last signal.
        assert pid == process.pid
        events.append(("signal", sig))
        if sig == signal.SIGKILL and not group_survives_grace:
            raise ProcessLookupError

    def grace(seconds):
        assert_contended()
        assert seconds == helper.TERMINATE_GRACE_SECONDS
        # No wait/reap between TERM and the full grace for surviving children.
        assert process.wait.call_count == (0 if during_spawn else 1)
        events.append(("grace", seconds))

    process.wait.side_effect = wait
    with (
        patch.object(helper.signal, "signal", side_effect=install),
        patch.object(helper.subprocess, "Popen", side_effect=spawn),
        patch.object(helper.os, "killpg", side_effect=killpg),
        patch.object(helper.time, "sleep", side_effect=grace),
    ):
        assert helper.main([str(tmp_path), "synthetic/Model"]) == 128 + interrupt
    assert repeated
    assert events == ([] if during_spawn else [("wait", None)]) + [
        ("signal", signal.SIGTERM),
        ("grace", helper.TERMINATE_GRACE_SECONDS),
        ("signal", signal.SIGKILL),
        ("wait", None),
    ]
    with pytest.raises(OSError):
        os.fstat(owned[0])
    retry = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        helper.check_lock(str(tmp_path), retry)
    finally:
        os.close(retry)


def test_standalone_launch_error_releases_and_returns_nonzero(tmp_path):
    with patch.object(helper.subprocess, "Popen", side_effect=OSError("synthetic")):
        assert helper.main([str(tmp_path), "synthetic/Model"]) == 1
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        helper.check_lock(str(tmp_path), fd)
    finally:
        os.close(fd)


def test_termination_reaps_even_if_group_is_already_gone():
    process = Mock(pid=1234, returncode=None)
    with (
        patch.object(helper.os, "killpg", side_effect=ProcessLookupError) as killpg,
        patch.object(helper.time, "sleep") as sleep,
    ):
        helper.terminate(process)
    killpg.assert_called_once_with(process.pid, signal.SIGTERM)
    sleep.assert_not_called()
    process.wait.assert_called_once_with()


def test_already_reaped_leader_is_not_signalled():
    process = Mock(pid=1234, returncode=0)
    with patch.object(helper.os, "killpg") as killpg:
        helper.terminate(process)
    killpg.assert_not_called()
    process.wait.assert_called_once_with()

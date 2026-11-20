"""Directory-flock admission and standalone swap supervision (ADR 0013).

The shell re-enters with the inherited descriptor; checking that descriptor
re-flocks its open file description, never bypasses admission via an env flag.
Only close descriptors: LOCK_UN would unlock other inherited copies too.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

TERMINATE_GRACE_SECONDS = 5


class Interrupted(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum


def check_lock(directory: str, fd: int) -> None:
    if not os.path.samestat(os.fstat(fd), os.stat(directory)):
        raise ValueError("descriptor is not the deployment directory")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def terminate(process: subprocess.Popen) -> None:
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            # macOS can report EPERM, not ESRCH, for an already-gone group.
            # Always continue to reap our child, even in that case.
            pass
        else:
            # Never reap the shell before our last group signal. Its PID pins
            # the group identity, and every descendant gets the full grace
            # even if the shell exits immediately on TERM.
            time.sleep(TERMINATE_GRACE_SECONDS)
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def supervise(directory: str, args: list[str], fd: int) -> int:
    starting = True
    pending_signal = None

    def interrupted(signum, _frame):
        nonlocal pending_signal
        # Latch BEFORE raising: repeated signals must never interrupt cleanup,
        # including the window before the except block installs SIG_IGN.
        if pending_signal is not None:
            return
        pending_signal = signum
        # Defer interruption until Popen has returned its child handle, so a
        # signal during spawn cannot strand an untracked child process group.
        if not starting:
            raise Interrupted(signum)

    previous = {
        sig: signal.signal(sig, interrupted)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }
    process = None
    try:
        process = subprocess.Popen(
            ["bash", os.path.join(directory, "scripts/swap-model.sh"), *args],
            # terminate() only signals this supervisor-created session's
            # process group, never the caller's inherited process group.
            start_new_session=True,
            pass_fds=(fd,),
            env={**os.environ, "FAMILY_CFO_SWAP_LOCK_FD": str(fd)},
        )
        starting = False
        if pending_signal is not None:
            raise Interrupted(pending_signal)
        return process.wait()
    except (Interrupted, OSError) as exc:
        # Ignore further signals until every child is stopped and reaped.
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        if process is not None:
            terminate(process)
        if isinstance(exc, Interrupted):
            return 128 + exc.signum
        print("Could not run the model swap.", file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(args: list[str]) -> int:
    fd = None
    inherited = args[0] == "--check"
    try:
        if inherited:
            directory, marker = args[1:]
            fd_number = int(marker)
            if fd_number < 0:
                raise ValueError("invalid descriptor")
            check_lock(directory, fd_number)
            return 0
        directory = args[0]
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        check_lock(directory, fd)
        return supervise(directory, args[1:], fd)
    except BlockingIOError:
        print(
            "A model swap is already in progress. Wait for it to finish, then retry.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, OverflowError):
        print("Could not lock the model swap deployment directory.", file=sys.stderr)
        return 1
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

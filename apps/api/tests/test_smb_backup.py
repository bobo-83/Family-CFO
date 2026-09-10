from __future__ import annotations

import io
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest

from family_cfo_api import smb_backup
from family_cfo_api.backup_operation_lock import BackupOperationLockLostError

_JOB_A = "00000000-0000-4000-8000-000000000001"
_JOB_B = "00000000-0000-4000-8000-000000000002"
_TARGET = smb_backup.SmbTarget("nas", "backups", None, "user", "secret")


class _Entry:
    def __init__(self, name: str, size: int, modified: int, mode: int = stat.S_IFREG) -> None:
        self.name = name
        self._stat = SimpleNamespace(st_size=size, st_mtime=modified, st_mode=mode)

    def stat(self) -> SimpleNamespace:
        return self._stat


class _RemoteHandle(io.BytesIO):
    def __init__(self, initial: bytes = b"", *, fail_write: bool = False) -> None:
        super().__init__(initial)
        self.fail_write = fail_write

    def write(self, value: bytes) -> int:
        if self.fail_write:
            raise OSError("write failed at \\\\nas\\private")
        return super().write(value)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _install_session_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smb_backup, "_open", lambda target: None)
    monkeypatch.setattr(smb_backup.smbclient, "reset_connection_cache", lambda: None)


def test_inventory_is_typed_recognized_and_stably_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_session_stubs(monkeypatch)
    entries = [
        _Entry(f"{_JOB_B}.v0.159.enc", 20, 100),
        _Entry("notes.enc", 30, 300),
        _Entry(f"{_JOB_A}.enc.partial", 10, 200),
        _Entry(f"{_JOB_A}.enc", 0, 100),
        _Entry("ignore.txt", 1, 500),
    ]
    monkeypatch.setattr(smb_backup.smbclient, "scandir", lambda path: entries)

    inventory = smb_backup.list_inventory(_TARGET)

    assert [item.filename for item in inventory.items] == [
        f"{_JOB_A}.enc",
        f"{_JOB_B}.v0.159.enc",
    ]
    assert inventory.items[0].anomaly_codes == ("zero_byte",)
    assert inventory.items[1].app_version == "0.159"
    assert inventory.items[1].timestamp_source == "remote_modified_at"
    assert [item.anomaly_code for item in inventory.protected_entries] == [
        "unrecognized_encrypted_file",
        "partial_file",
    ]
    assert [item["filename"] for item in smb_backup.list_backups(_TARGET)] == [
        f"{_JOB_B}.v0.159.enc"
    ]


def test_inventory_failure_is_typed_and_redacted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_session_stubs(monkeypatch)
    monkeypatch.setattr(
        smb_backup.smbclient,
        "scandir",
        lambda path: (_ for _ in ()).throw(OSError("failed at \\\\nas\\secret-share for alice")),
    )

    with pytest.raises(smb_backup.SmbInventoryError) as exc_info:
        smb_backup.list_inventory(_TARGET)

    assert exc_info.value.code == "smb_unavailable"
    assert "secret-share" not in str(exc_info.value)
    assert "secret-share" not in caplog.text
    assert "alice" not in caplog.text


def test_bounded_probe_alternates_oldest_and_newest(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_session_stubs(monkeypatch)
    items = tuple(
        smb_backup.SmbInventoryItem(
            filename=f"00000000-0000-4000-8000-{number:012d}.enc",
            job_id=f"00000000-0000-4000-8000-{number:012d}",
            app_version=None,
            size_bytes=10,
            modified_at=number,
        )
        for number in (4, 3, 2, 1)
    )
    opened: list[str] = []

    def open_file(path: str, mode: str) -> _RemoteHandle:
        opened.append(path.rsplit("\\", 1)[-1])
        return _RemoteHandle(b"x")

    monkeypatch.setattr(smb_backup.smbclient, "open_file", open_file)

    result = smb_backup.probe_inventory(_TARGET, smb_backup.SmbInventory(items, ()), limit=2)

    assert opened == [items[-1].filename, items[0].filename]
    assert result.status == "complete"
    assert result.probed_archive_count == 2
    assert result.readable_archive_count is None
    assert result.oldest_readable == items[-1]
    assert result.newest_readable == items[0]


def test_delete_requires_successful_read_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_session_stubs(monkeypatch)
    removed: list[str] = []
    monkeypatch.setattr(smb_backup.smbclient, "open_file", lambda path, mode: _RemoteHandle())
    monkeypatch.setattr(smb_backup.smbclient, "remove", removed.append)

    with pytest.raises(smb_backup.SmbReadProbeError):
        smb_backup.delete(_TARGET, f"{_JOB_A}.enc")

    assert removed == []


def test_upload_uses_partial_then_same_share_rename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_session_stubs(monkeypatch)
    source = tmp_path / "archive.enc"
    source.write_bytes(b"ciphertext")
    opened: list[tuple[str, str]] = []
    renamed: list[tuple[str, str]] = []
    monkeypatch.setattr(smb_backup.smbclient, "makedirs", lambda *args, **kwargs: None)
    monkeypatch.setattr(smb_backup.smbclient.path, "exists", lambda path: False)

    def open_file(path: str, mode: str) -> _RemoteHandle:
        opened.append((path, mode))
        return _RemoteHandle()

    monkeypatch.setattr(smb_backup.smbclient, "open_file", open_file)
    monkeypatch.setattr(smb_backup.smbclient, "rename", lambda src, dst: renamed.append((src, dst)))

    filename = f"{_JOB_A}.v0.159.enc"
    smb_backup.upload(_TARGET, str(source), filename)

    assert opened[0][0].endswith(filename + ".partial")
    assert opened[0][1] == "xb"
    assert renamed[0][0].endswith(filename + ".partial")
    assert renamed[0][1].endswith(filename)


def test_upload_checks_operation_ownership_before_promotion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_session_stubs(monkeypatch)
    source = tmp_path / "archive.enc"
    source.write_bytes(b"ciphertext")
    removed: list[str] = []
    renamed: list[tuple[str, str]] = []
    monkeypatch.setattr(smb_backup.smbclient, "makedirs", lambda *args, **kwargs: None)
    existence_checks = iter((False, False, True))
    monkeypatch.setattr(smb_backup.smbclient.path, "exists", lambda path: next(existence_checks))
    monkeypatch.setattr(smb_backup.smbclient, "open_file", lambda path, mode: _RemoteHandle())
    monkeypatch.setattr(smb_backup.smbclient, "remove", removed.append)
    monkeypatch.setattr(smb_backup.smbclient, "rename", lambda src, dst: renamed.append((src, dst)))

    with pytest.raises(BackupOperationLockLostError):
        smb_backup.upload(
            _TARGET,
            str(source),
            f"{_JOB_A}.enc",
            assert_mutation_owned=lambda: (_ for _ in ()).throw(
                BackupOperationLockLostError("lost")
            ),
        )

    assert renamed == []
    assert removed and removed[0].endswith(".partial")


def test_delete_checks_operation_ownership_immediately_before_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session_stubs(monkeypatch)
    removed: list[str] = []
    monkeypatch.setattr(smb_backup.smbclient, "open_file", lambda path, mode: _RemoteHandle(b"x"))
    monkeypatch.setattr(smb_backup.smbclient, "remove", removed.append)

    with pytest.raises(BackupOperationLockLostError):
        smb_backup.delete(
            _TARGET,
            f"{_JOB_A}.enc",
            assert_mutation_owned=lambda: (_ for _ in ()).throw(
                BackupOperationLockLostError("lost")
            ),
        )

    assert removed == []


def test_upload_failure_cleans_owned_partial_and_raises_redacted_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _install_session_stubs(monkeypatch)
    source = tmp_path / "archive.enc"
    source.write_bytes(b"ciphertext")
    removed: list[str] = []
    monkeypatch.setattr(smb_backup.smbclient, "makedirs", lambda *args, **kwargs: None)
    # The initial conflict checks need to report absent; cleanup then reports present.
    existence_checks = iter((False, False, True))
    monkeypatch.setattr(smb_backup.smbclient.path, "exists", lambda path: next(existence_checks))
    monkeypatch.setattr(
        smb_backup.smbclient,
        "open_file",
        lambda path, mode: _RemoteHandle(fail_write=True),
    )
    monkeypatch.setattr(smb_backup.smbclient, "remove", removed.append)

    with pytest.raises(smb_backup.SmbStorageError) as exc_info:
        smb_backup.upload(_TARGET, str(source), f"{_JOB_A}.enc")

    assert removed[0].endswith(f"{_JOB_A}.enc.partial")
    assert "private" not in str(exc_info.value)
    assert "private" not in caplog.text


def test_upload_conflict_does_not_remove_preexisting_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_session_stubs(monkeypatch)
    source = tmp_path / "archive.enc"
    source.write_bytes(b"ciphertext")
    removed: list[str] = []
    monkeypatch.setattr(smb_backup.smbclient, "makedirs", lambda *args, **kwargs: None)
    monkeypatch.setattr(smb_backup.smbclient.path, "exists", lambda path: path.endswith(".partial"))
    monkeypatch.setattr(smb_backup.smbclient, "remove", removed.append)

    with pytest.raises(smb_backup.SmbUploadConflictError):
        smb_backup.upload(_TARGET, str(source), f"{_JOB_A}.enc")

    assert removed == []


def test_query_capacity_uses_caller_available_size(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_session_stubs(monkeypatch)
    monkeypatch.setattr(
        smb_backup.smbclient,
        "stat_volume",
        lambda path: SimpleNamespace(
            total_size=1000, caller_available_size=200, actual_available_size=800
        ),
    )

    capacity = smb_backup.query_capacity(_TARGET)

    assert capacity.total_bytes == 1000
    assert capacity.available_bytes == 200
    assert capacity.actual_available_bytes == 800


def test_query_capacity_reports_unsupported_without_inventing_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session_stubs(monkeypatch)
    monkeypatch.setattr(
        smb_backup.smbclient,
        "stat_volume",
        lambda path: (_ for _ in ()).throw(NotImplementedError("private detail")),
    )

    with pytest.raises(smb_backup.SmbCapacityError) as exc_info:
        smb_backup.query_capacity(_TARGET)

    assert exc_info.value.code == "capacity_unsupported"
    assert "private detail" not in str(exc_info.value)


def test_friendly_unknown_error_never_echoes_raw_exception() -> None:
    reason = smb_backup._friendly(RuntimeError("\\\\nas\\private alice secret-token"))

    assert reason == "The Synology operation failed. Check the destination and try again."

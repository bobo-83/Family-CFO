"""Userspace SMB access to a Synology (or any SMB) share for off-box backups (M98).

The app collects the Synology's address + credentials and we talk SMB directly —
no host CIFS mount, no container privileges, works from both api and worker. The
password is handled as a secret: encrypted at rest by the caller, never logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import smbclient
from smbprotocol.exceptions import SMBOSError, SMBResponseException

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SmbTarget:
    host: str
    share: str
    folder: str | None
    username: str
    password: str
    domain: str | None = None


def _unc_base(target: SmbTarget) -> str:
    base = f"\\\\{target.host}\\{target.share}"
    if target.folder:
        cleaned = target.folder.strip("\\/").replace("/", "\\")
        if cleaned:
            base += "\\" + cleaned
    return base


def _open(target: SmbTarget) -> None:
    # smbprotocol takes an AD/NT domain as DOMAIN\\user; home Synology setups
    # (WORKGROUP) just use the bare username.
    username = target.username
    if target.domain and "\\" not in username:
        username = f"{target.domain}\\{username}"
    smbclient.register_session(target.host, username=username, password=target.password)


def _friendly(exc: Exception) -> str:
    text = str(exc).lower()
    if "logon" in text or "password" in text or "credential" in text or "access is denied" in text:
        return "The Synology rejected the username or password."
    if "bad_network_name" in text or "network name" in text or "share" in text:
        return "That share name wasn't found on the Synology."
    if "timed out" in text or "refused" in text or "unreachable" in text or "name or service" in text:
        return "Couldn't reach the Synology at that address — check the IP and that SMB is enabled."
    return f"SMB error: {exc}"


def verify(target: SmbTarget) -> tuple[bool, str | None]:
    """Connect and prove we can write to the folder. Returns (ok, reason)."""
    try:
        _open(target)
        base = _unc_base(target)
        try:
            smbclient.makedirs(base, exist_ok=True)
        except (SMBOSError, SMBResponseException, ValueError):
            pass  # folder may already exist, or the share root is the target
        probe = base + "\\.family-cfo-write-test"
        with smbclient.open_file(probe, mode="wb") as handle:
            handle.write(b"ok")
        smbclient.remove(probe)
        return True, None
    except Exception as exc:  # noqa: BLE001 — any failure is a user-facing reason
        return False, _friendly(exc)
    finally:
        smbclient.reset_connection_cache()


def upload(target: SmbTarget, local_path: str, filename: str) -> None:
    """Copy a finished .enc to the share. Raises on failure."""
    try:
        _open(target)
        base = _unc_base(target)
        try:
            smbclient.makedirs(base, exist_ok=True)
        except (SMBOSError, SMBResponseException, ValueError):
            pass
        with open(local_path, "rb") as src, smbclient.open_file(
            base + "\\" + filename, mode="wb"
        ) as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
    finally:
        smbclient.reset_connection_cache()


def list_backups(target: SmbTarget) -> list[dict]:
    """The .enc files on the share, newest first: filename, size_bytes, modified_at."""
    try:
        _open(target)
        base = _unc_base(target)
        items: list[dict] = []
        for entry in smbclient.scandir(base):
            if not entry.name.endswith(".enc"):
                continue
            info = entry.stat()
            items.append(
                {
                    "filename": entry.name,
                    "size_bytes": int(info.st_size),
                    "modified_at": int(info.st_mtime),
                }
            )
        items.sort(key=lambda item: item["modified_at"], reverse=True)
        return items
    except Exception as exc:  # noqa: BLE001
        logger.warning("smb list failed: %s", exc)
        return []
    finally:
        smbclient.reset_connection_cache()


def download(target: SmbTarget, filename: str) -> bytes:
    """Read one .enc back from the share for restore."""
    try:
        _open(target)
        path = _unc_base(target) + "\\" + filename
        with smbclient.open_file(path, mode="rb") as handle:
            return handle.read()
    finally:
        smbclient.reset_connection_cache()


def delete(target: SmbTarget, filename: str) -> None:
    """Remove one .enc from the share."""
    try:
        _open(target)
        smbclient.remove(_unc_base(target) + "\\" + filename)
    finally:
        smbclient.reset_connection_cache()

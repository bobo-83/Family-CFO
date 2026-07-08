from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class BackupEncryptionError(ValueError):
    """Raised for a missing/malformed encryption key, or a corrupt/tampered/wrong-key archive."""


def generate_key() -> str:
    return Fernet.generate_key().decode("utf-8")


def _build_fernet(key: str) -> Fernet:
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise BackupEncryptionError("invalid backup encryption key") from exc


def encrypt(key: str, data: bytes) -> bytes:
    return _build_fernet(key).encrypt(data)


def decrypt(key: str, token: bytes) -> bytes:
    fernet = _build_fernet(key)
    try:
        return fernet.decrypt(token)
    except InvalidToken as exc:
        raise BackupEncryptionError(
            "backup archive could not be decrypted; wrong key or corrupted archive"
        ) from exc

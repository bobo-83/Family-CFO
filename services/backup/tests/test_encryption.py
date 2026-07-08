from __future__ import annotations

import pytest

from family_cfo_backup import BackupEncryptionError, decrypt, encrypt, generate_key


def test_encrypt_decrypt_round_trip() -> None:
    key = generate_key()
    plaintext = b"database dump bytes and document tar bytes"

    ciphertext = encrypt(key, plaintext)

    assert ciphertext != plaintext
    assert decrypt(key, ciphertext) == plaintext


def test_decrypt_with_wrong_key_fails() -> None:
    ciphertext = encrypt(generate_key(), b"secret household data")

    with pytest.raises(BackupEncryptionError):
        decrypt(generate_key(), ciphertext)


def test_decrypt_corrupted_archive_fails() -> None:
    key = generate_key()
    ciphertext = bytearray(encrypt(key, b"secret household data"))
    ciphertext[-1] ^= 0xFF

    with pytest.raises(BackupEncryptionError):
        decrypt(key, bytes(ciphertext))


def test_invalid_key_raises_backup_encryption_error() -> None:
    with pytest.raises(BackupEncryptionError):
        encrypt("not-a-valid-fernet-key", b"data")

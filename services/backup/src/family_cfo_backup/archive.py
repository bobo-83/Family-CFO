from __future__ import annotations

import io
import tarfile

DATABASE_ENTRY_NAME = "database.dump"
DOCUMENTS_ENTRY_NAME = "documents.tar"


def build_archive(database_dump: bytes, documents_tar: bytes) -> bytes:
    """Bundle a database dump and a document-tree tar into one tar, encrypted as a single unit."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        _add_bytes(tar, DATABASE_ENTRY_NAME, database_dump)
        _add_bytes(tar, DOCUMENTS_ENTRY_NAME, documents_tar)
    return buffer.getvalue()


def extract_archive(archive_bytes: bytes) -> tuple[bytes, bytes]:
    buffer = io.BytesIO(archive_bytes)
    with tarfile.open(fileobj=buffer, mode="r") as tar:
        database_dump = _read_member(tar, DATABASE_ENTRY_NAME)
        documents_tar = _read_member(tar, DOCUMENTS_ENTRY_NAME)
    return database_dump, documents_tar


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _read_member(tar: tarfile.TarFile, name: str) -> bytes:
    member = tar.extractfile(name)
    if member is None:
        raise ValueError(f"backup archive is missing {name!r}")
    return member.read()

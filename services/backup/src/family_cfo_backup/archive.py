from __future__ import annotations

import io
import json
import tarfile

DATABASE_ENTRY_NAME = "database.dump"
DOCUMENTS_ENTRY_NAME = "documents.tar"
MANIFEST_ENTRY_NAME = "manifest.json"


def build_archive(
    database_dump: bytes, documents_tar: bytes, manifest: dict | None = None
) -> bytes:
    """Bundle a database dump and a document-tree tar into one tar, encrypted as a single unit.

    The optional manifest (app_version, schema_revision) travels INSIDE the
    encrypted archive, so compatibility is checkable even for archives that
    outlived their box (a Synology restore onto fresh hardware).
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        _add_bytes(tar, DATABASE_ENTRY_NAME, database_dump)
        _add_bytes(tar, DOCUMENTS_ENTRY_NAME, documents_tar)
        if manifest is not None:
            _add_bytes(tar, MANIFEST_ENTRY_NAME, json.dumps(manifest).encode())
    return buffer.getvalue()


def extract_archive(archive_bytes: bytes) -> tuple[bytes, bytes, dict | None]:
    """(database_dump, documents_tar, manifest). Manifest is None for archives
    from before versioned backups — callers treat those as unknown-version."""
    buffer = io.BytesIO(archive_bytes)
    with tarfile.open(fileobj=buffer, mode="r") as tar:
        database_dump = _read_member(tar, DATABASE_ENTRY_NAME)
        documents_tar = _read_member(tar, DOCUMENTS_ENTRY_NAME)
        manifest = None
        try:
            member = tar.extractfile(MANIFEST_ENTRY_NAME)
            if member is not None:
                manifest = json.loads(member.read().decode())
        except KeyError:
            manifest = None
    return database_dump, documents_tar, manifest


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _read_member(tar: tarfile.TarFile, name: str) -> bytes:
    member = tar.extractfile(name)
    if member is None:
        raise ValueError(f"backup archive is missing {name!r}")
    return member.read()

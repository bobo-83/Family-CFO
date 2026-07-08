from __future__ import annotations

from family_cfo_backup import build_archive, extract_archive


def test_build_extract_archive_round_trip() -> None:
    database_dump = b"fake pg_dump custom-format bytes"
    documents_tar = b"fake tar of the document staging tree"

    archive = build_archive(database_dump, documents_tar)
    restored_dump, restored_tar = extract_archive(archive)

    assert restored_dump == database_dump
    assert restored_tar == documents_tar


def test_build_archive_handles_empty_documents_tar() -> None:
    archive = build_archive(b"dump-bytes", b"")
    restored_dump, restored_tar = extract_archive(archive)

    assert restored_dump == b"dump-bytes"
    assert restored_tar == b""

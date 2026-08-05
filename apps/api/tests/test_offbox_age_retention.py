"""#192: age-based pruning of off-box (Synology) backups, so a deleted
household's ciphertext has a bounded erasure horizon."""


from family_cfo_api import backup_processing, smb_backup


class _FakeSmb:
    def __init__(self, items):
        self.items = list(items)
        self.deleted = []

    def list_backups(self, target):
        return sorted(self.items, key=lambda i: i["modified_at"], reverse=True)

    def delete(self, target, filename):
        self.deleted.append(filename)
        self.items = [i for i in self.items if i["filename"] != filename]


def _install(monkeypatch, fake):
    monkeypatch.setattr(smb_backup, "list_backups", fake.list_backups)
    monkeypatch.setattr(smb_backup, "delete", fake.delete)


def test_age_cap_deletes_only_stale_and_keeps_newest(monkeypatch) -> None:
    now = 1_000_000_000.0
    day = 86400
    fake = _FakeSmb([
        {"filename": "new.enc", "size_bytes": 10, "modified_at": now - 1 * day},
        {"filename": "mid.enc", "size_bytes": 10, "modified_at": now - 40 * day},
        {"filename": "old.enc", "size_bytes": 10, "modified_at": now - 100 * day},
        {"filename": "ancient.enc", "size_bytes": 10, "modified_at": now - 400 * day},
    ])
    _install(monkeypatch, fake)

    deleted = backup_processing._enforce_age_cap_remote(
        object(), 90, now=now
    )
    assert deleted == 2
    assert set(fake.deleted) == {"old.enc", "ancient.enc"}
    assert "new.enc" not in fake.deleted and "mid.enc" not in fake.deleted


def test_age_cap_keeps_the_single_newest_even_if_old(monkeypatch) -> None:
    """A household that stopped backing up long ago keeps ONE restorable copy."""
    now = 1_000_000_000.0
    day = 86400
    fake = _FakeSmb([
        {"filename": "only-old.enc", "size_bytes": 10, "modified_at": now - 500 * day},
        {"filename": "older.enc", "size_bytes": 10, "modified_at": now - 600 * day},
    ])
    _install(monkeypatch, fake)

    deleted = backup_processing._enforce_age_cap_remote(object(), 90, now=now)
    assert deleted == 1  # the truly-oldest goes; the newest (still old) stays
    assert fake.deleted == ["older.enc"]


def test_zero_days_is_a_noop(monkeypatch, demo_file_engine, demo_file_settings) -> None:
    """0 = keep forever: run_backup_once with no retention never age-prunes."""
    calls = []
    monkeypatch.setattr(
        backup_processing, "_enforce_age_cap_remote",
        lambda *a, **k: calls.append(a) or 0,
    )
    backup_processing.run_backup_once(
        demo_file_engine,
        database_url=demo_file_settings.database_url,
        staging_dir=demo_file_settings.import_staging_dir,
        backup_dir=demo_file_settings.backup_dir,
        encryption_key=demo_file_settings.backup_encryption_key,
        retention_count=7,
        smb_target=None,
        offbox_retention_days=0,
    )
    assert calls == []

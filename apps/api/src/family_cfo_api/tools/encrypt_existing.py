"""One-shot re-encryption of pre-ADR-0072 plaintext content rows.

Run once after enabling FAMILY_CFO_MASTER_KEY:

    python -m family_cfo_api.tools.encrypt_existing

Idempotent: rows already carrying the enc1: prefix are skipped, so an
interrupted run just resumes. Prints a per-table count.
"""

from __future__ import annotations

from sqlalchemy import create_engine, select, update

from family_cfo_api import household_crypto, models
from family_cfo_api.config import get_settings


def _encrypt_table(engine, table, id_col, text_cols, household_of) -> int:
    changed = 0
    with engine.connect() as conn:
        rows = conn.execute(select(table)).mappings().all()
    for row in rows:
        household_id = household_of(row)
        if household_id is None:
            continue
        values = {}
        for col in text_cols:
            value = row[col]
            if value is None:
                continue
            value = str(value)  # amounts arrive as ints/digit strings (#184)
            if value.startswith(household_crypto.ENC_PREFIX):
                continue
            values[col] = household_crypto.encrypt_text(engine, household_id, value)
        if not values:
            continue
        with engine.begin() as conn:
            conn.execute(update(table).where(id_col == row["id"]).values(**values))
        changed += 1
    return changed


def main() -> int:
    settings = get_settings()
    if not settings.master_key:
        print("FAMILY_CFO_MASTER_KEY is not set — nothing to do.")
        return 1
    engine = create_engine(settings.database_url, future=True)

    with engine.connect() as conn:
        conversation_households = {
            row[0]: row[1]
            for row in conn.execute(
                select(models.conversations.c.id, models.conversations.c.household_id)
            )
        }
        document_households = {
            row[0]: row[1]
            for row in conn.execute(
                select(models.documents.c.id, models.documents.c.household_id)
            )
        }

    plan = [
        (
            "conversation_messages",
            models.conversation_messages,
            models.conversation_messages.c.id,
            ["content"],
            lambda row: conversation_households.get(row["conversation_id"]),
        ),
        (
            "recommendations",
            models.recommendations,
            models.recommendations.c.id,
            ["answer"],
            lambda row: row["household_id"],
        ),
        (
            "household_memories",
            models.household_memories,
            models.household_memories.c.id,
            ["value"],
            lambda row: row["household_id"],
        ),
        (
            "advisor_feedback",
            models.advisor_feedback,
            models.advisor_feedback.c.id,
            ["note"],
            lambda row: row["household_id"],
        ),
        (
            "document_extractions",
            models.document_extractions,
            models.document_extractions.c.id,
            ["text"],
            lambda row: document_households.get(row["document_id"]),
        ),
        (
            "transactions",
            models.transactions,
            models.transactions.c.id,
            ["merchant", "description", "note", "amount_minor"],
            lambda row: row["household_id"],
        ),
        (
            "accounts",
            models.accounts,
            models.accounts.c.id,
            ["name", "institution"],
            lambda row: row["household_id"],
        ),
        (
            "bills",
            models.bills,
            models.bills.c.id,
            ["name"],
            lambda row: row["household_id"],
        ),
        (
            "income_sources",
            models.income_sources,
            models.income_sources.c.id,
            ["name"],
            lambda row: row["household_id"],
        ),
        (
            "goals",
            models.goals,
            models.goals.c.id,
            ["name"],
            lambda row: row["household_id"],
        ),
        (
            "audit_events",
            models.audit_events,
            models.audit_events.c.id,
            ["summary", "undo_token"],
            lambda row: row["household_id"],
        ),
        (
            "reports",
            models.reports,
            models.reports.c.id,
            ["explanation_text"],
            lambda row: row["household_id"],
        ),
    ]
    for name, table, id_col, cols, household_of in plan:
        changed = _encrypt_table(engine, table, id_col, cols, household_of)
        print(f"{name}: {changed} rows encrypted")

    # Phase 2 backfill: devices paired before wraps existed get their ECIES
    # wrap from the stored pairing public key (idempotent — upsert per device).
    from family_cfo_api import repository

    wrapped = 0
    purged = 0
    for household_id in repository.list_households(engine):
        for device in repository.list_paired_devices(engine, household_id):
            if device.revoked_at is None and device.public_key:
                household_crypto.ensure_device_wrap(
                    engine, household_id, device.id, device.public_key
                )
                wrapped += 1
            elif device.revoked_at is not None:
                household_crypto.delete_device_wrap(engine, household_id, device.id)
                purged += 1
    print(f"device wraps ensured: {wrapped}, revoked-device wraps purged: {purged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

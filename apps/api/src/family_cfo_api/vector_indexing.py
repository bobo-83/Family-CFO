"""Index household memories + transactions into the vector store (M69, ADR 0017).

Runs in the worker: an additive upsert at startup and a wipe-and-rebuild
daily (which prunes vectors of deleted rows). Everything is best-effort —
an unreachable Qdrant or embedder logs and skips, never blocks the worker.
The store is rebuildable from PostgreSQL, so backups deliberately exclude it.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.config import Settings, get_settings
from family_cfo_api.embeddings import EmbeddingAdapter, get_default_embedder
from family_cfo_api.explanation import format_money
from family_cfo_api.vector_store import (
    QdrantVectorStore,
    VectorPoint,
    VectorStoreAdapter,
)
from family_cfo_financial_engine import Money

logger = logging.getLogger(__name__)

# Matches the sync/bill-detection window: ~13 months of transactions.
INDEX_WINDOW_DAYS = 400
_BATCH = 64


def _transaction_point_text(merchant: str | None, description: str | None) -> str:
    return " ".join(part for part in (merchant, description) if part).strip() or "transaction"


def _collect_points(engine: Engine, household_id: str) -> list[tuple[str, str, dict]]:
    """(point_id, text_to_embed, payload) for one household."""
    collected: list[tuple[str, str, dict]] = []
    for memory in repository.list_household_memories(engine, household_id):
        collected.append(
            (
                memory.id,
                memory.value,
                {
                    "household_id": household_id,
                    "kind": "memory",
                    "text": memory.value,
                    "date": memory.updated_at.date().isoformat(),
                },
            )
        )
    since = date.today() - timedelta(days=INDEX_WINDOW_DAYS)
    for (
        txn_id,
        occurred_at,
        amount_minor,
        currency,
        merchant,
        description,
        account_name,
    ) in repository.list_transactions_for_indexing(engine, household_id, since=since):
        text = _transaction_point_text(merchant, description)
        collected.append(
            (
                txn_id,
                text,
                {
                    "household_id": household_id,
                    "kind": "transaction",
                    "text": text,
                    "date": occurred_at.isoformat(),
                    "amount_display": format_money(Money(amount_minor, currency)),
                    "account": account_name,
                },
            )
        )
    return collected


def index_household_data(
    engine: Engine,
    settings: Settings | None = None,
    *,
    embedder: EmbeddingAdapter | None = None,
    store: VectorStoreAdapter | None = None,
    wipe: bool = False,
) -> int:
    """Embed and upsert every household's records; returns points indexed."""
    settings = settings or get_settings()
    if not settings.qdrant_url:
        return 0
    store = store or QdrantVectorStore(settings.qdrant_url)
    embedder = embedder or get_default_embedder()
    if wipe:
        store.wipe_collection(embedder.dim)
    else:
        store.ensure_collection(embedder.dim)

    total = 0
    for household_id in repository.list_households(engine):
        collected = _collect_points(engine, household_id)
        for start in range(0, len(collected), _BATCH):
            batch = collected[start : start + _BATCH]
            vectors = embedder.embed([text for _id, text, _payload in batch])
            store.upsert(
                [
                    VectorPoint(id=point_id, vector=vector, payload=payload)
                    for (point_id, _text, payload), vector in zip(batch, vectors)
                ]
            )
            total += len(batch)
    return total


def run_indexing_once(engine: Engine, settings: Settings | None = None, *, wipe: bool = False) -> None:
    """Worker entry point: never raises."""
    try:
        indexed = index_household_data(engine, settings, wipe=wipe)
        if indexed:
            logger.info("vector index updated: %s points (wipe=%s)", indexed, wipe)
    except Exception:  # noqa: BLE001 — indexing must never kill the worker
        logger.exception("vector indexing failed")

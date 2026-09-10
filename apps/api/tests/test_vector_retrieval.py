"""M69 (ADR 0017): vector indexing + the search_records chat tool."""

import hashlib
from datetime import date, timedelta

import httpx
from sqlalchemy.engine import Engine

from family_cfo_api import ai_tools, fixtures, repository, vector_indexing
from family_cfo_api.config import Settings
from family_cfo_api.vector_store import InMemoryVectorStore, QdrantVectorStore, VectorPoint

_HH = fixtures.DEMO_HOUSEHOLD_ID


class HashEmbedder:
    """Deterministic test embedder: token-overlap similarity, no model."""

    dim = 32

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in text.lower().split():
                digest = int(hashlib.md5(token.encode()).hexdigest(), 16)
                vector[digest % self.dim] += 1.0
            vectors.append(vector)
        return vectors


def _settings() -> Settings:
    return Settings(version="0.1.0", health_check_database=False, qdrant_url="http://qdrant:6333")


def _seed(engine: Engine) -> None:
    account = repository.create_account(
        engine, _HH, name="Checking", account_type="checking", currency="USD"
    )
    repository.create_transaction(
        engine,
        household_id=_HH,
        account_id=account.id,
        occurred_at=date.today() - timedelta(days=30),
        amount_minor=-13_900,
        currency="USD",
        merchant="Blue Fin Swim Academy",
        description="Monthly swim lessons",
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )
    repository.upsert_household_memory(engine, _HH, "kids_count", "We have two kids.")


def test_indexing_embeds_memories_and_transactions(demo_engine: Engine) -> None:
    _seed(demo_engine)
    store = InMemoryVectorStore()

    indexed = vector_indexing.index_household_data(
        demo_engine, _settings(), embedder=HashEmbedder(), store=store
    )

    kinds = {p.payload["kind"] for p in store.points.values()}
    assert indexed >= 4  # seeded fixture txns + ours + the memory
    assert kinds == {"memory", "transaction"}
    swim = [p for p in store.points.values() if "Blue Fin" in p.payload["text"]]
    assert swim and swim[0].payload["amount_display"] == "-USD 139.00"


def test_indexing_disabled_without_url(demo_engine: Engine) -> None:
    settings = Settings(version="0.1.0", health_check_database=False, qdrant_url="")
    assert vector_indexing.index_household_data(demo_engine, settings) == 0


def test_wipe_prunes_stale_points(demo_engine: Engine) -> None:
    _seed(demo_engine)
    store = InMemoryVectorStore()
    store.upsert(
        [VectorPoint(id="stale", vector=[1.0] * 32, payload={"household_id": _HH, "kind": "x"})]
    )

    vector_indexing.index_household_data(
        demo_engine, _settings(), embedder=HashEmbedder(), store=store, wipe=True
    )

    assert "stale" not in store.points


def test_wipe_is_household_scoped(demo_engine: Engine) -> None:
    """#115 review: the prune must clear one household at a time, never the
    collection. The worker and the API each rebuild only the households they
    own, on independent five-minute schedules — a global wipe from either side
    destroys the other's households, and the sealed household whose vectors the
    worker deleted could sit ungrounded until the API's next pass."""
    _seed(demo_engine)
    store = InMemoryVectorStore()
    store.upsert(
        [
            # A household THIS pass does not cover — the other process's.
            VectorPoint(
                id="foreign",
                vector=[1.0] * 32,
                payload={"household_id": "someone-elses-household", "kind": "memory"},
            ),
            VectorPoint(
                id="stale", vector=[1.0] * 32, payload={"household_id": _HH, "kind": "x"}
            ),
        ]
    )

    vector_indexing.index_household_data(
        demo_engine,
        _settings(),
        embedder=HashEmbedder(),
        store=store,
        wipe=True,
        households={_HH},
    )

    assert "stale" not in store.points, "the covered household is pruned"
    assert "foreign" in store.points, "the other process's household is untouched"


def test_wipe_leaves_an_unreadable_household_grounded(demo_engine: Engine, monkeypatch) -> None:
    """A household that turns out to be locked mid-pass must lose nothing: its
    old vectors keep grounding the advisor until a pass that can actually
    rebuild them. The delete deliberately happens only after collection
    succeeds."""
    store = InMemoryVectorStore()
    store.upsert(
        [
            VectorPoint(
                id="old-memory",
                vector=[1.0] * 32,
                payload={"household_id": _HH, "kind": "memory", "text": "old memory"},
            ),
            VectorPoint(
                id="old-transaction",
                vector=[1.0] * 32,
                payload={
                    "household_id": _HH,
                    "kind": "transaction",
                    "text": "old transaction",
                    "amount_display": "USD 99.00",
                },
            ),
        ]
    )

    from family_cfo_api import household_crypto

    def locked(engine, household_id):
        raise household_crypto.HouseholdLockedError(household_id)

    monkeypatch.setattr(vector_indexing, "_collect_points", locked)

    vector_indexing.index_household_data(
        demo_engine, _settings(), embedder=HashEmbedder(), store=store, wipe=True
    )

    assert "old-memory" in store.points
    assert "old-transaction" in store.points


def test_wipe_removes_only_corrupt_transaction_vectors_and_continues(
    demo_engine: Engine, monkeypatch, caplog
) -> None:
    from family_cfo_api import household_crypto

    caplog.set_level("INFO")
    damaged, complete = "damaged-household", "complete-household"
    store = InMemoryVectorStore()
    store.upsert(
        [
            VectorPoint(
                id="old-memory",
                vector=[1.0] * 32,
                payload={
                    "household_id": damaged,
                    "kind": "memory",
                    "text": "remembered preference",
                },
            ),
            VectorPoint(
                id="old-transaction",
                vector=[1.0] * 32,
                payload={
                    "household_id": damaged,
                    "kind": "transaction",
                    "text": "stale private purchase",
                    "amount_display": "USD 99.00",
                },
            ),
            VectorPoint(
                id="foreign-transaction",
                vector=[1.0] * 32,
                payload={
                    "household_id": "foreign-household",
                    "kind": "transaction",
                    "text": "foreign purchase",
                    "amount_display": "USD 88.00",
                },
            ),
        ]
    )
    monkeypatch.setattr(
        repository, "list_households", lambda _engine: [damaged, complete]
    )

    def collect(_engine, household_id):
        if household_id == damaged:
            raise household_crypto.SealedAmountUnreadableError(household_id)
        return [
            (
                "new",
                "complete household text",
                {"household_id": household_id, "kind": "memory"},
            )
        ]

    monkeypatch.setattr(vector_indexing, "_collect_points", collect)

    indexed = vector_indexing.index_household_data(
        demo_engine,
        _settings(),
        embedder=HashEmbedder(),
        store=store,
        wipe=True,
    )

    assert indexed == 1
    assert "old-memory" in store.points
    assert "old-transaction" not in store.points
    assert "foreign-transaction" in store.points
    assert "new" in store.points

    embedder = HashEmbedder()
    monkeypatch.setattr(ai_tools, "_search_backends", lambda _settings: (embedder, store))
    executor = ai_tools.build_executor(demo_engine, damaged, "USD", _settings())
    result = executor("search_records", {"query": "stale private purchase"})
    assert all(match["kind"] != "transaction" for match in result["matches"])
    assert damaged in caplog.text
    assert "retryable=true" in caplog.text
    assert "transactions" not in caplog.text
    assert "amount_minor" not in caplog.text
    assert "ciphertext" not in caplog.text


def test_corrupt_vector_delete_failure_is_reported_and_later_households_continue(
    demo_engine: Engine, monkeypatch, caplog
) -> None:
    from family_cfo_api import household_crypto

    class FailingDeleteStore(InMemoryVectorStore):
        def delete_household_kind(self, household_id: str, kind: str) -> None:
            raise RuntimeError("adapter unavailable")

    damaged, complete = "damaged-household", "complete-household"
    store = FailingDeleteStore()
    monkeypatch.setattr(repository, "list_households", lambda _engine: [damaged, complete])

    def collect(_engine, household_id):
        if household_id == damaged:
            raise household_crypto.SealedAmountUnreadableError(household_id)
        return [
            (
                "new-after-failure",
                "complete household text",
                {"household_id": household_id, "kind": "memory"},
            )
        ]

    monkeypatch.setattr(vector_indexing, "_collect_points", collect)
    caplog.set_level("ERROR")

    indexed = vector_indexing.index_household_data(
        demo_engine,
        _settings(),
        embedder=HashEmbedder(),
        store=store,
        wipe=True,
    )

    assert indexed == 1
    assert "new-after-failure" in store.points
    assert "vector indexing failed: corrupt transaction cleanup" in caplog.text
    assert damaged in caplog.text
    assert "adapter unavailable" not in caplog.text


def test_search_records_tool_finds_the_swim_school(demo_engine: Engine, monkeypatch) -> None:
    _seed(demo_engine)
    store = InMemoryVectorStore()
    embedder = HashEmbedder()
    vector_indexing.index_household_data(
        demo_engine, _settings(), embedder=embedder, store=store
    )
    monkeypatch.setattr(ai_tools, "_search_backends", lambda settings: (embedder, store))

    executor = ai_tools.build_executor(demo_engine, _HH, "USD", _settings())
    result = executor("search_records", {"query": "swim school lessons"})

    top = result["matches"][0]
    assert "Blue Fin" in top["description"]
    assert top["amount"] == "-USD 139.00"
    assert top["kind"] == "transaction"


def test_search_records_reports_lookup_failed(demo_engine: Engine, monkeypatch) -> None:
    def boom(settings):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(ai_tools, "_search_backends", boom)
    executor = ai_tools.build_executor(demo_engine, _HH, "USD", _settings())

    result = executor("search_records", {"query": "anything"})

    assert result["error"] == "lookup_failed"


def test_search_records_tool_absent_without_url(demo_engine: Engine) -> None:
    settings = Settings(version="0.1.0", health_check_database=False, qdrant_url="")
    names = [tool.name for tool in ai_tools.build_tools(settings)]
    assert "search_records" not in names

    executor = ai_tools.build_executor(demo_engine, _HH, "USD", settings)
    assert executor("search_records", {"query": "x"})["error"] == "unknown_tool"


def test_qdrant_adapter_request_shapes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(404)
        if request.url.path.endswith("/points/search"):
            return httpx.Response(
                200,
                json={"result": [{"id": "t1", "score": 0.9, "payload": {"kind": "memory"}}]},
            )
        return httpx.Response(200, json={"result": True})

    store = QdrantVectorStore(
        "http://qdrant:6333", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    store.ensure_collection(32)
    store.upsert([VectorPoint(id="t1", vector=[0.0] * 32, payload={"household_id": _HH})])
    store.delete_household_kind(_HH, "transaction")
    hits = store.search([0.0] * 32, _HH, limit=5)

    assert hits[0].id == "t1" and hits[0].score == 0.9
    paths = [r.url.path for r in seen]
    assert "/collections/household_records/points/search" in paths
    # The search body always filters on the household.
    import json

    body = json.loads(seen[-1].content)
    assert body["filter"]["must"][0]["match"]["value"] == _HH
    delete_request = next(
        request
        for request in seen
        if request.method == "POST" and request.url.path.endswith("/points/delete")
    )
    delete_body = json.loads(delete_request.content)
    predicates = {
        item["key"]: item["match"]["value"]
        for item in delete_body["filter"]["must"]
    }
    assert predicates == {"household_id": _HH, "kind": "transaction"}

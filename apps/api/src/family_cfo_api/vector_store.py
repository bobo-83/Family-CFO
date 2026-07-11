"""Vector store behind a replaceable seam (M69, ADR 0017).

The Qdrant implementation speaks plain REST via httpx — no client library.
Every search filters on household_id; points are keyed by the source row's
uuid so re-indexing is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

_TIMEOUT_SECONDS = 20.0

COLLECTION = "household_records"


@dataclass(frozen=True, slots=True)
class VectorPoint:
    id: str  # source row uuid
    vector: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VectorHit:
    id: str
    score: float
    payload: dict[str, Any]


class VectorStoreAdapter(Protocol):
    def ensure_collection(self, dim: int) -> None: ...

    def wipe_collection(self, dim: int) -> None: ...

    def upsert(self, points: list[VectorPoint]) -> None: ...

    def search(self, vector: list[float], household_id: str, limit: int) -> list[VectorHit]: ...


class QdrantVectorStore:
    def __init__(self, base_url: str, *, client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=_TIMEOUT_SECONDS)

    def _url(self, path: str) -> str:
        return f"{self._base}/collections/{COLLECTION}{path}"

    def ensure_collection(self, dim: int) -> None:
        exists = self._client.get(self._url(""))
        if exists.status_code == 200:
            return
        response = self._client.put(
            self._url(""),
            json={"vectors": {"size": dim, "distance": "Cosine"}},
        )
        response.raise_for_status()

    def wipe_collection(self, dim: int) -> None:
        self._client.delete(self._url(""))
        self.ensure_collection(dim)

    def upsert(self, points: list[VectorPoint]) -> None:
        if not points:
            return
        response = self._client.put(
            self._url("/points?wait=true"),
            json={
                "points": [
                    {"id": p.id, "vector": p.vector, "payload": p.payload} for p in points
                ]
            },
        )
        response.raise_for_status()

    def search(self, vector: list[float], household_id: str, limit: int) -> list[VectorHit]:
        response = self._client.post(
            self._url("/points/search"),
            json={
                "vector": vector,
                "limit": limit,
                "with_payload": True,
                "filter": {
                    "must": [{"key": "household_id", "match": {"value": household_id}}]
                },
            },
        )
        response.raise_for_status()
        return [
            VectorHit(id=str(hit["id"]), score=hit["score"], payload=hit.get("payload") or {})
            for hit in response.json()["result"]
        ]


@dataclass
class InMemoryVectorStore:
    """Test double: exact cosine search over a dict. Not for production."""

    points: dict[str, VectorPoint] = field(default_factory=dict)

    def ensure_collection(self, dim: int) -> None:  # noqa: ARG002 - protocol shape
        return

    def wipe_collection(self, dim: int) -> None:  # noqa: ARG002
        self.points.clear()

    def upsert(self, points: list[VectorPoint]) -> None:
        for point in points:
            self.points[point.id] = point

    def search(self, vector: list[float], household_id: str, limit: int) -> list[VectorHit]:
        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = sum(x * x for x in a) ** 0.5
            norm_b = sum(y * y for y in b) ** 0.5
            return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

        hits = [
            VectorHit(id=p.id, score=cosine(vector, p.vector), payload=p.payload)
            for p in self.points.values()
            if p.payload.get("household_id") == household_id
        ]
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

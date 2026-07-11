"""Local text embeddings behind a replaceable seam (M69, ADR 0017).

CPU-only and fully on-box: fastembed runs a small ONNX model, so embedding
household text never leaves the server and never competes with the chat
model for GPU memory.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384


class EmbeddingAdapter(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastembedEmbedder:
    """fastembed-backed adapter; the ONNX model loads lazily on first use."""

    dim = EMBEDDING_DIM

    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(model_name=EMBEDDING_MODEL)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        return [vector.tolist() for vector in model.embed(texts)]


_default: FastembedEmbedder | None = None
_default_lock = threading.Lock()


def get_default_embedder() -> FastembedEmbedder:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = FastembedEmbedder()
    return _default

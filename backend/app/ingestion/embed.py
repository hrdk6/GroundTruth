"""Embedding. Local, CPU, batched.

One asymmetry matters and is easy to get wrong: **bge retrieval models are
trained with an instruction prefix on the query side only.** Embedding documents
with the query prefix measurably degrades retrieval, so `embed_documents` and
`embed_query` are separate methods rather than one method with a flag that
somebody eventually passes wrong.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from app.core.logging import get_logger
from app.core.pipeline import EmbeddingConfig

log = get_logger(__name__)


class Embedder:
    """sentence-transformers encoder, loaded lazily and cached per process.

    Loading takes seconds to tens of seconds (torch, then the weights), and the
    API answers on worker threads, so the load is guarded: concurrent first
    queries wait for one load instead of each loading a copy into memory.
    """

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        self._model: Any | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self.config.model

    @property
    def dimension(self) -> int:
        return self.config.dimension

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                self._model = self._load_model()
        return self._model

    def _load_model(self) -> Any:
        from sentence_transformers import SentenceTransformer

        started = time.perf_counter()
        log.info("embed.loading_model", model=self.config.model)
        model = SentenceTransformer(self.config.model, device="cpu")
        # Renamed in sentence-transformers 5; keep working on either side.
        dimension_of = getattr(model, "get_embedding_dimension", None)
        actual = dimension_of() if dimension_of else model.get_sentence_embedding_dimension()
        if actual != self.config.dimension:
            raise ValueError(
                f"Config says dimension={self.config.dimension} but "
                f"{self.config.model} produces {actual}. The `chunks.embedding` "
                f"column is typed, so this needs a migration, not a config edit."
            )
        log.info(
            "embed.model_loaded",
            model=self.config.model,
            seconds=round(time.perf_counter() - started, 1),
        )
        return model

    def warm(self) -> None:
        """Load the model now, so the first query does not pay for it."""
        self._load()

    def _encode(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        vectors = model.encode(
            texts,
            batch_size=self.config.batch_size,
            normalize_embeddings=self.config.normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        prefixed = [f"{self.config.document_prefix}{t}" for t in texts]
        log.debug("embed.documents", count=len(prefixed))
        return [v.tolist() for v in self._encode(prefixed)]

    def embed_query(self, text: str) -> list[float]:
        """Single query, with the instruction prefix the model expects."""
        return self.embed_queries([text])[0]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        prefixed = [f"{self.config.query_prefix}{t}" for t in texts]
        return [v.tolist() for v in self._encode(prefixed)]


_cache: dict[tuple[str, int], Embedder] = {}
_cache_lock = threading.Lock()


def get_embedder(config: EmbeddingConfig) -> Embedder:
    """Shared embedder per (model, dimension); loading weights is expensive."""
    key = (config.model, config.dimension)
    with _cache_lock:
        if key not in _cache:
            _cache[key] = Embedder(config)
        return _cache[key]

"""Embedding. Local, CPU, batched.

One asymmetry matters and is easy to get wrong: **bge retrieval models are
trained with an instruction prefix on the query side only.** Embedding documents
with the query prefix measurably degrades retrieval, so `embed_documents` and
`embed_query` are separate methods rather than one method with a flag that
somebody eventually passes wrong.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.core.logging import get_logger
from app.core.pipeline import EmbeddingConfig

log = get_logger(__name__)


class Embedder:
    """sentence-transformers encoder, loaded lazily and cached per process."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        self._model: Any | None = None

    @property
    def model_name(self) -> str:
        return self.config.model

    @property
    def dimension(self) -> int:
        return self.config.dimension

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            log.info("embed.loading_model", model=self.config.model)
            self._model = SentenceTransformer(self.config.model, device="cpu")
            # Renamed in sentence-transformers 5; keep working on either side.
            model = self._model
            dimension_of = getattr(model, "get_embedding_dimension", None)
            actual = dimension_of() if dimension_of else model.get_sentence_embedding_dimension()
            if actual != self.config.dimension:
                raise ValueError(
                    f"Config says dimension={self.config.dimension} but "
                    f"{self.config.model} produces {actual}. The `chunks.embedding` "
                    f"column is typed, so this needs a migration, not a config edit."
                )
        return self._model

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


def get_embedder(config: EmbeddingConfig) -> Embedder:
    """Shared embedder per (model, dimension); loading weights is expensive."""
    key = (config.model, config.dimension)
    if key not in _cache:
        _cache[key] = Embedder(config)
    return _cache[key]

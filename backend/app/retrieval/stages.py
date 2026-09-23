"""Retrieval stages: dense, lexical, fusion, rerank.

Each is a plain function over `Candidate` lists so the orchestrator in
`retriever.py` can switch one on or off from config, and so each stage can be
tested without a pipeline around it.

The version filter is applied **in SQL**, as a pre-filter. Filtering after
retrieval would silently shrink `k`: ask for 20 chunks about 1.26, get 20 across
all versions, discard 14, and retrieve with 6. Pre-filtering keeps `k` honest.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Select, and_, bindparam, cast, func, literal, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.pipeline import PipelineConfig
from app.models import Chunk, Document
from app.retrieval.base import Candidate

log = get_logger(__name__)


def _base_query(config: PipelineConfig, version: str | None) -> Select[Any]:
    """Chunk+document select with the chunker and version pre-filters applied."""
    query = select(
        Chunk.id,
        Chunk.document_id,
        Chunk.text,
        Chunk.heading_path,
        Chunk.chunk_type,
        Chunk.token_count,
        Chunk.version,
        Document.source_path,
        Document.title,
        Document.url,
    ).join(Document, Document.id == Chunk.document_id)

    # Only chunks produced by the active chunking strategy are eligible, so
    # several chunkings can coexist without contaminating each other's results.
    conditions = [Chunk.chunker_name == config.chunker_name, Document.deleted_at.is_(None)]
    if config.retrieval.version_filter and version:
        conditions.append(Chunk.version == version)
    return query.where(and_(*conditions))


def _to_candidate(row: Any, stage: str, score: float, rank: int) -> Candidate:
    return Candidate(
        chunk_id=row.id,
        document_id=row.document_id,
        text=row.text,
        heading_path=row.heading_path or "",
        source_path=row.source_path,
        version=row.version,
        title=row.title or "",
        url=row.url or "",
        chunk_type=row.chunk_type or "prose",
        token_count=row.token_count or 0,
        scores={stage: score},
        ranks={stage: rank},
    )


# ---------------------------------------------------------------------------
# Dense
# ---------------------------------------------------------------------------
def dense_search(
    session: Session,
    query_vector: list[float],
    config: PipelineConfig,
    *,
    version: str | None = None,
    k: int | None = None,
) -> list[Candidate]:
    """Cosine similarity over the HNSW index."""
    limit = k or config.retrieval.dense.k

    # pgvector exposes distance; similarity is 1 - distance for cosine.
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    statement = (
        _base_query(config, version)
        .add_columns(distance)
        .where(Chunk.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )

    rows = session.execute(statement).all()
    return [
        _to_candidate(row, "dense", 1.0 - float(row.distance), rank)
        for rank, row in enumerate(rows, start=1)
    ]


# ---------------------------------------------------------------------------
# Lexical
# ---------------------------------------------------------------------------
def lexical_search(
    session: Session,
    query_text: str,
    config: PipelineConfig,
    *,
    version: str | None = None,
    k: int | None = None,
) -> list[Candidate]:
    """Postgres full-text search ranked by `ts_rank_cd`.

    This is BM25-*like*, not BM25: `ts_rank_cd` weights cover density and term
    frequency but has neither IDF saturation nor document-length normalization.
    Documented in docs/LIMITATIONS.md (L2); ParadeDB `pg_search` is the upgrade
    path if lexical recall turns out to be the bottleneck.
    """
    limit = k or config.retrieval.lexical.k
    regconfig = config.retrieval.lexical.text_search_config

    cleaned = _to_tsquery_input(query_text)
    if not cleaned:
        return []

    # websearch_to_tsquery tolerates arbitrary user input (quotes, operators,
    # punctuation) instead of raising the way to_tsquery does.
    #
    # The config name must be cast to `regconfig`: passed as a plain string it
    # binds as varchar, and Postgres has no
    # websearch_to_tsquery(varchar, varchar) overload.
    tsquery = func.websearch_to_tsquery(
        cast(literal(regconfig), REGCONFIG), bindparam("q", cleaned)
    )
    rank = func.ts_rank_cd(Chunk.tsv, tsquery).cast(Float).label("rank")

    statement = (
        _base_query(config, version)
        .add_columns(rank)
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(limit)
    )

    rows = session.execute(statement).all()
    return [
        _to_candidate(row, "lexical", float(row.rank), position)
        for position, row in enumerate(rows, start=1)
    ]


def _to_tsquery_input(text: str) -> str:
    """Strip characters that make websearch_to_tsquery return an empty query."""
    cleaned = " ".join(text.split())
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------
def reciprocal_rank_fusion(
    result_sets: list[list[Candidate]], *, rrf_k: int = 60
) -> list[Candidate]:
    """Combine ranked lists by Reciprocal Rank Fusion.

    RRF scores by `1 / (k + rank)`, which means it only ever compares *ranks*.
    That is the point: dense cosine similarity (0-1) and `ts_rank_cd`
    (unbounded, corpus-dependent) are not on a comparable scale, so any
    weighted sum of the raw scores would be dominated by whichever happened to
    be larger. Rank is the only thing both lists agree on.
    """
    merged: dict[int, Candidate] = {}

    for candidates in result_sets:
        for rank, candidate in enumerate(candidates, start=1):
            existing = merged.get(candidate.chunk_id)
            if existing is None:
                existing = candidate
                merged[candidate.chunk_id] = existing
            else:
                # Same chunk found by another leg: keep every stage's evidence.
                existing.scores.update(candidate.scores)
                existing.ranks.update(candidate.ranks)
            existing.scores["rrf"] = existing.scores.get("rrf", 0.0) + 1.0 / (rrf_k + rank)

    fused = sorted(merged.values(), key=lambda c: c.scores.get("rrf", 0.0), reverse=True)
    for position, candidate in enumerate(fused, start=1):
        candidate.ranks["rrf"] = position
    return fused


def merge_unique(result_sets: list[list[Candidate]]) -> list[Candidate]:
    """Deduplicate by chunk id, keeping the best-scoring copy.

    Used when several sub-queries retrieve independently and there is no
    meaningful cross-list rank to fuse on.
    """
    merged: dict[int, Candidate] = {}
    for candidates in result_sets:
        for candidate in candidates:
            existing = merged.get(candidate.chunk_id)
            if existing is None:
                merged[candidate.chunk_id] = candidate
            elif candidate.score > existing.score:
                candidate.scores = {**existing.scores, **candidate.scores}
                merged[candidate.chunk_id] = candidate
    return sorted(merged.values(), key=lambda c: c.score, reverse=True)


# ---------------------------------------------------------------------------
# Rerank
# ---------------------------------------------------------------------------
class CrossEncoderReranker:
    """Cross-encoder reranking over the fused candidate set.

    A bi-encoder embeds the query and the chunk separately, so it never sees
    them together. A cross-encoder scores the pair jointly, which is far more
    accurate and far more expensive -- affordable precisely because it only ever
    runs over the handful of candidates the cheap stages surfaced.
    """

    def __init__(self, model_name: str, batch_size: int = 32) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self._model: object | None = None

    def _load(self) -> object:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            log.info("rerank.loading_model", model=self.model_name)
            self._model = CrossEncoder(self.model_name, device="cpu")
        return self._model

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        """Every candidate, re-ordered. The caller decides where to cut.

        Returning the full ordering rather than the top k is what lets the
        evaluation measure recall@10 after reranking, and lets a trace show a
        chunk the reranker demoted to rank 17 instead of making it vanish.
        """
        if not candidates:
            return []

        model = self._load()
        pairs = [(query, c.text) for c in candidates]
        scores = model.predict(pairs, batch_size=self.batch_size, show_progress_bar=False)  # type: ignore[attr-defined]

        for candidate, score in zip(candidates, scores, strict=True):
            candidate.scores["rerank"] = float(score)

        ranked = sorted(candidates, key=lambda c: c.scores["rerank"], reverse=True)
        for position, candidate in enumerate(ranked, start=1):
            candidate.ranks["rerank"] = position
        return ranked


_reranker_cache: dict[str, CrossEncoderReranker] = {}


def get_reranker(model_name: str, batch_size: int = 32) -> CrossEncoderReranker:
    if model_name not in _reranker_cache:
        _reranker_cache[model_name] = CrossEncoderReranker(model_name, batch_size)
    return _reranker_cache[model_name]

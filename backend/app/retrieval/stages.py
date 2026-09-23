"""Retrieval stages: dense, lexical, fusion, rerank.

Each is a plain function over `Candidate` lists so the orchestrator in
`retriever.py` can switch one on or off from config, and so each stage can be
tested without a pipeline around it.

The version and chunk-set filters are applied **in SQL**, because filtering
after retrieval silently shrinks `k`: ask for 20 chunks about 1.26, get 20
across all versions, discard 14, and retrieve with 6.

SQL alone does not make that true for the dense leg, though. An HNSW index scan
in pgvector < 0.8 returns only its `ef_search` nearest neighbours (default 40)
from the *whole* index, and the `WHERE` clause filters those -- so it is a
post-filter after all. With several chunk sets and versions sharing one index,
a query matching 10% of rows got back about 4 of the 20 chunks it asked for,
and nothing reported it. `dense_search` therefore widens `ef_search` per query
and falls back to an exact scan when the index still comes back short.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Select, and_, bindparam, cast, func, literal, select, text
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import BindParameter

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
    """Cosine similarity over the HNSW index, guaranteed to return `k` when `k` exist."""
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

    # SET LOCAL takes no bind parameters; the value is a validated int.
    ef_search = max(int(config.retrieval.dense.ef_search), limit)
    session.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))
    rows = session.execute(statement).all()

    if len(rows) < limit:
        # The index scan came back short -- the filters rejected most of its
        # candidates -- or there really are fewer than `limit` matching rows.
        # An exact scan answers both, and at this corpus size costs tens of
        # milliseconds. Logged, because a frequent fallback means ef_search is
        # too small for how many chunk sets share the index.
        session.execute(text("SET LOCAL enable_indexscan = off"))
        exact = session.execute(statement).all()
        session.execute(text("SET LOCAL enable_indexscan = on"))
        if len(exact) > len(rows):
            log.info(
                "dense.exact_fallback",
                ann_rows=len(rows),
                exact_rows=len(exact),
                limit=limit,
                ef_search=ef_search,
            )
            rows = exact

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
    """Postgres full-text search, ranked by `ts_rank_cd` or by BM25.

    `ts_rank_cd` is BM25-*like*, not BM25: it weights cover density and term
    frequency, has no IDF at all, and -- called without a normalization flag,
    as here -- no document-length normalization either (docs/LIMITATIONS.md,
    L2). `lexical.ranking: bm25` computes Okapi BM25 instead; see `_bm25`.
    """
    limit = k or config.retrieval.lexical.k
    regconfig = config.retrieval.lexical.text_search_config

    cleaned = _to_tsquery_input(query_text)
    if not cleaned:
        return []

    if config.retrieval.lexical.ranking == "bm25":
        return _bm25(session, cleaned, config, version=version, limit=limit)

    # The config name must be cast to `regconfig`: passed as a plain string it
    # binds as varchar, and Postgres has no
    # websearch_to_tsquery(varchar, varchar) overload.
    language = cast(literal(regconfig), REGCONFIG)
    question: BindParameter[str] = bindparam("q", cleaned)

    if config.retrieval.lexical.match == "any":
        # OR together the question's own lexemes, stemmed by the same config
        # that built `tsv`. Built from `to_tsvector` output, so user text is
        # never parsed as query syntax: nothing can be negated, and nothing
        # can fail to parse.
        tsquery = func.to_tsquery(
            language,
            func.array_to_string(
                func.tsvector_to_array(func.to_tsvector(language, question)), " | "
            ),
        )
    else:
        # websearch_to_tsquery tolerates arbitrary input instead of raising
        # the way to_tsquery does -- but ANDs every term and reads a leading
        # `-` as NOT. See LexicalConfig.match.
        tsquery = func.websearch_to_tsquery(language, question)
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
    """Collapse whitespace; an empty question skips the query entirely."""
    return " ".join(text.split())


# Matching chunks are the ones `lexical.match` admits; scoring always uses the
# question's own lexemes. `{match}` is one of the two fixed expressions below,
# never user input.
_MATCH_ANY = (
    "to_tsquery(CAST(:language AS regconfig), array_to_string("
    "tsvector_to_array(to_tsvector(CAST(:language AS regconfig), :question)), ' | '))"
)
_MATCH_ALL = "websearch_to_tsquery(CAST(:language AS regconfig), :question)"

_BM25_SQL = """
WITH terms AS (
    SELECT DISTINCT lexeme
    FROM unnest(tsvector_to_array(to_tsvector(CAST(:language AS regconfig), :question))) AS lexeme
),
scope AS (
    SELECT c.id, c.tsv
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.chunker_name = :chunker_name
      AND d.deleted_at IS NULL
      AND (CAST(:version AS text) IS NULL OR c.version = CAST(:version AS text))
),
corpus AS (
    SELECT count(*)::float8 AS n, greatest(avg(length(tsv)), 1)::float8 AS avg_len FROM scope
),
df AS (
    SELECT t.lexeme, count(*)::float8 AS df
    FROM terms t
    JOIN scope s ON s.tsv @@ quote_literal(t.lexeme)::tsquery
    GROUP BY t.lexeme
),
hits AS (
    SELECT id, tsv FROM scope WHERE tsv @@ {match}
),
scored AS (
    SELECT h.id,
           sum(
               ln(1 + (corpus.n - df.df + 0.5) / (df.df + 0.5))
               * u.tf * (:k1 + 1)
               / (u.tf + :k1 * (1 - :b + :b * length(h.tsv) / corpus.avg_len))
           ) AS score
    FROM hits h
    CROSS JOIN LATERAL (
        SELECT e.lexeme, coalesce(array_length(e.positions, 1), 1)::float8 AS tf
        FROM unnest(h.tsv) AS e(lexeme, positions, weights)
    ) u
    JOIN df ON df.lexeme = u.lexeme
    CROSS JOIN corpus
    GROUP BY h.id
)
SELECT c.id, c.document_id, c.text, c.heading_path, c.chunk_type, c.token_count, c.version,
       d.source_path, d.title, d.url, s.score AS rank
FROM scored s
JOIN chunks c ON c.id = s.id
JOIN documents d ON d.id = c.document_id
ORDER BY s.score DESC, c.id
LIMIT :k
"""


def _bm25(
    session: Session, question: str, config: PipelineConfig, *, version: str | None, limit: int
) -> list[Candidate]:
    """Okapi BM25 over the filtered chunk set, in one SQL statement.

    Everything BM25 needs is derivable from the stored `tsvector`, so no
    extension and no side table: `N` and the mean length come from the scope
    (active chunk set, live documents, requested version), each term's
    document frequency from a GIN-backed `@@` count, and its frequency in a
    chunk from `unnest(tsv)`'s position list.

        score = sum over query terms t in the chunk of
                idf(t) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len / avg_len))
        idf(t) = ln(1 + (N - df + 0.5) / (df + 0.5))

    Two approximations, both documented in LIMITATIONS: `len` is the chunk's
    count of *distinct* lexemes (`length(tsvector)`), not its token count; and
    Postgres keeps at most 256 positions per lexeme, which caps `tf` -- far
    beyond where BM25's saturation has already flattened it.
    """
    lexical = config.retrieval.lexical
    scoped_version = version if config.retrieval.version_filter else None
    statement = text(_BM25_SQL.format(match=_MATCH_ANY if lexical.match == "any" else _MATCH_ALL))
    rows = session.execute(
        statement,
        {
            "language": lexical.text_search_config,
            "question": question,
            "chunker_name": config.chunker_name,
            "version": scoped_version,
            "k1": lexical.bm25_k1,
            "b": lexical.bm25_b,
            "k": limit,
        },
    ).all()
    return [
        _to_candidate(row, "lexical", float(row.rank), position)
        for position, row in enumerate(rows, start=1)
    ]


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

"""The retrieval orchestrator: config in, ranked candidates out.

This is the single place that knows the stage order. Every stage is gated by
config, so each Phase 3 experiment is one boolean in a YAML file rather than a
code change -- which is what makes the before/after comparison trustworthy.

Order, and why:

1. **Version resolution** -- decides the SQL pre-filter, so it must come first.
2. **Decomposition** -- may replace one query with several.
3. **Rewriting** -- applied per (sub)query, after decomposition so each part
   gets expanded on its own terms.
4. **Dense and lexical** -- run independently over the same filtered set.
5. **Fusion** -- RRF, but only when both legs ran.
6. **Rerank** -- cross-encoder against the *original* question, never the
   rewritten one: the rewrite exists to help recall, and judging relevance
   against a machine-expanded query would reward the rewrite rather than the
   user's actual intent.

Every stage records its output and timing so a trace can show exactly where a
gold chunk was lost.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.core.llm import LLMClient, get_llm_client
from app.core.logging import get_logger
from app.core.pipeline import PipelineConfig
from app.ingestion.embed import get_embedder
from app.retrieval.base import Candidate, RetrievalResult
from app.retrieval.rewrite import decompose_query, rewrite_query
from app.retrieval.stages import (
    dense_search,
    get_reranker,
    lexical_search,
    merge_unique,
    reciprocal_rank_fusion,
)
from app.retrieval.versioning import VersionDecision, indexed_versions, resolve_version

log = get_logger(__name__)


class Retriever:
    """Runs the configured retrieval stages over one Postgres session."""

    def __init__(
        self,
        config: PipelineConfig,
        *,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.config = config
        self._llm = llm_client
        self.embedder = get_embedder(config.embedding)

    @property
    def llm(self) -> LLMClient:
        # Only built when a stage actually needs a model, so retrieval-only
        # runs never require an API key.
        if self._llm is None:
            self._llm = get_llm_client()
        return self._llm

    def retrieve(
        self,
        session: Session,
        question: str,
        *,
        version: str | None = None,
        trace: object | None = None,
    ) -> tuple[RetrievalResult, VersionDecision]:
        timings: dict[str, float] = {}
        stage_outputs: dict[str, list[Candidate]] = {}
        retrieval = self.config.retrieval

        @contextmanager
        def timed(name: str) -> Iterator[None]:
            started = time.perf_counter()
            try:
                yield
            finally:
                timings[name] = (time.perf_counter() - started) * 1000

        # --- 1. version ---------------------------------------------------
        with timed("version_detection"):
            available = indexed_versions(session, self.config.chunker_name)
            decision = resolve_version(
                question,
                available=available,
                requested=version,
                default=self.config.versioning.default_version,
                detect=self.config.versioning.detect_from_question,
            )

        # --- 2. decomposition ---------------------------------------------
        subqueries: list[str] = []
        if retrieval.decomposition.enabled:
            with timed("decomposition"):
                decomposition = decompose_query(
                    self.llm,
                    question,
                    model=retrieval.decomposition.model,
                    max_subqueries=retrieval.decomposition.max_subqueries,
                )
            subqueries = decomposition.subqueries

        search_queries = subqueries or [question]

        # --- 3. rewriting ---------------------------------------------------
        rewritten_query: str | None = None
        dense_queries: list[str] = []
        lexical_queries: list[str] = []

        for query in search_queries:
            if retrieval.query_rewrite.enabled:
                with timed("query_rewrite"):
                    rewrite = rewrite_query(self.llm, query, model=retrieval.query_rewrite.model)
                dense_queries.append(rewrite.rewritten)
                if retrieval.query_rewrite.keep_original_for_lexical:
                    # Both forms go to lexical search: the rewrite adds likely
                    # exact terms, the original keeps the ones the user typed.
                    lexical_queries.extend([rewrite.rewritten, query])
                else:
                    lexical_queries.append(rewrite.rewritten)
                if query == question:
                    rewritten_query = rewrite.rewritten
            else:
                dense_queries.append(query)
                lexical_queries.append(query)

        # --- 4. dense + lexical ---------------------------------------------
        result_sets: list[list[Candidate]] = []

        if retrieval.dense.enabled:
            with timed("dense"):
                vectors = self.embedder.embed_queries(dense_queries)
                dense_results = []
                for query, vector in zip(dense_queries, vectors, strict=True):
                    hits = dense_search(session, vector, self.config, version=decision.version)
                    if len(dense_queries) > 1:
                        for hit in hits:
                            hit.origin = query
                    dense_results.append(hits)
                dense_merged = (
                    dense_results[0] if len(dense_results) == 1 else merge_unique(dense_results)
                )
                stage_outputs["dense"] = dense_merged
                result_sets.append(dense_merged)

        if retrieval.lexical.enabled:
            with timed("lexical"):
                lexical_results = []
                for query in dict.fromkeys(lexical_queries):  # dedupe, keep order
                    hits = lexical_search(session, query, self.config, version=decision.version)
                    if len(lexical_queries) > 1:
                        for hit in hits:
                            hit.origin = query
                    lexical_results.append(hits)
                lexical_merged = (
                    lexical_results[0]
                    if len(lexical_results) == 1
                    else merge_unique(lexical_results)
                )
                stage_outputs["lexical"] = lexical_merged
                result_sets.append(lexical_merged)

        # --- 5. fusion --------------------------------------------------------
        if retrieval.fusion.enabled and len(result_sets) > 1:
            with timed("fusion"):
                candidates = reciprocal_rank_fusion(result_sets, rrf_k=retrieval.fusion.rrf_k)
                stage_outputs["fusion"] = candidates
        elif len(result_sets) > 1:
            candidates = merge_unique(result_sets)
        elif result_sets:
            candidates = result_sets[0]
        else:
            candidates = []

        # --- 6. rerank ---------------------------------------------------------
        if retrieval.rerank.enabled and candidates:
            with timed("rerank"):
                reranker = get_reranker(retrieval.rerank.model, retrieval.rerank.batch_size)
                # Against the original question, deliberately: see module docstring.
                candidates = reranker.rerank(question, candidates, top_k=retrieval.k_final)
                stage_outputs["rerank"] = candidates
        else:
            candidates = candidates[: retrieval.k_final]

        result = RetrievalResult(
            candidates=candidates,
            query=question,
            version=decision.version,
            rewritten_query=rewritten_query,
            subqueries=subqueries,
            stage_outputs=stage_outputs,
            timings_ms=timings,
        )

        log.debug(
            "retrieve.complete",
            question=question[:80],
            version=decision.version,
            candidates=len(candidates),
            stages=list(stage_outputs),
        )
        return result, decision

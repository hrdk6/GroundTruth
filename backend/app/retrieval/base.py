"""Shared retrieval types.

Every stage produces and consumes `Candidate`, and each stage *adds* its score
under its own name rather than overwriting a single `score` field. That is what
makes a ranking miss diagnosable later: a trace can show that a chunk was rank 3
after dense retrieval, rank 1 after fusion, and rank 12 after reranking, which
is exactly the evidence the failure-attribution step needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    """One retrieved chunk, with the scores every stage gave it."""

    chunk_id: int
    document_id: int
    text: str
    heading_path: str
    source_path: str
    version: str
    title: str = ""
    url: str = ""
    chunk_type: str = "prose"
    token_count: int = 0

    # Stage name -> score. Keys used: dense, lexical, rrf, rerank.
    scores: dict[str, float] = field(default_factory=dict)
    # Stage name -> 1-based rank in that stage's output.
    ranks: dict[str, int] = field(default_factory=dict)
    # Which sub-query surfaced this, when decomposition is on.
    origin: str | None = None

    @property
    def score(self) -> float:
        """The score that decided the final ordering, newest stage first."""
        for stage in ("rerank", "rrf", "dense", "lexical"):
            if stage in self.scores:
                return self.scores[stage]
        return 0.0

    def citation_label(self) -> str:
        """What a reader needs to verify the claim: page, version, section."""
        parts = [self.source_path, f"v{self.version}"]
        if self.heading_path:
            parts.append(self.heading_path)
        return " | ".join(parts)

    def to_dict(self, *, include_text: bool = True, max_text: int = 2000) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source_path": self.source_path,
            "version": self.version,
            "title": self.title,
            "url": self.url,
            "heading_path": self.heading_path,
            "chunk_type": self.chunk_type,
            "token_count": self.token_count,
            "scores": {k: round(v, 6) for k, v in self.scores.items()},
            "ranks": dict(self.ranks),
        }
        if self.origin:
            payload["origin"] = self.origin
        if include_text:
            payload["text"] = self.text[:max_text]
        return payload


@dataclass
class RetrievalResult:
    """Final candidates plus the intermediate sets that produced them.

    Two lists, and the difference is load-bearing for evaluation:

    * `candidates` is the **context** -- the top `k_final` chunks the model is
      shown. Attribution asks whether gold made it *here*.
    * `ranked` is the **full final ordering** before that cut. Ranking metrics
      (recall@10, MRR, nDCG@10) are computed over it. Computing them over the
      five-chunk context instead makes recall@10 identical to recall@5 by
      construction -- a column that looks measured and carries no information.
    """

    candidates: list[Candidate]
    query: str
    version: str | None = None
    rewritten_query: str | None = None
    subqueries: list[str] = field(default_factory=list)
    # Stage name -> candidates that stage emitted, kept for attribution.
    stage_outputs: dict[str, list[Candidate]] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
    ranked: list[Candidate] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.ranked:
            self.ranked = list(self.candidates)

    def chunk_ids(self) -> list[int]:
        return [c.chunk_id for c in self.candidates]

    def all_candidate_ids(self) -> set[int]:
        """Every chunk any stage saw.

        Distinguishes `retrieval_miss` (gold never appeared anywhere) from
        `ranking_miss` (gold appeared but was dropped before the final cut).
        """
        seen = {c.chunk_id for c in self.candidates}
        for stage in self.stage_outputs.values():
            seen.update(c.chunk_id for c in stage)
        return seen

"""Retrieval metrics: Recall@k, MRR, nDCG@k.

All of them reduce to one question -- *at which ranks did gold evidence
appear?* -- so `relevance_ranks` does the matching once and each metric is a
few lines over its output.

Matching is by `gold_evidence`, never by chunk id, for the reason in
`evals/dataset/schema.py`: ids do not survive re-chunking, and Phase 3
re-chunks on purpose.

An item may carry several pieces of gold evidence (a multi-hop question needs
two pages). Recall@k asks whether *all* required evidence is present, because
retrieving half of what a question needs does not let the model answer it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.retrieval.base import Candidate
from evals.dataset.schema import GoldEvidence


@dataclass
class MatchResult:
    """Where each piece of gold evidence turned up in the ranking."""

    # Gold index -> 1-based rank of the first candidate matching it.
    first_rank: dict[int, int] = field(default_factory=dict)
    # Every rank that matched any gold, for nDCG.
    relevant_ranks: list[int] = field(default_factory=list)
    total_gold: int = 0

    @property
    def best_rank(self) -> int | None:
        return min(self.first_rank.values()) if self.first_rank else None

    def covered(self) -> int:
        return len(self.first_rank)

    def all_found_within(self, k: int) -> bool:
        if not self.total_gold:
            return False
        return sum(1 for r in self.first_rank.values() if r <= k) == self.total_gold


def match_candidates(candidates: list[Candidate], gold: list[GoldEvidence]) -> MatchResult:
    """Find the rank at which each piece of gold evidence first appears."""
    result = MatchResult(total_gold=len(gold))
    if not gold:
        return result

    for rank, candidate in enumerate(candidates, start=1):
        matched_any = False
        for gold_index, evidence in enumerate(gold):
            if evidence.matches(
                source_path=candidate.source_path,
                version=candidate.version,
                heading_path=candidate.heading_path,
                text=candidate.text,
            ):
                matched_any = True
                result.first_rank.setdefault(gold_index, rank)
        if matched_any:
            result.relevant_ranks.append(rank)

    return result


def recall_at_k(match: MatchResult, k: int) -> float:
    """1.0 when every required piece of evidence is in the top k.

    Strict by design: a multi-hop question with one of its two pages retrieved
    is not answerable, so scoring it 0.5 would overstate the system.
    """
    return 1.0 if match.all_found_within(k) else 0.0


def partial_recall_at_k(match: MatchResult, k: int) -> float:
    """Fraction of gold evidence within the top k. Diagnostic, not headline."""
    if not match.total_gold:
        return 0.0
    return sum(1 for r in match.first_rank.values() if r <= k) / match.total_gold


def reciprocal_rank(match: MatchResult) -> float:
    best = match.best_rank
    return 1.0 / best if best else 0.0


def ndcg_at_k(match: MatchResult, k: int) -> float:
    """nDCG with binary relevance.

    The ideal ranking puts all `total_gold` relevant chunks in the top
    positions, so IDCG sums the first `min(total_gold, k)` discounts.
    """
    if not match.total_gold:
        return 0.0

    dcg = sum(1.0 / math.log2(rank + 1) for rank in match.relevant_ranks if rank <= k)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(match.total_gold, k)))
    return dcg / ideal if ideal else 0.0


@dataclass
class RetrievalMetrics:
    """Aggregated retrieval metrics over a set of items."""

    count: int = 0
    recall_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0
    ndcg_at_10: float = 0.0
    partial_recall_at_10: float = 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "recall@1": round(self.recall_at_1, 4),
            "recall@5": round(self.recall_at_5, 4),
            "recall@10": round(self.recall_at_10, 4),
            "mrr": round(self.mrr, 4),
            "ndcg@10": round(self.ndcg_at_10, 4),
            "partial_recall@10": round(self.partial_recall_at_10, 4),
        }


def aggregate_retrieval(matches: list[MatchResult]) -> RetrievalMetrics:
    """Mean of each metric. Items with no gold (unanswerable) must be excluded."""
    scored = [m for m in matches if m.total_gold]
    if not scored:
        return RetrievalMetrics()

    n = len(scored)
    return RetrievalMetrics(
        count=n,
        recall_at_1=sum(recall_at_k(m, 1) for m in scored) / n,
        recall_at_5=sum(recall_at_k(m, 5) for m in scored) / n,
        recall_at_10=sum(recall_at_k(m, 10) for m in scored) / n,
        mrr=sum(reciprocal_rank(m) for m in scored) / n,
        ndcg_at_10=sum(ndcg_at_k(m, 10) for m in scored) / n,
        partial_recall_at_10=sum(partial_recall_at_k(m, 10) for m in scored) / n,
    )

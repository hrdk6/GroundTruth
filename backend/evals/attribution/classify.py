"""Failure attribution: when an item fails, which stage is to blame?

This is the difference between "recall@5 went down" and "recall@5 went down
because the reranker is dropping table chunks". Every failed item gets exactly
one label, assigned in a fixed order so the classification is reproducible:

1. `false_answer` / `false_abstention` -- the abstention decision was wrong.
   Checked first, because an item that answered an unanswerable question has a
   generation problem regardless of what retrieval did.
2. `retrieval_miss` -- gold evidence appeared in *no* candidate set. Nothing
   downstream could have fixed this.
3. `ranking_miss` -- gold was retrieved but dropped before the final context.
   This is the one a reranker or fusion change can address.
4. `version_error` -- the right content, from the wrong release.
5. `generation_failure` -- gold was in the context and the answer was still
   wrong. Retrieval did its job.

The order matters because a single item can look like several of these at once,
and counting it twice would make the distribution lie about where to spend
effort next.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from app.retrieval.base import Candidate, RetrievalResult
from evals.dataset.schema import GoldenItem, GoldEvidence
from evals.metrics.retrieval import MatchResult, match_candidates

FailureType = Literal[
    "retrieval_miss",
    "ranking_miss",
    "generation_failure",
    "false_answer",
    "false_abstention",
    "version_error",
    "none",
]


@dataclass
class Attribution:
    failure: FailureType
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"failure": self.failure, "detail": self.detail}


def _gold_in_candidates(candidates: list[Candidate], gold: list[GoldEvidence]) -> bool:
    return bool(match_candidates(candidates, gold).first_rank)


def _gold_ignoring_version(candidates: list[Candidate], gold: list[GoldEvidence]) -> bool:
    """Whether the right *section* was retrieved, from any version."""
    for candidate in candidates:
        for evidence in gold:
            relaxed = GoldEvidence(
                source_path=evidence.source_path,
                version=candidate.version,  # deliberately ignore the version
                heading_path=evidence.heading_path,
                key_quote=evidence.key_quote,
            )
            if relaxed.matches(
                source_path=candidate.source_path,
                version=candidate.version,
                heading_path=candidate.heading_path,
                text=candidate.text,
            ):
                return True
    return False


def classify_failure(
    item: GoldenItem,
    retrieval: RetrievalResult,
    *,
    final_match: MatchResult,
    answered_correctly: bool,
    abstained: bool,
    answer_version: str | None = None,
    generation_ran: bool = True,
) -> Attribution:
    """Label one evaluated item. Returns `none` when nothing went wrong.

    `generation_ran` is False for retrieval-only evaluations. In that mode no
    answer was produced, so the abstention decision does not exist: scoring an
    unanswerable item as `false_answer` would invent a failure the system was
    never given the chance to make.
    """
    # --- abstention decisions ------------------------------------------
    if not item.answerable:
        if not generation_ran:
            return Attribution("none", "abstention not evaluated in retrieval-only mode")
        if abstained:
            return Attribution("none", "correctly abstained")
        return Attribution("false_answer", "answered a question the docs do not cover")

    if abstained:
        gold_present = bool(final_match.first_rank)
        if gold_present:
            return Attribution(
                "false_abstention", "abstained although gold evidence was in the context"
            )
        # Abstaining without the evidence is the correct call; the failure is
        # upstream in retrieval, so it is labelled there.

    if answered_correctly:
        return Attribution("none", "")

    # --- retrieval vs ranking -------------------------------------------
    all_candidates: list[Candidate] = list(retrieval.candidates)
    for stage_candidates in retrieval.stage_outputs.values():
        all_candidates.extend(stage_candidates)

    gold_anywhere = _gold_in_candidates(all_candidates, item.gold_evidence)
    gold_in_context = bool(final_match.first_rank)

    if not gold_anywhere:
        if _gold_ignoring_version(all_candidates, item.gold_evidence):
            return Attribution(
                "version_error",
                f"right section retrieved, but not in the required version "
                f"({item.version or 'unspecified'}; answered from {answer_version})",
            )
        return Attribution("retrieval_miss", "gold evidence was in no candidate set")

    if not gold_in_context:
        best = None
        for stage_name, stage_candidates in retrieval.stage_outputs.items():
            stage_match = match_candidates(stage_candidates, item.gold_evidence)
            if stage_match.best_rank is not None:
                best = f"{stage_name} rank {stage_match.best_rank}"
                break
        return Attribution(
            "ranking_miss", f"gold was retrieved ({best or 'in an earlier stage'}) but cut"
        )

    if item.version and answer_version and item.version != answer_version:
        return Attribution(
            "version_error", f"answered from {answer_version}, expected {item.version}"
        )

    if abstained:
        return Attribution("false_abstention", "gold was in context but the answer abstained")

    return Attribution("generation_failure", "gold was in context but the answer was wrong")


def summarize(attributions: list[Attribution]) -> dict[str, int]:
    """Distribution of failure types, `none` excluded."""
    counts = Counter(a.failure for a in attributions if a.failure != "none")
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

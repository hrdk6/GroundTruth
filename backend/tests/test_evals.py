"""Evaluation harness: matching, metrics, attribution, and judge agreement.

These tests are the guardrails on the numbers this project publishes. A silent
bug in `recall_at_k` or `classify_failure` would not crash anything -- it would
just produce plausible, wrong results and drive the wrong engineering decisions.
"""

from __future__ import annotations

import math

import pytest

from app.retrieval.base import Candidate, RetrievalResult
from evals.attribution.classify import classify_failure, summarize
from evals.dataset.schema import GoldenDataset, GoldenItem, GoldEvidence, normalize_quote
from evals.judge.agreement import HumanLabel, cohens_kappa, compute_agreement, interpret_kappa
from evals.metrics.retrieval import (
    aggregate_retrieval,
    match_candidates,
    ndcg_at_k,
    partial_recall_at_k,
    recall_at_k,
    reciprocal_rank,
)


def make_candidate(
    chunk_id: int,
    *,
    source_path: str = "concepts/pods.md",
    version: str = "1.28",
    heading_path: str = "Pods > Lifecycle",
    text: str = "The kubelet restarts the container.",
) -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        document_id=chunk_id,
        text=text,
        heading_path=heading_path,
        source_path=source_path,
        version=version,
    )


def gold(**kwargs: object) -> GoldEvidence:
    defaults = {
        "source_path": "concepts/pods.md",
        "version": "1.28",
        "heading_path": "Pods > Lifecycle",
        "key_quote": "kubelet restarts the container",
    }
    return GoldEvidence(**{**defaults, **kwargs})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Gold matching
# ---------------------------------------------------------------------------
def test_quote_match_ignores_whitespace_and_case() -> None:
    evidence = gold(key_quote="The   KUBELET restarts\nthe container")
    assert evidence.matches(
        source_path="concepts/pods.md",
        version="1.28",
        heading_path="anything",
        text="Note: the kubelet restarts the container automatically.",
    )


def test_gold_does_not_match_a_different_version() -> None:
    """The entire point of the project: 1.26 content is not a 1.28 answer."""
    assert not gold().matches(
        source_path="concepts/pods.md",
        version="1.26",
        heading_path="Pods > Lifecycle",
        text="The kubelet restarts the container.",
    )


def test_gold_does_not_match_a_different_page() -> None:
    assert not gold().matches(
        source_path="concepts/services.md",
        version="1.28",
        heading_path="Pods > Lifecycle",
        text="The kubelet restarts the container.",
    )


def test_gold_survives_rechunking() -> None:
    """The quote is what matters, so a different heading still matches."""
    assert gold().matches(
        source_path="concepts/pods.md",
        version="1.28",
        heading_path="Completely > Different > Path",
        text="... the kubelet restarts the container ...",
    )


def test_heading_is_used_when_there_is_no_quote() -> None:
    evidence = gold(key_quote="")
    assert evidence.matches(
        source_path="concepts/pods.md",
        version="1.28",
        heading_path="Pods > Lifecycle",
        text="anything at all",
    )
    assert not evidence.matches(
        source_path="concepts/pods.md",
        version="1.28",
        heading_path="Pods > Something Else",
        text="anything at all",
    )


def test_normalize_quote_collapses_whitespace() -> None:
    assert normalize_quote("  A  B\n\tC ") == "a b c"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def test_match_records_first_rank_of_each_gold() -> None:
    candidates = [
        make_candidate(1, text="unrelated"),
        make_candidate(2, text="the kubelet restarts the container"),
        make_candidate(3, text="the kubelet restarts the container again"),
    ]
    match = match_candidates(candidates, [gold()])
    assert match.first_rank == {0: 2}
    assert match.relevant_ranks == [2, 3]
    assert match.best_rank == 2


def test_recall_requires_every_piece_of_gold() -> None:
    """A multi-hop question with half its evidence is not answerable."""
    golds = [
        gold(source_path="a.md", key_quote="alpha"),
        gold(source_path="b.md", key_quote="beta"),
    ]
    candidates = [make_candidate(1, source_path="a.md", text="alpha here")]

    match = match_candidates(candidates, golds)
    assert recall_at_k(match, 5) == 0.0, "partial evidence must not count as recall"
    assert partial_recall_at_k(match, 5) == 0.5, "but partial recall reports the half"


def test_recall_at_k_respects_the_cutoff() -> None:
    candidates = [make_candidate(i, text="nothing") for i in range(1, 8)]
    candidates.append(make_candidate(8, text="the kubelet restarts the container"))
    match = match_candidates(candidates, [gold()])

    assert recall_at_k(match, 5) == 0.0
    assert recall_at_k(match, 10) == 1.0


def test_reciprocal_rank_values() -> None:
    candidates = [
        make_candidate(1, text="nope"),
        make_candidate(2, text="the kubelet restarts the container"),
    ]
    match = match_candidates(candidates, [gold()])
    assert reciprocal_rank(match) == pytest.approx(0.5)


def test_reciprocal_rank_is_zero_when_nothing_matched() -> None:
    match = match_candidates([make_candidate(1, text="nope")], [gold()])
    assert reciprocal_rank(match) == 0.0


def test_ndcg_is_one_for_a_perfect_ranking() -> None:
    candidates = [make_candidate(1, text="the kubelet restarts the container")]
    match = match_candidates(candidates, [gold()])
    assert ndcg_at_k(match, 10) == pytest.approx(1.0)


def test_ndcg_discounts_lower_ranks() -> None:
    top = match_candidates([make_candidate(1, text="the kubelet restarts the container")], [gold()])
    third = match_candidates(
        [
            make_candidate(1, text="no"),
            make_candidate(2, text="no"),
            make_candidate(3, text="the kubelet restarts the container"),
        ],
        [gold()],
    )
    assert ndcg_at_k(third, 10) < ndcg_at_k(top, 10)
    assert ndcg_at_k(third, 10) == pytest.approx(1 / math.log2(4))


def test_aggregate_excludes_items_with_no_gold() -> None:
    """Unanswerable items have no gold and must not drag retrieval metrics down."""
    with_gold = match_candidates(
        [make_candidate(1, text="the kubelet restarts the container")], [gold()]
    )
    without_gold = match_candidates([make_candidate(2)], [])

    metrics = aggregate_retrieval([with_gold, without_gold])
    assert metrics.count == 1
    assert metrics.recall_at_5 == 1.0


def test_aggregate_of_nothing_is_zero_not_a_crash() -> None:
    assert aggregate_retrieval([]).to_dict()["recall@5"] == 0.0


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------
def make_item(**kwargs: object) -> GoldenItem:
    defaults: dict[str, object] = {
        "id": "q1",
        "question": "What restarts a failed container?",
        "category": "factual",
        "reference_answer": "The kubelet.",
        "gold_evidence": [gold()],
        "version": "1.28",
        "answerable": True,
        "curated": True,
    }
    return GoldenItem(**{**defaults, **kwargs})  # type: ignore[arg-type]


def make_retrieval(final: list[Candidate], **stages: list[Candidate]) -> RetrievalResult:
    return RetrievalResult(candidates=final, query="q", stage_outputs=dict(stages))


def test_retrieval_miss_when_gold_was_never_retrieved() -> None:
    retrieval = make_retrieval([make_candidate(1, source_path="other.md", text="nope")])
    match = match_candidates(retrieval.candidates, [gold()])

    result = classify_failure(
        make_item(), retrieval, final_match=match, answered_correctly=False, abstained=False
    )
    assert result.failure == "retrieval_miss"


def test_ranking_miss_when_gold_was_retrieved_then_dropped() -> None:
    """The distinction a reranker change is evaluated against."""
    hit = make_candidate(9, text="the kubelet restarts the container")
    retrieval = make_retrieval(
        [make_candidate(1, source_path="other.md", text="nope")],
        dense=[make_candidate(1, source_path="other.md", text="nope"), hit],
    )
    match = match_candidates(retrieval.candidates, [gold()])

    result = classify_failure(
        make_item(), retrieval, final_match=match, answered_correctly=False, abstained=False
    )
    assert result.failure == "ranking_miss"
    assert "dense rank 2" in result.detail


def test_generation_failure_when_gold_was_in_context() -> None:
    hit = make_candidate(9, text="the kubelet restarts the container")
    retrieval = make_retrieval([hit])
    match = match_candidates(retrieval.candidates, [gold()])

    result = classify_failure(
        make_item(), retrieval, final_match=match, answered_correctly=False, abstained=False
    )
    assert result.failure == "generation_failure"


def test_false_answer_on_an_unanswerable_question() -> None:
    item = make_item(answerable=False, gold_evidence=[])
    retrieval = make_retrieval([make_candidate(1)])
    match = match_candidates(retrieval.candidates, [])

    result = classify_failure(
        item, retrieval, final_match=match, answered_correctly=False, abstained=False
    )
    assert result.failure == "false_answer"


def test_correct_abstention_is_not_a_failure() -> None:
    item = make_item(answerable=False, gold_evidence=[])
    retrieval = make_retrieval([make_candidate(1)])
    match = match_candidates(retrieval.candidates, [])

    result = classify_failure(
        item, retrieval, final_match=match, answered_correctly=False, abstained=True
    )
    assert result.failure == "none"


def test_false_abstention_when_the_evidence_was_there() -> None:
    hit = make_candidate(9, text="the kubelet restarts the container")
    retrieval = make_retrieval([hit])
    match = match_candidates(retrieval.candidates, [gold()])

    result = classify_failure(
        make_item(), retrieval, final_match=match, answered_correctly=False, abstained=True
    )
    assert result.failure == "false_abstention"


def test_version_error_when_the_right_section_came_from_the_wrong_release() -> None:
    wrong_version = make_candidate(5, version="1.26", text="the kubelet restarts the container")
    retrieval = make_retrieval([wrong_version])
    match = match_candidates(retrieval.candidates, [gold()])

    result = classify_failure(
        make_item(),
        retrieval,
        final_match=match,
        answered_correctly=False,
        abstained=False,
        answer_version="1.26",
    )
    assert result.failure == "version_error"


def test_success_is_labelled_none() -> None:
    hit = make_candidate(9, text="the kubelet restarts the container")
    retrieval = make_retrieval([hit])
    match = match_candidates(retrieval.candidates, [gold()])

    result = classify_failure(
        make_item(), retrieval, final_match=match, answered_correctly=True, abstained=False
    )
    assert result.failure == "none"


def test_summarize_counts_and_excludes_successes() -> None:
    from evals.attribution.classify import Attribution

    counts = summarize(
        [
            Attribution("retrieval_miss"),
            Attribution("retrieval_miss"),
            Attribution("ranking_miss"),
            Attribution("none"),
        ]
    )
    assert counts == {"retrieval_miss": 2, "ranking_miss": 1}


# ---------------------------------------------------------------------------
# Judge agreement
# ---------------------------------------------------------------------------
def test_kappa_is_one_for_perfect_agreement() -> None:
    assert cohens_kappa(both_pass=10, both_fail=10, lenient=0, strict=0) == pytest.approx(1.0)


def test_kappa_is_zero_for_chance_agreement() -> None:
    """An all-pass judge on 90%-pass data has high accuracy and no skill."""
    report = compute_agreement(
        judge_results={f"q{i}": True for i in range(10)},
        human_labels=[HumanLabel(f"q{i}", correct=i < 9) for i in range(10)],
    )
    assert report.accuracy == pytest.approx(0.9)
    assert report.kappa == pytest.approx(0.0, abs=1e-9)
    assert report.interpretation in ("slight", "poor (worse than chance)")


def test_agreement_counts_lenient_and_strict_separately() -> None:
    report = compute_agreement(
        judge_results={"a": True, "b": False, "c": True, "d": False},
        human_labels=[
            HumanLabel("a", correct=True),
            HumanLabel("b", correct=False),
            HumanLabel("c", correct=False),  # judge too lenient
            HumanLabel("d", correct=True),  # judge too strict
        ],
    )
    assert report.both_pass == 1
    assert report.both_fail == 1
    assert report.judge_pass_human_fail == 1
    assert report.judge_fail_human_pass == 1
    assert report.accuracy == pytest.approx(0.5)


def test_labels_without_a_judge_result_are_reported_not_silently_dropped() -> None:
    report = compute_agreement(
        judge_results={"a": True},
        human_labels=[HumanLabel("a", correct=True), HumanLabel("missing", correct=True)],
    )
    assert report.n == 1
    assert report.missing_labels == ["missing"]


@pytest.mark.parametrize(
    ("kappa", "expected"),
    [
        (-0.1, "poor (worse than chance)"),
        (0.1, "slight"),
        (0.5, "moderate"),
        (0.9, "almost perfect"),
    ],
)
def test_kappa_interpretation(kappa: float, expected: str) -> None:
    assert interpret_kappa(kappa) == expected


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------
def test_dataset_filters_to_curated_items_only() -> None:
    dataset = GoldenDataset([make_item(id="a", curated=True), make_item(id="b", curated=False)])
    assert [i.id for i in dataset.curated_only().items] == ["a"]


def test_dataset_splits() -> None:
    dataset = GoldenDataset([make_item(id="a", split="dev"), make_item(id="b", split="test")])
    assert [i.id for i in dataset.split("dev").items] == ["a"]
    assert [i.id for i in dataset.split("test").items] == ["b"]


def test_item_round_trips_through_dict() -> None:
    item = make_item()
    restored = GoldenItem.from_dict(item.to_dict())
    assert restored.question == item.question
    assert restored.gold_evidence[0].key_quote == item.gold_evidence[0].key_quote

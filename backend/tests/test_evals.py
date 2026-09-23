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


# ---------------------------------------------------------------------------
# Regression gate
# ---------------------------------------------------------------------------
def make_record(metrics: dict, by_category: dict | None = None) -> dict:
    return {
        "config": {"name": "test"},
        "split": "dev",
        "dataset_size": 10,
        "metrics": metrics,
        "metrics_by_category": by_category or {},
        "attribution": {},
    }


def test_gate_passes_when_metrics_are_above_their_floors() -> None:
    from evals.gate import build_checks

    checks = build_checks(
        make_record({"recall@5": 0.80, "mrr": 0.70}),
        {"metrics": {"recall@5": 0.75, "mrr": 0.65}},
    )
    assert [c.status for c in checks] == ["pass", "pass"]
    assert not any(c.failed for c in checks)


def test_gate_fails_on_a_regression() -> None:
    from evals.gate import build_checks

    checks = build_checks(make_record({"recall@5": 0.60}), {"metrics": {"recall@5": 0.75}})
    assert checks[0].status == "FAIL"
    assert checks[0].failed


def test_gate_fails_when_a_tracked_metric_is_missing() -> None:
    """A removed metric must not slip through as 'nothing to check'."""
    from evals.gate import build_checks

    checks = build_checks(make_record({}), {"metrics": {"recall@5": 0.75}})
    assert checks[0].status == "missing"
    assert checks[0].failed


def test_unset_floor_reports_but_never_fails() -> None:
    """Before a baseline exists there is nothing to compare against."""
    from evals.gate import build_checks

    checks = build_checks(make_record({"recall@5": 0.1}), {"metrics": {"recall@5": None}})
    assert checks[0].status == "unset"
    assert not checks[0].failed


def test_gate_checks_per_category_floors() -> None:
    from evals.gate import build_checks

    checks = build_checks(
        make_record({"recall@5": 0.9}, {"exact_term": {"recall@5": 0.40}}),
        {"by_category": {"exact_term": {"recall@5": 0.60}}},
    )
    assert checks[0].name == "exact_term.recall@5"
    assert checks[0].failed, "an overall win must not hide a per-category regression"


def test_shipped_thresholds_file_parses() -> None:
    from evals.gate import load_thresholds

    thresholds = load_thresholds()
    assert "metrics" in thresholds
    assert "recall@5" in thresholds["metrics"]


# ---------------------------------------------------------------------------
# Fixture golden set (the dataset CI gates on)
# ---------------------------------------------------------------------------
def test_fixture_golden_set_is_loadable_and_curated() -> None:
    from evals.dataset.schema import load_dataset

    dataset = load_dataset("fixture_golden.jsonl")
    assert len(dataset) >= 8
    assert all(item.curated for item in dataset.items)


def test_fixture_quotes_exist_in_the_fixture_corpus() -> None:
    """Gold that cannot be found in the source is not gold."""
    from pathlib import Path

    from evals.dataset.schema import load_dataset, normalize_quote

    corpus = Path(__file__).parent / "fixtures" / "corpus"
    for item in load_dataset("fixture_golden.jsonl").items:
        for evidence in item.gold_evidence:
            path = corpus / evidence.version / evidence.source_path
            assert path.exists(), f"{item.id}: missing {path}"
            text = path.read_text(encoding="utf-8")
            assert normalize_quote(evidence.key_quote) in normalize_quote(text), (
                f"{item.id}: quote not found in {evidence.source_path}"
            )


def test_fixture_set_has_both_answerable_and_unanswerable_items() -> None:
    """Abstention metrics need both classes present."""
    from evals.dataset.schema import load_dataset

    items = load_dataset("fixture_golden.jsonl").items
    assert any(i.answerable for i in items)
    assert any(not i.answerable for i in items)


# ---------------------------------------------------------------------------
# Citation parsing
#
# Both of these were found by running a real evaluation, and both caused the
# same visible failure: a correctly-cited answer scored `support_fraction 0.0`
# and was replaced by an abstention. The verifier was punishing the model for
# doing the right thing.
# ---------------------------------------------------------------------------
def test_a_trailing_citation_stays_with_its_sentence() -> None:
    """`...253 characters. [3]` must not split into a sentence plus an orphan."""
    from app.generation.verify import split_sentences

    pairs = split_sentences("A name can contain no more than 253 characters. [3]")
    assert len(pairs) == 1, "the citation must not become its own sentence"
    sentence, citations = pairs[0]
    assert citations == [3]
    assert "253 characters" in sentence


def test_full_width_citation_brackets_are_recognized() -> None:
    """Models emit the CJK form often enough that ASCII-only matching lies."""
    from app.generation.verify import normalize_citations, split_sentences

    assert normalize_citations("levels are baseline\u30101\u3011.") == "levels are baseline[1]."

    pairs = split_sentences("The levels are privileged, baseline, or restricted\u30101\u3011.")
    assert pairs[0][1] == [1], "a full-width citation must count as a citation"


def test_ordinary_multi_sentence_answers_still_split() -> None:
    """The merge must not collapse genuinely separate sentences."""
    from app.generation.verify import split_sentences

    pairs = split_sentences("First claim [1]. Second claim [2].")
    assert [c for _, c in pairs] == [[1], [2]]


def test_extract_citations_handles_full_width_brackets() -> None:
    """Otherwise the UI silently shows an answer with no sources."""
    from app.generation.answer import extract_citations

    candidates = [make_candidate(7, source_path="a.md", text="x")]
    citations = extract_citations("The answer\u30101\u3011.", candidates)
    assert [c.marker for c in citations] == [1]
    assert citations[0].chunk_id == 7


def test_an_uncited_factual_sentence_is_still_unsupported() -> None:
    """The fix must not weaken the rule it was masking."""
    from app.generation.verify import is_factual, split_sentences

    pairs = split_sentences("The kubelet restarts the container automatically.")
    sentence, citations = pairs[0]
    assert citations == []
    assert is_factual(sentence)


# ---------------------------------------------------------------------------
# Ranking depth: ranked list vs. the k_final context
#
# Every early experiment computed recall@10 over the five-chunk context, so it
# equalled recall@5 by construction. These pin the two depths apart.
# ---------------------------------------------------------------------------
def _ranked_with_gold_at(rank: int, depth: int = 20) -> list[Candidate]:
    ranked = [make_candidate(i, source_path="other.md", text="nope") for i in range(1, depth + 1)]
    ranked[rank - 1] = make_candidate(rank, text="the kubelet restarts the container")
    return ranked


def test_recall_at_10_sees_past_the_context_cut() -> None:
    match = match_candidates(_ranked_with_gold_at(7), [gold()])
    metrics = aggregate_retrieval([match], k_final=5)
    assert metrics.recall_at_5 == 0.0
    assert metrics.recall_at_10 == 1.0, "rank 7 is inside the top 10"
    assert metrics.context_recall == 0.0, "but outside the five chunks the model saw"


def test_context_recall_is_recall_at_k_final() -> None:
    match = match_candidates(_ranked_with_gold_at(6), [gold()])
    assert aggregate_retrieval([match], k_final=6).context_recall == 1.0
    assert aggregate_retrieval([match], k_final=5).context_recall == 0.0


def test_mrr_has_an_explicit_cutoff() -> None:
    """A hit at rank 30 of a 40-deep fused list must not outscore a config that fetched 20."""
    assert reciprocal_rank(match_candidates(_ranked_with_gold_at(10), [gold()])) == 0.1
    assert reciprocal_rank(match_candidates(_ranked_with_gold_at(11), [gold()])) == 0.0


def test_retrieval_result_ranked_defaults_to_the_context() -> None:
    final = [make_candidate(1)]
    assert RetrievalResult(candidates=final, query="q").ranked == final


# ---------------------------------------------------------------------------
# Gold integrity
# ---------------------------------------------------------------------------
def test_recall_ceiling_counts_items_not_evidence() -> None:
    """A multi-hop item with one unmatchable page cannot be recalled at all."""
    from evals.integrity import IntegrityReport

    report = IntegrityReport(chunker_name="x", items_scored=4, items_matchable=3)
    assert report.recall_ceiling == 0.75


def test_gate_fails_when_gold_is_unmatchable() -> None:
    """The check that would have caught the decode bug on day one."""
    from evals.gate import build_checks

    record = make_record({"recall@5": 0.9})
    record["integrity"] = {"recall_ceiling": 0.3077}
    checks = build_checks(record, {"integrity": {"recall_ceiling": 1.0}})
    assert checks[0].name == "integrity.recall_ceiling"
    assert checks[0].failed


def test_gate_fails_when_a_record_predates_the_integrity_audit() -> None:
    from evals.gate import build_checks

    checks = build_checks(make_record({"recall@5": 0.9}), {"integrity": {"recall_ceiling": 1.0}})
    assert checks[0].status == "missing"


# ---------------------------------------------------------------------------
# Judge agreement bookkeeping
# ---------------------------------------------------------------------------
def test_labels_for_a_different_answer_are_stale_not_counted() -> None:
    """Re-running an experiment gives the same item id a new answer."""
    labels = [
        HumanLabel("a", correct=True, answer_sha="old"),
        HumanLabel("b", correct=False, answer_sha="same"),
    ]
    report = compute_agreement({"a": True, "b": False}, labels, {"a": "new", "b": "same"})
    assert report.n == 1
    assert report.stale_labels == ["a"]


def test_answer_digest_ignores_whitespace_only_changes() -> None:
    from evals.judge.agreement import answer_digest

    assert answer_digest("A  pod\nruns.") == answer_digest("A pod runs.")
    assert answer_digest("A pod runs.") != answer_digest("A pod stops.")


# ---------------------------------------------------------------------------
# Golden set building
# ---------------------------------------------------------------------------
def test_generated_drafts_never_replace_curated_items() -> None:
    """Regression: `build --yes` wrote its drafts over the curated golden set."""
    from evals.dataset.build import merge_drafts

    curated = make_item(id="kept", question="What restarts a container?", curated=True)
    duplicate_question = make_item(id="new-id", question="what restarts a container?  ")
    fresh = make_item(id="fresh", question="What is a DaemonSet?", curated=False)

    added = merge_drafts([curated], [duplicate_question, fresh])
    assert [item.id for item in added] == ["fresh"]
    assert curated.curated, "existing items are never modified"


# ---------------------------------------------------------------------------
# Answer segments and concurrent verification
# ---------------------------------------------------------------------------
def test_segments_attach_verdicts_to_the_sentences_the_verifier_judged() -> None:
    from app.generation.verify import SentenceVerification, VerificationReport, segment_answer

    answer = "A name can contain no more than 253 characters. [3] Labels are shorter [1]."
    report = VerificationReport(
        sentences=[
            SentenceVerification(
                "A name can contain no more than 253 characters. [3]", [3], "supported"
            ),
            SentenceVerification("Labels are shorter [1].", [1], "partially"),
        ]
    )
    segments = segment_answer(answer, report)
    assert [s["verdict"] for s in segments] == ["supported", "partially"]
    assert segments[0]["citations"] == [3], "the trailing marker stays with its sentence"


def test_segments_without_verification_carry_no_verdict() -> None:
    from app.generation.verify import segment_answer

    segments = segment_answer("First claim [1]. Second claim [2].", None)
    assert [s["verdict"] for s in segments] == [None, None]
    assert [s["citations"] for s in segments] == [[1], [2]]


class _VerdictByClaim:
    """Supports claims mentioning 'alpha'; slow enough that threads interleave."""

    name = "fake"

    def invoke(self, **kwargs: object):  # type: ignore[no-untyped-def]
        import time

        from app.core.llm import ProviderResult

        prompt = str(kwargs["prompt"])
        claim = prompt.split("Claim:", 1)[1]
        time.sleep(0.01 if "alpha" in claim else 0.03)
        verdict = "supported" if "alpha" in claim else "unsupported"
        return ProviderResult(text=f'{{"verdict": "{verdict}"}}', input_tokens=10, output_tokens=5)


@pytest.mark.parametrize("workers", [1, 4])
def test_concurrent_verification_preserves_sentence_order(llm_client, workers: int) -> None:  # type: ignore[no-untyped-def]
    from app.generation.verify import verify_answer

    llm_client._client = _VerdictByClaim()
    answer = (
        "The beta widget is configured per node [1]. "
        "The alpha widget is configured per pod [1]. "
        "The gamma widget has no default value at all [1]. "
        "The alpha controller reconciles every ten seconds [1]."
    )
    candidates = [make_candidate(1)]
    report = verify_answer(llm_client, answer, candidates, max_concurrency=workers)

    assert [s.verdict for s in report.sentences] == [
        "unsupported",
        "supported",
        "unsupported",
        "supported",
    ]
    assert report.support_fraction == 0.5


def test_a_mid_answer_trailing_citation_stays_with_its_sentence() -> None:
    """Regression: the earlier fix only handled a citation after the *last* sentence.

    Mid-answer, `...characters. [3] Labels...` split before the `[`, so `[3]`
    was glued to the front of the next sentence: the first claim read as
    uncited, and the second was checked against the wrong excerpt.
    """
    from app.generation.verify import split_sentences

    pairs = split_sentences("Names hold 253 characters. [3] Labels hold 63 characters [1].")
    assert pairs == [
        ("Names hold 253 characters. [3]", [3]),
        ("Labels hold 63 characters [1].", [1]),
    ]


def test_leading_citations_with_punctuation_do_not_leave_an_empty_sentence() -> None:
    from app.generation.verify import split_sentences

    pairs = split_sentences("Names hold 253 characters. [3][4]. Labels are short [1].")
    assert [c for _, c in pairs] == [[3, 4], [1]]


def test_multi_hop_with_one_page_missing_is_a_retrieval_miss() -> None:
    """Regression: attribution tested for ANY gold, recall for ALL of it.

    A two-page item with one page retrieved scored recall 0 while attribution
    called it a success, so the distribution under-reported the category that
    scored 0.000 in every run.
    """
    page_a = gold(source_path="a.md", key_quote="alpha fact")
    page_b = gold(source_path="b.md", key_quote="beta fact")
    item = make_item(category="multi_hop", gold_evidence=[page_a, page_b])
    found = make_candidate(1, source_path="a.md", text="the alpha fact is here")
    retrieval = make_retrieval([found], dense=[found])
    match = match_candidates(retrieval.candidates, item.gold_evidence)

    result = classify_failure(
        item,
        retrieval,
        final_match=match,
        answered_correctly=False,
        abstained=False,
        generation_ran=False,
    )
    assert result.failure == "retrieval_miss"
    assert "1 of 2" in result.detail


def test_multi_hop_with_one_page_cut_is_a_ranking_miss() -> None:
    page_a = gold(source_path="a.md", key_quote="alpha fact")
    page_b = gold(source_path="b.md", key_quote="beta fact")
    item = make_item(category="multi_hop", gold_evidence=[page_a, page_b])
    a = make_candidate(1, source_path="a.md", text="the alpha fact is here")
    b = make_candidate(2, source_path="b.md", text="the beta fact is here")
    retrieval = make_retrieval([a], dense=[a, make_candidate(3, text="x"), b])
    match = match_candidates(retrieval.candidates, item.gold_evidence)

    result = classify_failure(
        item, retrieval, final_match=match, answered_correctly=False, abstained=False
    )
    assert result.failure == "ranking_miss"
    assert "dense rank 3" in result.detail, "the rank of the piece that was cut"


def test_retrieval_only_success_is_never_a_generation_failure() -> None:
    hit = make_candidate(9, text="the kubelet restarts the container")
    retrieval = make_retrieval([hit])
    match = match_candidates(retrieval.candidates, [gold()])

    result = classify_failure(
        make_item(),
        retrieval,
        final_match=match,
        answered_correctly=False,
        abstained=False,
        generation_ran=False,
    )
    assert result.failure == "none"


# ---------------------------------------------------------------------------
# Reproducibility bookkeeping
# ---------------------------------------------------------------------------
def test_experiment_output_does_not_make_the_tree_dirty(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression: each run's own output file marked the *next* run dirty."""
    import shutil
    import subprocess

    from evals.runner import git_is_dirty

    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "code.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "code.py")
    git("commit", "-q", "-m", "init")

    (tmp_path / "experiments").mkdir()
    (tmp_path / "experiments" / "run.json").write_text("{}", encoding="utf-8")
    assert not git_is_dirty(tmp_path), "an experiment record is output, not a change"

    (tmp_path / "code.py").write_text("x = 2\n", encoding="utf-8")
    assert git_is_dirty(tmp_path), "a modified source file is"


# ---------------------------------------------------------------------------
# Conflict notes
# ---------------------------------------------------------------------------
def test_conflicts_are_checked_only_for_the_sections_the_answer_cites(
    llm_client, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """Regression: every context chunk was checked, so an answer got version
    notes about excerpts it never used."""
    from app.core.pipeline import load_config
    from app.generation import answer as answer_module
    from app.retrieval.versioning import VersionDecision
    from tests.conftest import FakeProvider

    checked: list[int] = []

    def fake_detect(session, candidates, **kwargs):  # type: ignore[no-untyped-def]
        checked.extend(c.chunk_id for c in candidates)
        return []

    monkeypatch.setattr(answer_module, "detect_conflicts", fake_detect)
    monkeypatch.setattr(answer_module, "indexed_versions", lambda session, name: ["1.26", "1.30"])
    llm_client._client = FakeProvider(text="The kubelet restarts the container [2].")

    config = load_config("hybrid")
    config = config.model_copy(
        update={"versioning": config.versioning.model_copy(update={"conflict_detection": True})}
    )
    service = answer_module.AnswerService(config, llm_client=llm_client)
    retrieval = RetrievalResult(
        candidates=[make_candidate(1), make_candidate(2), make_candidate(3)], query="q"
    )
    decision = VersionDecision("1.30", explicit=False)

    service._generate_and_verify(None, "q", retrieval, decision, llm=llm_client)  # type: ignore[arg-type]
    assert checked == [2]


def test_the_judge_never_sees_citation_markers(llm_client) -> None:  # type: ignore[no-untyped-def]
    """Regression: `.spec.revisionHistoryLimit[1][2]` was docked for "incorrect indices"."""
    from evals.judge.judge import judge_answer, strip_citations
    from tests.conftest import FakeProvider

    assert strip_citations(".spec.revisionHistoryLimit[1][2]") == ".spec.revisionHistoryLimit"
    assert strip_citations("It is beta 【2】.") == "It is beta."

    fake = FakeProvider(text='{"score": 5, "pass": true, "reason": "ok"}')
    llm_client._client = fake
    judge_answer(
        llm_client,
        question="Which field?",
        reference_answer="`.spec.revisionHistoryLimit`",
        answer=".spec.revisionHistoryLimit[1][2]",
    )
    prompt = str(fake.calls[0]["prompt"])
    assert "[1]" not in prompt and ".spec.revisionHistoryLimit" in prompt

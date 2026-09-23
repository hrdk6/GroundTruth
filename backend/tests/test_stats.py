"""Bootstrap intervals, the paired sign-flip test, and record-level helpers.

The exact p-values below are checkable by hand, which is the point of testing
a statistics routine against cases small enough to enumerate.
"""

from __future__ import annotations

import pytest

from evals.compare import (
    comparability_problems,
    compare_records,
    confidence_block,
    item_values,
)
from evals.metrics.stats import bootstrap_ci, paired_comparison, sign_flip_p_value


# --- bootstrap --------------------------------------------------------------
def test_interval_contains_the_mean_and_is_ordered() -> None:
    values = [1.0] * 12 + [0.0] * 7
    interval = bootstrap_ci(values)
    assert interval.low <= interval.mean <= interval.high
    assert interval.mean == pytest.approx(12 / 19)
    assert interval.n == 19


def test_small_sets_get_wide_intervals() -> None:
    """The whole reason this module exists: 19 items cannot pin a mean down."""
    interval = bootstrap_ci([1.0] * 15 + [0.0] * 4)
    assert interval.high - interval.low > 0.25


def test_more_items_narrow_the_interval() -> None:
    small = bootstrap_ci([1.0, 0.0] * 10)
    large = bootstrap_ci([1.0, 0.0] * 200)
    assert (large.high - large.low) < (small.high - small.low)


def test_constant_scores_give_a_point_not_a_fake_interval() -> None:
    interval = bootstrap_ci([1.0] * 8)
    assert interval.low == interval.high == interval.mean == 1.0


def test_empty_input_is_zero_not_a_crash() -> None:
    assert bootstrap_ci([]).n == 0


def test_bootstrap_is_reproducible() -> None:
    values = [0.2, 0.9, 0.4, 1.0, 0.0, 0.7]
    assert bootstrap_ci(values) == bootstrap_ci(values)


# --- sign-flip test ---------------------------------------------------------
def test_sign_flip_p_value_is_exact_for_small_n() -> None:
    # Five items, all improved by 1. Of the 2^5 sign assignments only
    # all-positive and all-negative reach |sum| = 5: p = 2/32.
    assert sign_flip_p_value([1.0] * 5) == pytest.approx(2 / 32)


def test_ties_carry_no_sign_and_are_dropped() -> None:
    assert sign_flip_p_value([1.0] * 5 + [0.0] * 20) == pytest.approx(2 / 32)


def test_no_differences_means_no_evidence() -> None:
    assert sign_flip_p_value([0.0, 0.0, 0.0]) == 1.0


def test_balanced_wins_and_losses_are_not_significant() -> None:
    assert sign_flip_p_value([1.0, -1.0, 1.0, -1.0]) == 1.0


def test_large_n_uses_monte_carlo_and_stays_in_range() -> None:
    p = sign_flip_p_value([1.0] * 30 + [-1.0] * 2)
    assert 0 < p < 0.001


# --- paired comparison ------------------------------------------------------
def test_one_item_swing_on_nineteen_is_within_noise() -> None:
    """The hybrid_rerank case: one item better on 19 is not a finding."""
    a = [1.0] * 14 + [0.0] * 5
    b = [1.0] * 15 + [0.0] * 4
    comparison = paired_comparison("recall@5", a, b)
    assert comparison.delta == pytest.approx(1 / 19)
    assert comparison.wins == 1 and comparison.losses == 0
    assert not comparison.distinguishable
    assert comparison.p_value == pytest.approx(1.0)


def test_a_consistent_large_improvement_is_distinguishable() -> None:
    a = [0.0] * 15 + [1.0] * 4
    b = [1.0] * 19
    comparison = paired_comparison("recall@5", a, b)
    assert comparison.distinguishable
    assert comparison.p_value < 0.001


def test_unequal_lengths_are_rejected() -> None:
    with pytest.raises(ValueError):
        paired_comparison("m", [1.0], [1.0, 0.0])


# --- record helpers ---------------------------------------------------------
def _record(scores: dict[str, float], *, split: str = "dev", judged: bool = False) -> dict:
    items = []
    for item_id, score in scores.items():
        item = {"item_id": item_id, "gold_total": 1, "recall@5": score, "mrr": score}
        if judged:
            item["judge_passed"] = score >= 1.0
        items.append(item)
    items.append({"item_id": "unanswerable", "gold_total": 0, "recall@5": 0.0, "mrr": 0.0})
    return {"dataset_version": "golden_v1", "split": split, "items": items, "_id": split}


def test_retrieval_values_exclude_items_without_gold() -> None:
    values = item_values(_record({"a": 1.0, "b": 0.0}), "recall@5")
    assert values == {"a": 1.0, "b": 0.0}


def test_confidence_block_covers_the_metrics_present() -> None:
    block = confidence_block(_record({"a": 1.0, "b": 0.0, "c": 1.0}, judged=True))
    assert set(block) >= {"recall@5", "mrr", "answer_correctness"}
    assert "faithfulness" not in block


def test_comparison_pairs_by_item_id_not_position() -> None:
    a = _record({"x": 0.0, "y": 1.0})
    b = _record({"y": 1.0, "x": 1.0})  # same items, different order
    rows = {row.metric: row for row in compare_records(a, b)}
    assert rows["recall@5"].wins == 1 and rows["recall@5"].losses == 0


def test_runs_over_different_splits_are_not_comparable() -> None:
    problems = comparability_problems(_record({"a": 1.0}), _record({"a": 1.0}, split="test"))
    assert problems and "split" in problems[0]

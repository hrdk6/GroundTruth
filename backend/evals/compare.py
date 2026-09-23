"""Compare two experiments item by item: `python -m evals.compare A B`.

    python -m evals.compare 20260923T..._hybrid 20260923T..._hybrid_rerank

Prints, for every metric both runs report, the paired difference (B - A) with
a 95% bootstrap interval, an exact sign-flip p-value, and how many items each
side won. A difference whose interval spans zero is labelled *within noise*,
which on a 19-item split is most differences under ~0.15 -- and saying so is
the point.

Refuses to compare runs over different datasets or splits unless `--force` is
given: the items would not pair, and a mean over different questions is not a
comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.core.settings import REPO_ROOT
from evals.metrics.stats import Interval, PairedComparison, bootstrap_ci, paired_comparison

EXPERIMENTS_DIR = REPO_ROOT / "experiments"

# Per-item fields that average to the run's headline metrics.
RETRIEVAL_METRICS = ("recall@1", "recall@5", "recall@10", "mrr", "ndcg@10")
GENERATION_METRICS = ("answer_correctness", "faithfulness", "citation_precision")
METRICS = RETRIEVAL_METRICS + GENERATION_METRICS


def item_values(record: dict[str, Any], metric: str) -> dict[str, float]:
    """`item_id -> score` for the items that count toward `metric`.

    Mirrors how the runner aggregates: retrieval metrics over items with gold
    evidence, correctness over judged items, faithfulness and citation
    precision over answers that were verified.
    """
    values: dict[str, float] = {}
    for item in record.get("items", []):
        item_id = item["item_id"]
        if metric in RETRIEVAL_METRICS:
            if item.get("gold_total") and metric in item:
                values[item_id] = float(item[metric])
        elif metric == "answer_correctness":
            if item.get("judge_passed") is not None:
                values[item_id] = 1.0 if item["judge_passed"] else 0.0
        elif metric == "faithfulness":
            if item.get("support_fraction") is not None:
                values[item_id] = float(item["support_fraction"])
        elif metric == "citation_precision" and item.get("citation_precision") is not None:
            values[item_id] = float(item["citation_precision"])
    return values


def confidence_block(record: dict[str, Any]) -> dict[str, dict[str, float | int]]:
    """95% bootstrap interval for every metric the record has per-item data for."""
    out: dict[str, dict[str, float | int]] = {}
    for metric in METRICS:
        values = list(item_values(record, metric).values())
        if values:
            interval: Interval = bootstrap_ci(values)
            out[metric] = interval.to_dict()
    return out


def compare_records(a: dict[str, Any], b: dict[str, Any]) -> list[PairedComparison]:
    """Paired comparison on every metric, over the items both runs scored."""
    comparisons: list[PairedComparison] = []
    for metric in METRICS:
        values_a = item_values(a, metric)
        values_b = item_values(b, metric)
        shared = sorted(set(values_a) & set(values_b))
        if shared:
            comparisons.append(
                paired_comparison(
                    metric, [values_a[i] for i in shared], [values_b[i] for i in shared]
                )
            )
    return comparisons


def comparability_problems(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Reasons two records should not be compared item by item."""
    problems = []
    for key in ("dataset_version", "split"):
        if a.get(key) != b.get(key):
            problems.append(f"{key} differs: {a.get(key)!r} vs {b.get(key)!r}")
    return problems


def load_record(name: str) -> dict[str, Any]:
    path = Path(name)
    if not path.exists():
        path = EXPERIMENTS_DIR / (name if name.endswith(".json") else f"{name}.json")
    if not path.exists():
        raise FileNotFoundError(f"No experiment {name!r} (looked in {EXPERIMENTS_DIR})")
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    record["_id"] = path.stem
    return record


def format_table(a: dict[str, Any], b: dict[str, Any], rows: list[PairedComparison]) -> str:
    name_a = a.get("config", {}).get("name", "A")
    name_b = b.get("config", {}).get("name", "B")
    lines = [
        f"A = {a['_id']} ({name_a})",
        f"B = {b['_id']} ({name_b})",
        "",
        f"{'metric':<20} {'n':>3} {'A':>7} {'B':>7} {'B-A':>7}  {'95% CI':<18} {'p':>6}  "
        f"{'W/L':>5}  verdict",
    ]
    for row in rows:
        verdict = "distinguishable" if row.distinguishable else "within noise"
        lines.append(
            f"{row.metric:<20} {row.n:>3} {row.mean_a:>7.3f} {row.mean_b:>7.3f} "
            f"{row.delta:>+7.3f}  [{row.low:+.3f}, {row.high:+.3f}]  {row.p_value:>6.3f}  "
            f"{row.wins:>2}/{row.losses:<2}  {verdict}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("a", help="Baseline experiment id or path")
    parser.add_argument("b", help="Candidate experiment id or path")
    parser.add_argument("--force", action="store_true", help="Compare despite a mismatch")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table")
    args = parser.parse_args(argv)

    a, b = load_record(args.a), load_record(args.b)
    problems = comparability_problems(a, b)
    if problems and not args.force:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        print(
            "These runs are over different items. Pass --force to compare anyway.", file=sys.stderr
        )
        return 2

    rows = compare_records(a, b)
    if args.json:
        print(json.dumps([row.to_dict() for row in rows], indent=2))
    else:
        print(format_table(a, b, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Print the markdown tables EXPERIMENTS.md quotes, read from `experiments/*.json`.

    cd backend && uv run python ../scripts/experiment_tables.py [section]

Sections: retrieval, correction, categories, paired, generation (default: all).

EXPERIMENTS.md is prose, so it is written by hand -- but its tables are not.
Every figure in them comes from this script, run against the committed
records, so a reader can regenerate any table and diff it against the file.
When several runs exist for one (config, split), the newest is used.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from evals.compare import compare_records, load_record  # noqa: E402

EXPERIMENTS_DIR = REPO_ROOT / "experiments"
RETRIEVAL_CONFIGS = [
    "baseline",
    "structure_aware",
    "hybrid_all_terms",
    "hybrid",
    "hybrid_bm25",
    "hybrid_rerank",
]
PAIRS = [
    ("baseline (pre-audit) → baseline", ("baseline", True), ("baseline", False)),
    ("baseline → structure_aware", ("baseline", False), ("structure_aware", False)),
    ("structure_aware → hybrid_all_terms", ("structure_aware", False), ("hybrid_all_terms", False)),
    ("hybrid_all_terms → hybrid", ("hybrid_all_terms", False), ("hybrid", False)),
    ("hybrid → hybrid_bm25", ("hybrid", False), ("hybrid_bm25", False)),
    ("hybrid → hybrid_rerank", ("hybrid", False), ("hybrid_rerank", False)),
    ("baseline → hybrid_bm25", ("baseline", False), ("hybrid_bm25", False)),
]


def record(
    name: str, split: str, *, superseded: bool = False, mode: str = "retrieval"
) -> dict[str, Any] | None:
    """Newest golden_v1 record for (config, split, mode), current or pre-audit."""
    folder = EXPERIMENTS_DIR / "superseded" if superseded else EXPERIMENTS_DIR
    found = None
    for path in sorted(folder.glob(f"2026*_{name}.json")):
        data = load_record(str(path))
        if (
            data.get("split") == split
            and data.get("dataset_version") == "golden_v1"
            and data.get("mode") == mode
        ):
            found = data
    return found


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def ci(run: dict[str, Any], metric: str) -> str:
    interval = (run.get("confidence") or {}).get(metric)
    return f" [{interval['low']:.2f}, {interval['high']:.2f}]" if interval else ""


def retrieval(split: str) -> None:
    print(f"\n#### {split}\n")
    print(
        "| Config | n | Recall@1 | Recall@5 (95% CI) | Recall@10 | MRR@10 | nDCG@10 | p50 | Failures |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for name in RETRIEVAL_CONFIGS:
        run = record(name, split)
        if run is None:
            continue
        m = run["metrics"]
        failures = ", ".join(f"{k} {v}" for k, v in run["attribution"].items()) or "—"
        print(
            f"| `{name}` | {m['count']} | {fmt(m['recall@1'])} | {fmt(m['recall@5'])}"
            f"{ci(run, 'recall@5')} | {fmt(m['recall@10'])} | {fmt(m['mrr'])} | "
            f"{fmt(m['ndcg@10'])} | {run['latency']['p50_ms']:,.0f}ms | {failures} |"
        )


def correction() -> None:
    print("\n| Config | dev before | dev after | test before | test after |")
    print("|---|---|---|---|---|")
    for name in ["baseline", "structure_aware", "hybrid", "hybrid_rerank"]:
        cells: list[str] = []
        for split in ["dev", "test"]:
            old, new = record(name, split, superseded=True), record(name, split)
            cells += [
                fmt(old["metrics"]["recall@5"]) if old else "—",
                fmt(new["metrics"]["recall@5"]) if new else "—",
            ]
        print(f"| `{name}` | " + " | ".join(cells) + " |")


def categories(split: str) -> None:
    names = ["baseline", "structure_aware", "hybrid_bm25"]
    runs = {n: record(n, split) for n in names}
    old = record("baseline", split, superseded=True)
    cats = sorted(
        {c for r in runs.values() if r for c in r["metrics_by_category"] if c != "unanswerable"}
    )
    print(f"\n#### {split}\n")
    print("| Category | n | `baseline` pre-audit | " + " | ".join(f"`{n}`" for n in names) + " |")
    print("|---|---|---|" + "---|" * len(names))
    for cat in cats:
        n = runs["baseline"]["metrics_by_category"][cat].get("count", "—")  # type: ignore[index]
        before = fmt(old["metrics_by_category"].get(cat, {}).get("recall@5")) if old else "—"
        cells = [fmt(runs[x]["metrics_by_category"].get(cat, {}).get("recall@5")) for x in names]  # type: ignore[index]
        print(f"| `{cat}` | {n} | {before} | " + " | ".join(cells) + " |")


def paired(split: str) -> None:
    print(f"\n#### {split}\n")
    print("| A → B | Recall@5 Δ (95% CI) | p | better / worse | MRR@10 Δ (95% CI) | Verdict |")
    print("|---|---|---|---|---|---|")
    for label, (a, a_old), (b, b_old) in PAIRS:
        run_a, run_b = record(a, split, superseded=a_old), record(b, split, superseded=b_old)
        if run_a is None or run_b is None:
            continue
        rows = {row.metric: row for row in compare_records(run_a, run_b)}
        r5, mrr = rows["recall@5"], rows["mrr"]
        verdict = "**distinguishable**" if (r5.distinguishable or mrr.distinguishable) else "noise"
        print(
            f"| {label} | {r5.delta:+.3f} [{r5.low:+.2f}, {r5.high:+.2f}] | {r5.p_value:.3f} | "
            f"{r5.wins} / {r5.losses} | {mrr.delta:+.3f} [{mrr.low:+.2f}, {mrr.high:+.2f}] | "
            f"{verdict} |"
        )


def generation() -> None:
    metrics = [
        ("answer_correctness", "Correctness (self-judged)"),
        ("faithfulness", "Faithfulness"),
        ("citation_precision", "Citation precision"),
        ("abstention_recall", "Abstention recall"),
        ("abstention_precision", "Abstention precision"),
        ("version_correctness", "Version correctness"),
        ("recall@5", "Retrieval recall@5"),
        ("context_recall", "Context recall (top 6)"),
    ]
    runs = {split: record("full", split, mode="full") for split in ["dev", "test"]}
    print("\n| Metric | dev | test |")
    print("|---|---|---|")
    for key, label in metrics:
        cells = []
        for split in ["dev", "test"]:
            run = runs[split]
            cells.append(fmt(run["metrics"].get(key)) + ci(run, key) if run else "—")
        print(f"| {label} | " + " | ".join(cells) + " |")
    for split in ["dev", "test"]:
        run = runs[split]
        if run:
            failures = ", ".join(f"`{k}` {v}" for k, v in run["attribution"].items()) or "none"
            cost = run["cost"]
            print(
                f"\n_{split}: {run['dataset_size']} items; failures: {failures}; "
                f"{cost['calls']} model calls ({cost['cached_calls']} cached), "
                f"${cost['cost_usd']:.2f}; record `{run['_id']}`._"
            )


def main() -> int:
    section = sys.argv[1] if len(sys.argv) > 1 else "all"
    if section in ("all", "correction"):
        correction()
    if section in ("all", "retrieval"):
        retrieval("dev")
        retrieval("test")
    if section in ("all", "paired"):
        paired("dev")
        paired("test")
    if section in ("all", "categories"):
        categories("dev")
        categories("test")
    if section in ("all", "generation"):
        generation()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

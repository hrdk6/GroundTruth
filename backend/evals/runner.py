"""The evaluation runner.

    python -m evals.runner --config configs/baseline.yaml --split dev --mode retrieval

Two modes, and the split between them is deliberate:

* **`retrieval`** needs no API key. Local embeddings, local reranker, Postgres.
  This is what CI runs on every PR, which is only possible because nothing in
  the retrieval path costs money.
* **`full`** adds generation, verification, and judging, so it needs a key and
  actually spends money.

Every run writes `experiments/{timestamp}_{config}.json` containing the config,
its hash, the git SHA, the dataset version, every metric overall and per
category, per-item results, the attribution breakdown, and the cost. That file
is the *only* source the README table is generated from -- a number that is not
in one of these files does not get published.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.core.llm import CostTracker, LLMClient, get_llm_client
from app.core.logging import configure_logging, get_logger
from app.core.pipeline import PipelineConfig, load_config
from app.core.settings import REPO_ROOT, get_settings
from app.generation.answer import AnswerService
from app.retrieval.retriever import Retriever
from evals.attribution.classify import Attribution, classify_failure, summarize
from evals.dataset.schema import GoldenDataset, load_dataset
from evals.judge.agreement import compute_agreement, load_labels
from evals.judge.judge import JudgeVerdict, judge_answer
from evals.metrics.retrieval import (
    MatchResult,
    aggregate_retrieval,
    match_candidates,
)

log = get_logger(__name__)

EXPERIMENTS_DIR = REPO_ROOT / "experiments"
Mode = Literal["retrieval", "full"]


def git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=10,
            check=False,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def git_is_dirty() -> bool:
    """A result from a dirty tree is not reproducible; the record says so."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=10,
            check=False,
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(round((p / 100) * (len(ordered) - 1)), len(ordered) - 1)
    return ordered[index]


@dataclass
class ItemResult:
    item_id: str
    category: str
    question: str
    answerable: bool
    expected_version: str | None = None

    # retrieval
    retrieved_chunk_ids: list[int] = field(default_factory=list)
    best_rank: int | None = None
    gold_found: int = 0
    gold_total: int = 0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    reciprocal_rank: float = 0.0
    ndcg_at_10: float = 0.0

    # generation
    answer: str = ""
    abstained: bool = False
    version_used: str | None = None
    judge_score: int | None = None
    judge_passed: bool | None = None
    judge_reason: str = ""
    support_fraction: float | None = None
    citation_precision: float | None = None

    attribution: str = "none"
    attribution_detail: str = ""
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    stage_timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "category": self.category,
            "question": self.question,
            "answerable": self.answerable,
            "expected_version": self.expected_version,
            "retrieved_chunk_ids": self.retrieved_chunk_ids,
            "best_rank": self.best_rank,
            "gold_found": self.gold_found,
            "gold_total": self.gold_total,
            "recall@5": round(self.recall_at_5, 4),
            "recall@10": round(self.recall_at_10, 4),
            "mrr": round(self.reciprocal_rank, 4),
            "ndcg@10": round(self.ndcg_at_10, 4),
            "answer": self.answer,
            "abstained": self.abstained,
            "version_used": self.version_used,
            "judge_score": self.judge_score,
            "judge_passed": self.judge_passed,
            "judge_reason": self.judge_reason,
            "support_fraction": self.support_fraction,
            "citation_precision": self.citation_precision,
            "attribution": self.attribution,
            "attribution_detail": self.attribution_detail,
            "latency_ms": round(self.latency_ms, 2),
            "cost_usd": round(self.cost_usd, 6),
            "stage_timings_ms": {k: round(v, 2) for k, v in self.stage_timings_ms.items()},
        }


def _generation_metrics(results: list[ItemResult], generation_ran: bool = True) -> dict[str, Any]:
    """Correctness, faithfulness, citation precision, abstention, version accuracy."""
    judged = [r for r in results if r.judge_passed is not None]
    metrics: dict[str, Any] = {}

    if judged:
        metrics["answer_correctness"] = round(
            sum(1 for r in judged if r.judge_passed) / len(judged), 4
        )
        scored = [r.judge_score for r in judged if r.judge_score]
        if scored:
            metrics["answer_score_mean"] = round(sum(scored) / len(scored), 3)

    supported = [r.support_fraction for r in results if r.support_fraction is not None]
    if supported:
        metrics["faithfulness"] = round(sum(supported) / len(supported), 4)

    cited = [r.citation_precision for r in results if r.citation_precision is not None]
    if cited:
        metrics["citation_precision"] = round(sum(cited) / len(cited), 4)

    # Abstention is a classifier: positive = "should abstain" (unanswerable).
    # Meaningless without generation, so it is omitted rather than reported as
    # a row of zeroes that looks like a measured result.
    unanswerable = [r for r in results if not r.answerable] if generation_ran else []
    answerable = [r for r in results if r.answerable]
    abstained_unanswerable = sum(1 for r in unanswerable if r.abstained)
    abstained_answerable = sum(1 for r in answerable if r.abstained)

    if unanswerable or answerable:
        total_abstentions = abstained_unanswerable + abstained_answerable
        metrics["abstention_precision"] = (
            round(abstained_unanswerable / total_abstentions, 4) if total_abstentions else None
        )
        metrics["abstention_recall"] = (
            round(abstained_unanswerable / len(unanswerable), 4) if unanswerable else None
        )
        metrics["false_answer_rate"] = (
            round((len(unanswerable) - abstained_unanswerable) / len(unanswerable), 4)
            if unanswerable
            else None
        )

    answerable = answerable if generation_ran else []
    version_items = [r for r in results if r.category == "version_sensitive" and r.expected_version]
    if version_items:
        metrics["version_correctness"] = round(
            sum(1 for r in version_items if r.version_used == r.expected_version)
            / len(version_items),
            4,
        )

    return metrics


def _aggregate(
    results: list[ItemResult], matches: list[MatchResult], generation_ran: bool = True
) -> dict[str, Any]:
    retrieval = aggregate_retrieval(matches).to_dict()
    return {**retrieval, **_generation_metrics(results, generation_ran)}


def evaluate(
    session: Session,
    config: PipelineConfig,
    dataset: GoldenDataset,
    *,
    mode: Mode = "retrieval",
    llm_client: LLMClient | None = None,
    limit: int | None = None,
    progress: bool = True,
) -> dict[str, Any]:
    """Run one experiment and return the record that gets written to disk."""
    started = time.perf_counter()
    settings = get_settings()
    items = dataset.items[:limit] if limit else dataset.items

    tracker = CostTracker()
    client = llm_client or get_llm_client()
    # A fresh tracker per run, so the reported cost is this run's cost.
    client.tracker = tracker

    retriever = Retriever(config, llm_client=client)
    service = AnswerService(config, llm_client=client) if mode == "full" else None

    results: list[ItemResult] = []
    matches: list[MatchResult] = []
    attributions: list[Attribution] = []
    judge_results: dict[str, bool] = {}

    for position, item in enumerate(items, start=1):
        if progress and position % 10 == 0:
            print(f"  {position}/{len(items)}", file=sys.stderr)

        item_started = time.perf_counter()
        cost_before = tracker.cost_usd

        result = ItemResult(
            item_id=item.id,
            category=item.category,
            question=item.question,
            answerable=item.answerable,
            expected_version=item.version,
            gold_total=len(item.gold_evidence),
        )

        if mode == "full" and service is not None:
            answer_result = service.answer(session, item.question, version=item.version)
            retrieval = answer_result.retrieval
            assert retrieval is not None
            result.answer = answer_result.answer
            result.abstained = answer_result.abstained
            result.version_used = answer_result.version_used
            verification = answer_result.verification or {}
            result.support_fraction = verification.get("support_fraction")
            result.citation_precision = verification.get("citation_precision")
            result.stage_timings_ms = answer_result.timings_ms
        else:
            retrieval, decision = retriever.retrieve(session, item.question, version=item.version)
            result.version_used = decision.version
            result.stage_timings_ms = retrieval.timings_ms

        match = match_candidates(retrieval.candidates, item.gold_evidence)
        matches.append(match)

        result.retrieved_chunk_ids = retrieval.chunk_ids()
        result.best_rank = match.best_rank
        result.gold_found = match.covered()
        from evals.metrics.retrieval import ndcg_at_k, recall_at_k, reciprocal_rank

        result.recall_at_5 = recall_at_k(match, 5)
        result.recall_at_10 = recall_at_k(match, 10)
        result.reciprocal_rank = reciprocal_rank(match)
        result.ndcg_at_10 = ndcg_at_k(match, 10)

        answered_correctly = bool(match.first_rank)
        if mode == "full":
            verdict: JudgeVerdict = judge_answer(
                client,
                question=item.question,
                reference_answer=item.reference_answer,
                answer=result.answer,
                reference_is_abstention=not item.answerable,
            )
            result.judge_score = verdict.score
            result.judge_passed = verdict.passed
            result.judge_reason = verdict.reason
            judge_results[item.id] = verdict.passed
            answered_correctly = verdict.passed

        attribution = classify_failure(
            item,
            retrieval,
            final_match=match,
            answered_correctly=answered_correctly,
            abstained=result.abstained if mode == "full" else False,
            answer_version=result.version_used,
            generation_ran=mode == "full",
        )
        attributions.append(attribution)
        result.attribution = attribution.failure
        result.attribution_detail = attribution.detail
        result.latency_ms = (time.perf_counter() - item_started) * 1000
        result.cost_usd = tracker.cost_usd - cost_before
        results.append(result)

    # --- aggregate -------------------------------------------------------
    by_category: dict[str, Any] = {}
    for category in sorted({r.category for r in results}):
        indices = [i for i, r in enumerate(results) if r.category == category]
        by_category[category] = _aggregate(
            [results[i] for i in indices], [matches[i] for i in indices], mode == "full"
        )

    latencies = [r.latency_ms for r in results]
    duration = time.perf_counter() - started

    record: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "mode": mode,
        "split": items[0].split if items else "unknown",
        "dataset_version": dataset.version,
        "dataset_size": len(items),
        "git_sha": git_sha(),
        "git_dirty": git_is_dirty(),
        "config": config.to_record(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "embedding_model": config.embedding.model,
            # Which model produced these numbers. Results from different
            # providers are not comparable, so this is not optional metadata.
            "llm_provider": settings.gt_llm_provider,
            "llm_base_url": settings.gt_llm_base_url,
            "generation_model": config.generation.model or settings.gt_generation_model,
            "cheap_model": settings.gt_cheap_model,
        },
        "metrics": _aggregate(results, matches, mode == "full"),
        "metrics_by_category": by_category,
        "attribution": summarize(attributions),
        "latency": {
            "p50_ms": round(percentile(latencies, 50), 2),
            "p95_ms": round(percentile(latencies, 95), 2),
            "total_seconds": round(duration, 2),
        },
        "cost": tracker.to_dict(),
        "items": [r.to_dict() for r in results],
    }

    if mode == "full" and judge_results:
        labels = load_labels()
        if labels:
            record["judge_agreement"] = compute_agreement(judge_results, labels).to_dict()

    return record


def write_record(record: dict[str, Any], config_name: str) -> Path:
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = EXPERIMENTS_DIR / f"{stamp}_{config_name}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def print_summary(record: dict[str, Any]) -> None:
    metrics = record["metrics"]
    print(f"\n{'=' * 64}")
    print(f"config:  {record['config']['name']}  ({record['config']['config_hash']})")
    print(f"split:   {record['split']}  mode: {record['mode']}  items: {record['dataset_size']}")
    if record.get("git_dirty"):
        print("WARNING: working tree is dirty; this result is not reproducible from the SHA")
    print(f"{'-' * 64}")
    for key in ("recall@1", "recall@5", "recall@10", "mrr", "ndcg@10"):
        if key in metrics:
            print(f"  {key:<24} {metrics[key]:.4f}")
    for key in (
        "answer_correctness",
        "faithfulness",
        "citation_precision",
        "abstention_precision",
        "abstention_recall",
        "version_correctness",
    ):
        if metrics.get(key) is not None:
            print(f"  {key:<24} {metrics[key]:.4f}")

    print(f"{'-' * 64}")
    print("  per category (recall@5):")
    for category, values in record["metrics_by_category"].items():
        if "recall@5" in values:
            print(f"    {category:<22} {values['recall@5']:.4f}  (n={values.get('count', 0)})")

    if record["attribution"]:
        print(f"{'-' * 64}")
        print("  failures:")
        for failure, count in record["attribution"].items():
            print(f"    {failure:<22} {count}")

    cost = record["cost"]
    print(f"{'-' * 64}")
    print(
        f"  latency p50 {record['latency']['p50_ms']:.0f}ms  "
        f"p95 {record['latency']['p95_ms']:.0f}ms"
    )
    print(f"  cost ${cost['cost_usd']:.4f} ({cost['calls']} calls, {cost['cached_calls']} cached)")
    print(f"{'=' * 64}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/baseline.yaml")
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("--mode", default="retrieval", choices=["retrieval", "full"])
    parser.add_argument("--dataset", default="golden_v1.jsonl")
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N items")
    parser.add_argument("--no-write", action="store_true", help="Do not write an experiment file")
    parser.add_argument(
        "--include-uncurated",
        action="store_true",
        help="Include uncurated items (never do this for a reported result)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.gt_log_level, settings.gt_log_format)

    config = load_config(args.config)
    dataset = load_dataset(args.dataset, curated_only=not args.include_uncurated)
    if args.split != "all":
        dataset = dataset.split(args.split)

    if not dataset.items:
        print(
            f"No {'curated ' if not args.include_uncurated else ''}items in split "
            f"{args.split!r} of {args.dataset}.",
            file=sys.stderr,
        )
        return 1

    if args.mode == "full" and not settings.has_llm_key:
        expected = (
            "ANTHROPIC_API_KEY" if settings.gt_llm_provider == "anthropic" else "GT_LLM_API_KEY"
        )
        print(
            f"Mode 'full' needs {expected} for provider '{settings.gt_llm_provider}'. "
            "Use --mode retrieval, or run `make llm-check`.",
            file=sys.stderr,
        )
        return 2

    print(f"Evaluating {len(dataset)} items ({args.split}) with {config.name} [{args.mode}]...")

    with session_scope() as session:
        record = evaluate(session, config, dataset, mode=args.mode, limit=args.limit)

    print_summary(record)

    if not args.no_write:
        path = write_record(record, config.name)
        print(f"Wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

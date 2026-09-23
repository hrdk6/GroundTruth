"""Build a golden set: `python -m evals.dataset.build --estimate`.

Always prints a cost estimate first and requires `--yes` to actually spend
money (PROJECT_SPEC.md §13: anything over ~$2 gets an estimate before it runs).

Splits are assigned by a hash of the item id, not randomly, so re-running the
build keeps an item in the same split. A question that drifts between dev and
test across runs would quietly contaminate the held-out set.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from app.core.llm import get_llm_client
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings
from app.ingestion.fetch import DEFAULT_VERSIONS, RAW_DIR, latest_version
from evals.dataset.generate import (
    find_version_diffs,
    generate_exact_term,
    generate_factual,
    generate_multi_hop,
    generate_table_or_code,
    generate_unanswerable,
    generate_version_sensitive,
    load_documents,
    split_sections,
)
from evals.dataset.schema import (
    TARGET_COUNTS,
    GoldenDataset,
    GoldenItem,
    load_dataset,
    save_dataset,
)

log = get_logger(__name__)

# Rough per-item cost, measured against Haiku 4.5 pricing: a section prompt is
# ~1.5k input tokens and ~200 output tokens. Deliberately an over-estimate.
COST_PER_ITEM_USD = 0.0025
DEV_FRACTION = 0.6


def assign_split(item_id: str, dev_fraction: float = DEV_FRACTION) -> str:
    """Deterministic split from the item id, so re-runs do not reshuffle."""
    digest = hashlib.sha256(item_id.encode()).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "dev" if bucket < dev_fraction else "test"


def _load_existing(output: str) -> list[GoldenItem]:
    """Every item already at `output`, curated or not, or nothing."""
    try:
        return load_dataset(output, curated_only=False).items
    except FileNotFoundError:
        return []


def merge_drafts(existing: list[GoldenItem], generated: list[GoldenItem]) -> list[GoldenItem]:
    """The generated items worth adding to `existing`, with splits assigned.

    Merging, never replacing: the default output is the curated golden set,
    and a generator run is a source of *drafts*. Writing its output over the
    file -- which this script used to do -- destroyed every hand-curated item
    in it. Existing items are never modified; a draft is dropped when its id
    or its question is already present (different sections can yield the
    same question).
    """
    seen_ids = {item.id for item in existing}
    seen_questions = {item.question.strip().lower() for item in existing}

    added: list[GoldenItem] = []
    for item in generated:
        key = item.question.strip().lower()
        if item.id in seen_ids or key in seen_questions:
            continue
        seen_ids.add(item.id)
        seen_questions.add(key)
        item.split = assign_split(item.id)  # type: ignore[assignment]
        added.append(item)
    return added


def estimate_cost(counts: dict[str, int]) -> float:
    # Generators discard a good share of candidates (bad quotes, skips), so the
    # number of model calls exceeds the number of items kept.
    attempts = sum(counts.values()) * 1.8
    return attempts * COST_PER_ITEM_USD


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="golden_v1.jsonl")
    parser.add_argument("--root", type=Path, default=None, help="Corpus root (default data/raw)")
    parser.add_argument("--versions", nargs="*", default=list(DEFAULT_VERSIONS))
    parser.add_argument("--model", default=None, help="Override the generation model")
    parser.add_argument("--limit-docs", type=int, default=None, help="Cap documents per version")
    parser.add_argument("--estimate", action="store_true", help="Print the cost and stop")
    parser.add_argument("--yes", action="store_true", help="Actually spend money")
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Generate only these categories",
    )
    parser.add_argument("--scale", type=float, default=1.0, help="Scale target counts")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.gt_log_level, settings.gt_log_format)

    targets: dict[str, int] = {
        str(category): max(1, int(count * args.scale))
        for category, count in TARGET_COUNTS.items()
        if args.only is None or category in args.only
    }

    estimated = estimate_cost(targets)
    print("Planned golden set:")
    for category, count in targets.items():
        print(f"  {category:<20} {count}")
    print(f"\nEstimated cost: ${estimated:.2f} (over-estimate; the LLM cache makes re-runs free)")

    if args.estimate:
        return 0
    if not args.yes:
        print("\nRe-run with --yes to generate. Nothing was spent.", file=sys.stderr)
        return 1
    if not settings.has_llm_key:
        expected = (
            "ANTHROPIC_API_KEY" if settings.gt_llm_provider == "anthropic" else "GT_LLM_API_KEY"
        )
        print(f"{expected} is not set; generation needs it.", file=sys.stderr)
        return 2

    root = args.root or RAW_DIR
    newest = latest_version(args.versions)
    client = get_llm_client()

    print(f"\nLoading corpus from {root} ...")
    documents_by_version = {
        version: load_documents(root / version, version, limit=args.limit_docs)
        for version in args.versions
    }
    for version, documents in documents_by_version.items():
        print(f"  {version}: {len(documents)} documents")

    newest_documents = documents_by_version[newest]
    sections = [s for d in newest_documents for s in split_sections(d)]
    print(f"  {len(sections)} sections in v{newest}")

    items: list[GoldenItem] = []

    if "factual" in targets:
        print("\nGenerating factual ...")
        items += generate_factual(client, sections, count=targets["factual"], model=args.model)
    if "exact_term" in targets:
        print("Generating exact_term ...")
        items += generate_exact_term(
            client, sections, count=targets["exact_term"], model=args.model
        )
    if "table_or_code" in targets:
        print("Generating table_or_code ...")
        items += generate_table_or_code(
            client, sections, count=targets["table_or_code"], model=args.model
        )
    if "version_sensitive" in targets:
        print("Diffing versions ...")
        older = [v for v in args.versions if v != newest]
        diffs = []
        for version in older:
            diffs += find_version_diffs(
                documents_by_version[version], newest_documents, limit=targets["version_sensitive"]
            )
        print(f"  {len(diffs)} materially-changed pages")
        items += generate_version_sensitive(
            client, diffs, count=targets["version_sensitive"], model=args.model
        )
    if "multi_hop" in targets:
        print("Generating multi_hop ...")
        items += generate_multi_hop(
            client, newest_documents, sections, count=targets["multi_hop"], model=args.model
        )
    if "unanswerable" in targets:
        print("Generating unanswerable ...")
        items += generate_unanswerable(
            client, sections, count=targets["unanswerable"], model=args.model
        )

    existing = _load_existing(args.output)
    added = merge_drafts(existing, items)
    dataset = GoldenDataset(existing + added)
    path = save_dataset(dataset, args.output)

    curated = sum(1 for item in existing if item.curated)
    print(f"\nWrote {path}: kept {len(existing)} existing ({curated} curated), added {len(added)}")
    print(f"  new by category: {GoldenDataset(added).counts()}")
    print(
        f"  new dev/test:    {sum(1 for i in added if i.split == 'dev')}/"
        f"{sum(1 for i in added if i.split == 'test')}"
    )
    print(f"  actual cost: ${client.tracker.cost_usd:.2f}")
    print("\nNext: `python -m evals.dataset.curate` — only curated items are reported.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

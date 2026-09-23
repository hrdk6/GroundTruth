#!/usr/bin/env python
"""Generate the README results table from `experiments/*.json`.

PROJECT_SPEC.md S3.2: no number in the README is ever typed by hand. This
script is the only thing allowed to write between the RESULTS_TABLE markers,
and it reads exclusively from committed experiment files.

Usage:
    python scripts/generate_results_table.py [--split test] [--check]

`--check` verifies the README is already up to date and exits non-zero if not,
which is what CI runs to catch a hand-edited table.

What the table shows, and why:

* **One row per (config, dataset, split, mode)** -- the newest run. Older runs
  stay in `experiments/` as history; a table of every run ever made buries the
  current result under the ones it superseded.
* **n and a 95% interval** on the headline metrics. On 19 items one item is
  ~5 points; an interval says so without anyone having to remember it.
* **A dirty-tree marker.** A run from uncommitted code is not reproducible
  from its SHA, and the table says which ones those are.

Standard library only: CI runs this with the system Python, before `uv sync`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS_DIR = REPO_ROOT / "experiments"
README = REPO_ROOT / "README.md"

START_MARKER = "<!-- RESULTS_TABLE_START -->"
END_MARKER = "<!-- RESULTS_TABLE_END -->"

EMPTY_MESSAGE = "No experiments recorded yet. Run `make eval` and then `make results`."

# The order experiments were run in, so the table reads as a progression.
CONFIG_ORDER = [
    "baseline",
    "structure_aware",
    "hybrid_all_terms",
    "hybrid",
    "hybrid_bm25",
    "hybrid_rerank",
    "full",
]
SPLIT_ORDER = ["dev", "test", "all"]

# (json key in metrics, column header, show a confidence interval)
COLUMNS: list[tuple[str, str, bool]] = [
    ("recall@5", "Recall@5 (95% CI)", True),
    ("recall@10", "Recall@10", False),
    ("mrr", "MRR@10", False),
    ("ndcg@10", "nDCG@10", False),
    ("answer_correctness", "Correctness (95% CI)", True),
    ("faithfulness", "Faithfulness", False),
    ("citation_precision", "Citation prec.", False),
]


def load_experiments(split: str | None = None) -> list[dict[str, Any]]:
    """Every experiment file, optionally filtered by split."""
    runs: list[dict[str, Any]] = []
    for path in sorted(EXPERIMENTS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"warning: skipping unparseable {path.name}", file=sys.stderr)
            continue
        if split and data.get("split") != split:
            continue
        data["_file"] = path.name
        runs.append(data)
    return runs


def latest_per_group(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The newest run for each (config, dataset, split, mode), in reading order."""
    newest: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for run in runs:
        key = (
            str(run.get("config", {}).get("name", "?")),
            str(run.get("dataset_version", "?")),
            str(run.get("split", "?")),
            str(run.get("mode", "?")),
        )
        stamp = str(run.get("timestamp", run["_file"]))
        if key not in newest or stamp > str(newest[key].get("timestamp", newest[key]["_file"])):
            newest[key] = run

    def order(run: dict[str, Any]) -> tuple[int, str, int, int, str]:
        config = str(run.get("config", {}).get("name", "?"))
        split = str(run.get("split", "?"))
        return (
            # Fixture runs are CI's gate, not a result; they go last.
            1 if run.get("dataset_version") == "fixture_golden" else 0,
            str(run.get("dataset_version", "")),
            CONFIG_ORDER.index(config) if config in CONFIG_ORDER else len(CONFIG_ORDER),
            SPLIT_ORDER.index(split) if split in SPLIT_ORDER else len(SPLIT_ORDER),
            config,
        )

    return sorted(newest.values(), key=order)


def format_value(run: dict[str, Any], key: str, with_ci: bool) -> str:
    value = run.get("metrics", {}).get(key)
    if value is None:
        return "—"
    try:
        text = f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)
    interval = (run.get("confidence") or {}).get(key)
    if with_ci and interval:
        text += f" [{interval['low']:.2f}, {interval['high']:.2f}]"
    return text


def build_table(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return EMPTY_MESSAGE

    # Only columns at least one run reports, so a retrieval-only table isn't
    # padded with empty generation columns.
    present = [c for c in COLUMNS if any(c[0] in run.get("metrics", {}) for run in runs)]

    # The dataset column is not decoration: a fixture run and a golden-set
    # run produce different numbers, and a table that hides which is which
    # invites comparing them.
    headers = [
        "Config",
        "Dataset",
        "Split",
        "n",
        *(header for _, header, _ in present),
        "p50",
        "Cost",
        "Commit",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]

    any_dirty = False
    any_cached = False
    for run in runs:
        cost = run.get("cost", {}).get("cost_usd")
        p50 = run.get("latency", {}).get("p50_ms")
        n = run.get("metrics", {}).get("count")
        sha = str(run.get("git_sha") or "")[:7] or "—"
        # A run that mostly replayed the LLM cache measured the cache, not the
        # model: its latency is not what a user would wait.
        cached = run.get("cost", {}).get("calls", 0) and (
            run.get("cost", {}).get("cache_hit_rate", 0) >= 0.5
        )
        latency = f"{float(p50):,.0f}ms" if p50 is not None else "—"
        if cached:
            latency += "†"
            any_cached = True
        if run.get("git_dirty"):
            sha += "*"
            any_dirty = True
        row = [
            f"`{run.get('config', {}).get('name', '?')}`",
            f"`{run.get('dataset_version', '?')}`",
            str(run.get("split", "?")),
            str(n) if n is not None else "—",
            *(format_value(run, key, with_ci) for key, _, with_ci in present),
            latency,
            f"${float(cost):.2f}" if cost is not None else "—",
            f"`{sha}`",
        ]
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    notes = [
        f"_Generated by `scripts/generate_results_table.py`: the newest run in each of "
        f"{len(runs)} (config, dataset, split, mode) groups in `experiments/`. "
        f"Do not edit by hand._",
        "_`n` counts items with gold evidence (retrieval metrics); intervals are a "
        "95% percentile bootstrap over items. Recall@k, MRR@10 and nDCG@10 are over "
        "the full ranked list; see `context_recall` in each file for the top-`k_final` cut._",
    ]
    if any_cached:
        notes.append(
            "_† mostly served from the LLM cache, so this p50 is not a cold-query "
            "latency; see EXPERIMENTS.md._"
        )
    if any_dirty:
        notes.append(
            "_`*` = recorded from a working tree with uncommitted changes, so not "
            "reproducible from the SHA alone._"
        )
    lines.extend(notes)
    return "\n".join(lines)


def splice(readme: str, table: str) -> str:
    start = readme.index(START_MARKER) + len(START_MARKER)
    end = readme.index(END_MARKER)
    return readme[:start] + "\n" + table + "\n" + readme[end:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", help="Only include runs from this split (e.g. test)")
    parser.add_argument("--check", action="store_true", help="Fail if the README is out of date")
    args = parser.parse_args()

    readme = README.read_text(encoding="utf-8")
    if START_MARKER not in readme or END_MARKER not in readme:
        print("error: README is missing the RESULTS_TABLE markers", file=sys.stderr)
        return 2

    runs = latest_per_group(load_experiments(args.split))
    updated = splice(readme, build_table(runs))

    if args.check:
        if updated != readme:
            print("error: README results table is stale. Run `make results`.", file=sys.stderr)
            return 1
        print("README results table is up to date.")
        return 0

    if updated == readme:
        print(f"README already up to date ({len(runs)} row(s)).")
        return 0

    README.write_text(updated, encoding="utf-8")
    print(f"README results table updated: {len(runs)} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Labeling CLI: `python -m evals.judge.label --experiment <id>`.

Presents answers from a completed experiment and takes a human correct/faithful
judgement on each, so `agreement.py` can measure whether the LLM judge can be
trusted.

**The judge's own verdict is hidden by default.** Seeing "the judge said pass"
before deciding is anchoring, and an anchored label makes the agreement number
meaningless -- it would measure how persuasive the judge is, not how accurate.
Pass `--show-judge` only when reviewing disagreements after the fact.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from app.core.settings import REPO_ROOT
from evals.judge.agreement import HumanLabel, compute_agreement, load_labels, save_labels

console = Console()
EXPERIMENTS_DIR = REPO_ROOT / "experiments"


def latest_experiment() -> Path | None:
    files = sorted(EXPERIMENTS_DIR.glob("*.json"), reverse=True)
    return files[0] if files else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default=None, help="Experiment id (default: newest)")
    parser.add_argument("--count", type=int, default=50, help="How many items to label")
    parser.add_argument("--seed", type=int, default=0, help="Sampling seed")
    parser.add_argument("--show-judge", action="store_true", help="Reveal the judge (anchors you)")
    parser.add_argument("--report", action="store_true", help="Print agreement and exit")
    args = parser.parse_args(argv)

    path = EXPERIMENTS_DIR / f"{args.experiment}.json" if args.experiment else latest_experiment()
    if path is None or not path.exists():
        console.print("[red]No experiment found. Run `make eval MODE=full` first.[/red]")
        return 1

    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("mode") != "full":
        console.print(
            f"[yellow]{path.name} is a retrieval-only run; there are no answers to label.[/yellow]"
        )
        return 1

    items = record.get("items", [])
    judge_results = {
        i["item_id"]: bool(i["judge_passed"]) for i in items if i.get("judge_passed") is not None
    }
    existing = {label.item_id: label for label in load_labels()}

    if args.report:
        report = compute_agreement(judge_results, list(existing.values()))
        console.print(Panel(json.dumps(report.to_dict(), indent=2), title="Judge agreement"))
        if report.n and report.kappa < 0.6:
            console.print(
                "[yellow]kappa below 0.6: the judge is not reliable enough to publish "
                "generation metrics from. Iterate on the judge prompt and log it in "
                "EXPERIMENTS.md.[/yellow]"
            )
        return 0

    # Stratify by category so the labelled sample is not all one kind of item.
    by_category: dict[str, list[dict]] = {}
    for item in items:
        if item["item_id"] in existing:
            continue
        by_category.setdefault(item["category"], []).append(item)

    rng = random.Random(args.seed)
    sample: list[dict] = []
    categories = sorted(by_category)
    per_category = max(1, args.count // max(1, len(categories)))
    for category in categories:
        pool = by_category[category]
        rng.shuffle(pool)
        sample.extend(pool[:per_category])
    rng.shuffle(sample)
    sample = sample[: args.count]

    if not sample:
        console.print("[green]Everything in this experiment is already labelled.[/green]")
        return 0

    console.print(
        Panel(
            f"Labelling {len(sample)} answers from [bold]{path.stem}[/bold].\n"
            "[dim]Judge verdicts are hidden so your labels stay independent.[/dim]\n"
            "Ctrl-C to stop; labels are saved as you go.",
            title="Judge validation",
        )
    )

    labels = list(existing.values())
    try:
        for position, item in enumerate(sample, start=1):
            console.print()
            console.print(
                f"[bold]{position}/{len(sample)}[/bold]  [dim]{item['item_id']}[/dim]  "
                f"category=[cyan]{item['category']}[/cyan]"
            )
            console.print(Panel(item["question"], title="Question", border_style="blue"))

            if not item["answerable"]:
                console.print(
                    "[yellow]This question is unanswerable: abstaining is correct.[/yellow]"
                )

            console.print(Panel(item.get("answer") or "[dim](empty)[/dim]", title="System answer"))
            if args.show_judge:
                console.print(
                    f"[dim]judge: {'PASS' if item.get('judge_passed') else 'FAIL'} "
                    f"({item.get('judge_score')}) — {item.get('judge_reason')}[/dim]"
                )

            decision = Prompt.ask("Correct?", choices=["y", "n", "s", "q"], default="y")
            if decision == "q":
                break
            if decision == "s":
                continue

            faithful: bool | None = None
            if decision == "y":
                faithful = Confirm.ask("Every claim supported by its citation?", default=True)

            labels.append(
                HumanLabel(
                    item_id=item["item_id"],
                    correct=decision == "y",
                    faithful=faithful,
                )
            )
            save_labels(labels)
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")

    save_labels(labels)
    report = compute_agreement(judge_results, labels)
    console.print()
    console.print(Panel(json.dumps(report.to_dict(), indent=2), title="Judge agreement so far"))
    if report.n < 30:
        console.print(f"[dim]Only {report.n} labels; aim for ~50 before reporting kappa.[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

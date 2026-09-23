"""Labeling CLI: `python -m evals.judge.label --experiment <id>`.

Presents answers from a completed `full` experiment and takes a human
correct/faithful judgement on each, so `agreement.py` can measure whether the
LLM judge can be trusted.

What the labeller sees is chosen so the label and the judge answer the *same*
question:

* **The reference answer is shown.** The judge grades against it, so a human
  grading from memory instead would be measuring a different thing.
* **The cited excerpts are shown.** "Is every claim supported by its
  citation?" cannot be answered without them.
* **The judge's verdict is hidden by default.** Seeing "the judge said pass"
  first is anchoring, and an anchored label measures how persuasive the judge
  is, not how accurate. `--show-judge` is for reviewing disagreements after.

Only items the *model* judged are offered. Abstentions are graded by an exact
string rule; labelling them would add agreement the judge never earned.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from app.core.settings import REPO_ROOT
from evals.judge.agreement import (
    HumanLabel,
    answer_digest,
    compute_agreement,
    load_labels,
    save_labels,
)

console = Console()
EXPERIMENTS_DIR = REPO_ROOT / "experiments"


def latest_full_experiment() -> Path | None:
    """Newest experiment that actually produced answers."""
    for path in sorted(EXPERIMENTS_DIR.glob("*.json"), reverse=True):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("mode") == "full":
                return path
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _judge_view(items: list[dict[str, Any]]) -> tuple[dict[str, bool], dict[str, str]]:
    """Model-judged verdicts and the digest of each answer they graded."""
    results: dict[str, bool] = {}
    shas: dict[str, str] = {}
    for item in items:
        if item.get("judge_passed") is None or item.get("judge_deterministic"):
            continue
        results[item["item_id"]] = bool(item["judge_passed"])
        shas[item["item_id"]] = item.get("answer_sha") or answer_digest(item.get("answer", ""))
    return results, shas


def _show(item: dict[str, Any], position: int, total: int, show_judge: bool) -> None:
    console.print()
    console.print(
        f"[bold]{position}/{total}[/bold]  [dim]{item['item_id']}[/dim]  "
        f"category=[cyan]{item['category']}[/cyan]"
    )
    console.print(Panel(item["question"], title="Question", border_style="blue"))
    console.print(
        Panel(item.get("reference_answer") or "[dim](none recorded)[/dim]", title="Reference")
    )
    console.print(Panel(item.get("answer") or "[dim](empty)[/dim]", title="System answer"))

    for citation in item.get("citations") or []:
        where = f"{citation['source_path']} v{citation['version']}"
        if citation.get("heading_path"):
            where += f" · {citation['heading_path']}"
        console.print(
            Panel(
                citation.get("text", ""),
                title=f"[{citation['marker']}] {where}",
                border_style="dim",
            )
        )

    if show_judge:
        console.print(
            f"[dim]judge: {'PASS' if item.get('judge_passed') else 'FAIL'} "
            f"({item.get('judge_score')}) — {item.get('judge_reason')}[/dim]"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment", default=None, help="Experiment id (default: newest full run)"
    )
    parser.add_argument("--count", type=int, default=50, help="How many items to label")
    parser.add_argument("--seed", type=int, default=0, help="Sampling seed")
    parser.add_argument("--show-judge", action="store_true", help="Reveal the judge (anchors you)")
    parser.add_argument("--report", action="store_true", help="Print agreement and exit")
    args = parser.parse_args(argv)

    path = (
        EXPERIMENTS_DIR / f"{args.experiment}.json" if args.experiment else latest_full_experiment()
    )
    if path is None or not path.exists():
        console.print("[red]No full-mode experiment found. Run `make eval MODE=full` first.[/red]")
        return 1

    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("mode") != "full":
        console.print(
            f"[yellow]{path.name} is a retrieval-only run; there are no answers to label.[/yellow]"
        )
        return 1

    items = record.get("items", [])
    judge_results, answer_shas = _judge_view(items)
    existing = load_labels()

    if args.report:
        report = compute_agreement(judge_results, existing, answer_shas)
        console.print(Panel(json.dumps(report.to_dict(), indent=2), title="Judge agreement"))
        if report.stale_labels:
            console.print(
                f"[yellow]{len(report.stale_labels)} label(s) were made against a different "
                "answer and were skipped. Re-label them for this run.[/yellow]"
            )
        if report.n and report.kappa < 0.6:
            console.print(
                "[yellow]kappa below 0.6: the judge is not reliable enough to publish "
                "generation metrics from. Iterate on the judge prompt and log it in "
                "EXPERIMENTS.md.[/yellow]"
            )
        return 0

    # Already labelled *for this exact answer* -> skip. A label for an older
    # answer to the same question does not count.
    done = {
        label.item_id
        for label in existing
        if label.answer_sha is None or label.answer_sha == answer_shas.get(label.item_id)
    }

    # Stratify by category so the labelled sample is not all one kind of item.
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item["item_id"] in judge_results and item["item_id"] not in done:
            by_category.setdefault(item["category"], []).append(item)

    rng = random.Random(args.seed)
    sample: list[dict[str, Any]] = []
    per_category = max(1, args.count // max(1, len(by_category)))
    for category in sorted(by_category):
        pool = by_category[category]
        rng.shuffle(pool)
        sample.extend(pool[:per_category])
    rng.shuffle(sample)
    sample = sample[: args.count]

    if not sample:
        console.print("[green]Every model-judged answer in this run is already labelled.[/green]")
        return 0

    console.print(
        Panel(
            f"Labelling {len(sample)} answers from [bold]{path.stem}[/bold].\n"
            "[dim]Judge verdicts are hidden so your labels stay independent. "
            "Grade the system answer against the reference.[/dim]\n"
            "Ctrl-C to stop; labels are saved as you go.",
            title="Judge validation",
        )
    )

    # Replacing a stale label for the same item, not stacking a second one.
    labels = {label.item_id: label for label in existing}
    try:
        for position, item in enumerate(sample, start=1):
            _show(item, position, len(sample), args.show_judge)

            decision = Prompt.ask("Correct?", choices=["y", "n", "s", "q"], default="y")
            if decision == "q":
                break
            if decision == "s":
                continue

            faithful: bool | None = None
            if item.get("citations"):
                faithful = Confirm.ask("Every claim supported by its citation?", default=True)

            labels[item["item_id"]] = HumanLabel(
                item_id=item["item_id"],
                correct=decision == "y",
                faithful=faithful,
                answer_sha=answer_shas[item["item_id"]],
                experiment=path.stem,
            )
            save_labels(list(labels.values()))
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")

    save_labels(list(labels.values()))
    report = compute_agreement(judge_results, list(labels.values()), answer_shas)
    console.print()
    console.print(Panel(json.dumps(report.to_dict(), indent=2), title="Judge agreement so far"))
    if report.n < 30:
        console.print(f"[dim]Only {report.n} labels; aim for ~50 before reporting kappa.[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

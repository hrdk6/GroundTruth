"""Curation CLI: `python -m evals.dataset.curate`.

Shows each generated item with its evidence and takes accept / edit / reject.
Only accepted items get `curated: true`, and only curated items are used for
reported results -- an LLM-written question graded against an LLM-written
answer is a draft, and publishing numbers from it would be measuring the
generator, not the system.

Progress is saved after every decision, so a session can be interrupted and
resumed without losing work or re-reviewing anything.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from evals.dataset.schema import GoldenItem, load_dataset, save_dataset

console = Console()


def show_item(item: GoldenItem, position: int, total: int, reviewed: int) -> None:
    header = (
        f"[bold]{position}/{total}[/bold]  "
        f"[dim]{item.id}[/dim]  "
        f"category=[cyan]{item.category}[/cyan]  "
        f"split=[magenta]{item.split}[/magenta]  "
        f"[dim]({reviewed} curated so far)[/dim]"
    )
    console.print()
    console.print(header)
    console.print(Panel(item.question, title="Question", border_style="blue"))

    if item.answerable:
        console.print(Panel(item.reference_answer or "[dim](none)[/dim]", title="Reference answer"))
    else:
        console.print(
            Panel(
                f"[yellow]UNANSWERABLE[/yellow] — the system should refuse.\n{item.notes}",
                title="Expected behaviour",
            )
        )

    if item.gold_evidence:
        table = Table(title="Gold evidence", show_lines=False, header_style="bold")
        table.add_column("source", overflow="fold")
        table.add_column("v", width=5)
        table.add_column("section", overflow="fold")
        table.add_column("quote", overflow="fold")
        for evidence in item.gold_evidence:
            table.add_row(
                evidence.source_path,
                evidence.version,
                evidence.heading_path or "[dim]—[/dim]",
                f"“{evidence.key_quote}”" if evidence.key_quote else "[dim]—[/dim]",
            )
        console.print(table)

    if item.meta.get("old_answer"):
        console.print(
            Panel(
                f"v{item.meta.get('old_version')}: {item.meta['old_answer']}\n"
                f"[dim]quote verified: {item.meta.get('old_quote_verified')}[/dim]",
                title="Previous version",
                border_style="yellow",
            )
        )


def edit_item(item: GoldenItem) -> GoldenItem:
    console.print("[dim]Enter to keep the current value.[/dim]")
    question = Prompt.ask("Question", default=item.question)
    item.question = question.strip() or item.question

    if item.answerable:
        answer = Prompt.ask("Reference answer", default=item.reference_answer)
        item.reference_answer = answer.strip() or item.reference_answer

    if item.gold_evidence:
        for index, evidence in enumerate(item.gold_evidence, start=1):
            quote = Prompt.ask(f"Quote {index}", default=evidence.key_quote)
            evidence.key_quote = quote.strip()

    notes = Prompt.ask("Notes", default=item.notes)
    item.notes = notes.strip()
    return item


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="golden_v1.jsonl")
    parser.add_argument("--category", default=None, help="Review only one category")
    parser.add_argument("--recurate", action="store_true", help="Review already-curated items too")
    args = parser.parse_args(argv)

    try:
        dataset = load_dataset(args.dataset, curated_only=False)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Run `python -m evals.dataset.build --yes` first.")
        return 1

    path = dataset.path or Path(args.dataset)
    # A rejection is a decision too: without the `rejected` check, every
    # session re-presented every item ever rejected.
    pending = [
        i
        for i in dataset.items
        if (args.recurate or (not i.curated and not i.meta.get("rejected")))
        and (args.category is None or i.category == args.category)
    ]

    if not pending:
        console.print("[green]Nothing left to curate.[/green]")
        console.print(f"Curated: {len(dataset.curated_only())}/{len(dataset)}")
        return 0

    console.print(
        Panel(
            "[bold]a[/bold]ccept   [bold]e[/bold]dit then accept   "
            "[bold]r[/bold]eject   [bold]s[/bold]kip   [bold]q[/bold]uit\n"
            "[dim]Rejected items stay in the file as uncurated and are never "
            "used for reported results.[/dim]",
            title="Curation",
        )
    )

    rejected = 0
    for position, item in enumerate(pending, start=1):
        show_item(item, position, len(pending), len(dataset.curated_only()))
        choice = Prompt.ask("Decision", choices=["a", "e", "r", "s", "q"], default="a")

        if choice == "q":
            break
        if choice == "s":
            continue
        if choice == "r":
            item.curated = False
            item.meta["rejected"] = True
            rejected += 1
        else:
            if choice == "e":
                item = edit_item(item)
            item.curated = True
            item.meta.pop("rejected", None)

        # Save after every decision: an interrupted session loses nothing.
        save_dataset(dataset, path)

    curated = dataset.curated_only()
    console.print()
    console.print(
        f"[green]Curated: {len(curated)}/{len(dataset)}[/green]  rejected this session: {rejected}"
    )
    console.print(f"  by category: {curated.counts()}")
    console.print(f"  dev/test: {len(curated.split('dev'))}/{len(curated.split('test'))}")
    save_dataset(dataset, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

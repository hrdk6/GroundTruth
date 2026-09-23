"""Retrieval regression gate: `python -m evals.gate --experiment <path>`.

Compares an experiment record against `evals/thresholds.yaml` and exits
non-zero when a tracked metric is below its floor. CI runs this after the
fixture-corpus eval, and writes the table to the job summary.

A floor of `null` is reported but never fails the build. That is deliberate:
before the first real fixture run there is nothing to compare against, and a
gate that passes vacuously while *looking* meaningful is worse than one that
says plainly that it has no floors yet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from app.core.settings import REPO_ROOT

THRESHOLDS_PATH = REPO_ROOT / "backend" / "evals" / "thresholds.yaml"


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"No thresholds file at {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def latest_experiment() -> Path | None:
    directory = REPO_ROOT / "experiments"
    if not directory.exists():
        return None
    files = sorted(directory.glob("*.json"), reverse=True)
    return files[0] if files else None


class Check:
    def __init__(self, name: str, actual: float | None, floor: float | None) -> None:
        self.name = name
        self.actual = actual
        self.floor = floor

    @property
    def status(self) -> str:
        if self.floor is None:
            return "unset"
        if self.actual is None:
            return "missing"
        return "pass" if self.actual >= self.floor else "FAIL"

    @property
    def failed(self) -> bool:
        # A metric the gate tracks but the run did not produce is a failure:
        # silently skipping it would let a removed metric pass the gate.
        return self.status in ("FAIL", "missing")

    def row(self) -> str:
        actual = f"{self.actual:.4f}" if self.actual is not None else "—"
        floor = f"{self.floor:.4f}" if self.floor is not None else "unset"
        # ASCII only: Windows consoles default to cp1252, and a gate that
        # crashes on a UnicodeEncodeError while reporting results is useless.
        mark = {"pass": "ok", "FAIL": "FAIL", "unset": "-", "missing": "?"}[self.status]
        return f"| {mark} | `{self.name}` | {actual} | {floor} |"


def build_checks(record: dict[str, Any], thresholds: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []

    metrics = record.get("metrics", {})
    for name, floor in (thresholds.get("metrics") or {}).items():
        checks.append(Check(name, metrics.get(name), floor))

    by_category = record.get("metrics_by_category", {})
    for category, floors in (thresholds.get("by_category") or {}).items():
        observed = by_category.get(category, {})
        for name, floor in (floors or {}).items():
            checks.append(Check(f"{category}.{name}", observed.get(name), floor))

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=None, help="Experiment JSON")
    parser.add_argument("--thresholds", type=Path, default=THRESHOLDS_PATH)
    parser.add_argument("--summary", type=Path, default=None, help="Write a markdown summary here")
    args = parser.parse_args(argv)

    path = args.experiment or latest_experiment()
    if path is None or not path.exists():
        print("No experiment file to check. Run the eval first.", file=sys.stderr)
        return 2

    record = json.loads(path.read_text(encoding="utf-8"))
    thresholds = load_thresholds(args.thresholds)
    checks = build_checks(record, thresholds)

    lines = [
        "### Retrieval regression gate",
        "",
        f"Run `{path.name}` · config `{record.get('config', {}).get('name', '?')}` "
        f"· split `{record.get('split', '?')}` · {record.get('dataset_size', 0)} items",
        "",
        "| | Metric | Value | Floor |",
        "|---|---|---|---|",
        *[check.row() for check in checks],
    ]

    attribution = record.get("attribution") or {}
    if attribution:
        lines += ["", "**Failures by cause**", ""]
        lines += [f"- `{name}`: {count}" for name, count in attribution.items()]

    failed = [c for c in checks if c.failed]
    unset = [c for c in checks if c.status == "unset"]

    if failed:
        lines += ["", f"**{len(failed)} metric(s) below floor.**"]
    elif unset and len(unset) == len(checks):
        lines += [
            "",
            "No floors are set yet, so this gate cannot fail. Set them in `evals/thresholds.yaml` once a baseline exists.",
        ]
    else:
        lines += ["", "All tracked metrics are at or above their floors."]

    report = "\n".join(lines)
    print(report)

    # GitHub Actions job summary, when running there.
    summary_path = args.summary or (
        Path(os.environ["GITHUB_STEP_SUMMARY"]) if os.environ.get("GITHUB_STEP_SUMMARY") else None
    )
    if summary_path:
        with summary_path.open("a", encoding="utf-8") as fh:
            fh.write(report + "\n")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

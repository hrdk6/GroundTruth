"""Judge validation: how often does the judge agree with a human?

Accuracy alone is misleading here. If 85% of answers are correct, a judge that
blindly says "pass" scores 85% accuracy while carrying no information at all.
Cohen's kappa corrects for exactly that by discounting the agreement you would
expect from chance:

    kappa = (observed - expected) / (1 - expected)

Landis & Koch's conventional reading, which the report prints:

    < 0.00  poor       0.00-0.20  slight     0.21-0.40  fair
    0.41-0.60  moderate  0.61-0.80  substantial  0.81-1.00  almost perfect

Below ~0.6 the judge is not trustworthy enough to report generation metrics
from, and the honest response is to iterate on the judge prompt and log it in
EXPERIMENTS.md -- not to publish the numbers with a caveat.

Two rules keep the agreement number honest:

* **A label belongs to an answer, not to a question.** Each label stores a
  hash of the answer text it was given. Re-running an experiment produces new
  answers for the same item ids, and scoring old labels against new answers
  would measure nothing. Labels whose answer no longer matches are reported
  as `stale` and left out.
* **Only model verdicts count.** Abstentions are graded by an exact string
  rule, not by the judge; including them would pad agreement with matches the
  judge never made. The runner passes only non-deterministic verdicts here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.settings import REPO_ROOT

LABELS_DIR = REPO_ROOT / "data" / "golden"
DEFAULT_LABELS = LABELS_DIR / "human_labels.jsonl"


def answer_digest(answer: str) -> str:
    """Stable identity of the exact answer a label was made against."""
    return hashlib.sha256(" ".join(answer.split()).encode("utf-8")).hexdigest()[:16]


@dataclass
class HumanLabel:
    """One hand-labelled item."""

    item_id: str
    correct: bool
    faithful: bool | None = None
    notes: str = ""
    # Hash of the answer that was labelled, and the run it came from. A label
    # without a digest predates this rule and matches any answer.
    answer_sha: str | None = None
    experiment: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgreementReport:
    n: int = 0
    accuracy: float = 0.0
    kappa: float = 0.0
    # Confusion counts, judge vs human.
    both_pass: int = 0
    both_fail: int = 0
    judge_pass_human_fail: int = 0  # judge too lenient
    judge_fail_human_pass: int = 0  # judge too strict
    interpretation: str = ""
    missing_labels: list[str] = field(default_factory=list)
    stale_labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "accuracy": round(self.accuracy, 4),
            "cohens_kappa": round(self.kappa, 4),
            "interpretation": self.interpretation,
            "confusion": {
                "both_pass": self.both_pass,
                "both_fail": self.both_fail,
                "judge_lenient": self.judge_pass_human_fail,
                "judge_strict": self.judge_fail_human_pass,
            },
            "missing_labels": len(self.missing_labels),
            "stale_labels": len(self.stale_labels),
        }


def interpret_kappa(kappa: float) -> str:
    if kappa < 0:
        return "poor (worse than chance)"
    if kappa <= 0.20:
        return "slight"
    if kappa <= 0.40:
        return "fair"
    if kappa <= 0.60:
        return "moderate"
    if kappa <= 0.80:
        return "substantial"
    return "almost perfect"


def cohens_kappa(both_pass: int, both_fail: int, lenient: int, strict: int) -> float:
    """Cohen's kappa for two binary raters."""
    n = both_pass + both_fail + lenient + strict
    if n == 0:
        return 0.0

    observed = (both_pass + both_fail) / n
    judge_pass_rate = (both_pass + lenient) / n
    human_pass_rate = (both_pass + strict) / n
    expected = judge_pass_rate * human_pass_rate + (1 - judge_pass_rate) * (1 - human_pass_rate)

    if expected >= 1.0:
        # Both raters were unanimous on everything; kappa is undefined, and
        # reporting 0 would be as wrong as reporting 1.
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def compute_agreement(
    judge_results: dict[str, bool],
    human_labels: list[HumanLabel],
    answer_shas: dict[str, str] | None = None,
) -> AgreementReport:
    """Compare judge pass/fail against human pass/fail, per item.

    `answer_shas` maps item id to the digest of the answer the judge graded.
    When given, a label made against a different answer is stale and skipped.
    """
    report = AgreementReport()

    for label in human_labels:
        if label.item_id not in judge_results:
            # Includes rule-decided verdicts, which the caller leaves out.
            report.missing_labels.append(label.item_id)
            continue
        if (
            answer_shas is not None
            and label.answer_sha is not None
            and answer_shas.get(label.item_id) != label.answer_sha
        ):
            report.stale_labels.append(label.item_id)
            continue
        judge_pass = judge_results[label.item_id]
        if judge_pass and label.correct:
            report.both_pass += 1
        elif not judge_pass and not label.correct:
            report.both_fail += 1
        elif judge_pass and not label.correct:
            report.judge_pass_human_fail += 1
        else:
            report.judge_fail_human_pass += 1

    report.n = (
        report.both_pass
        + report.both_fail
        + report.judge_pass_human_fail
        + report.judge_fail_human_pass
    )
    if report.n:
        report.accuracy = (report.both_pass + report.both_fail) / report.n
        report.kappa = cohens_kappa(
            report.both_pass,
            report.both_fail,
            report.judge_pass_human_fail,
            report.judge_fail_human_pass,
        )
        report.interpretation = interpret_kappa(report.kappa)
    return report


def load_labels(path: str | Path = DEFAULT_LABELS) -> list[HumanLabel]:
    resolved = Path(path)
    if not resolved.exists():
        return []
    labels: list[HumanLabel] = []
    for line in resolved.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        data = json.loads(stripped)
        labels.append(
            HumanLabel(
                item_id=data["item_id"],
                correct=bool(data["correct"]),
                faithful=data.get("faithful"),
                notes=data.get("notes", ""),
                answer_sha=data.get("answer_sha"),
                experiment=data.get("experiment"),
            )
        )
    return labels


def save_labels(labels: list[HumanLabel], path: str | Path = DEFAULT_LABELS) -> Path:
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as fh:
        for label in labels:
            fh.write(json.dumps(label.to_dict(), ensure_ascii=False) + "\n")
    return resolved

"""LLM-as-judge for answer correctness, plus the machinery to validate it.

An unvalidated judge is an opinion, not a metric. So this module ships with
`agreement.py` next to it, and the README reports judge-human agreement
(accuracy and Cohen's kappa) alongside any number the judge produced. If the
judge disagrees with a human as often as it agrees, every generation metric in
the repo is noise, and a reader deserves to know that.

Three rules keep the judge honest:

* **It never sees the retrieved context.** It compares the answer to the
  reference. A judge shown the context starts grading whether the answer is
  well-supported, which is what `verify.py` measures separately.
* **An abstention is correct only if the reference abstains.** Otherwise
  "I don't have enough information" would be a safe way to score well on
  everything.
* **It never sees citation markers.** They are the verifier's business, and
  to a model that has not been told what they are, `field[1][2]` reads as
  array indexing.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.generation.answer import is_abstention
from app.generation.prompts import render_judge_prompt
from app.generation.verify import normalize_citations

log = get_logger(__name__)


@dataclass
class JudgeVerdict:
    score: int  # 1-5
    passed: bool
    reason: str = ""
    cost_usd: float = 0.0
    cached: bool = False
    # True when the verdict came from a rule rather than the model.
    deterministic: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_MARKERS = re.compile(r"\s*\[\d+\]")


def strip_citations(answer: str) -> str:
    """The answer as the judge should see it: substance, without `[n]` markers.

    Citations are checked by the verifier, against the excerpts; the judge
    compares substance with the reference and is never shown the excerpts, so
    the markers mean nothing to it. Worse, they can read as content: given
    `.spec.revisionHistoryLimit[1][2]`, the judge docked a correct answer for
    "incorrect indices".
    """
    return _MARKERS.sub("", normalize_citations(answer)).strip()


def _parse(text: str) -> tuple[int, bool, str] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    try:
        score = int(parsed.get("score", 0))
    except (TypeError, ValueError):
        return None
    if not 1 <= score <= 5:
        return None

    # Trust the score over the flag: a model that says {"score": 5,
    # "pass": false} has contradicted itself, and the scale is better defined.
    passed = bool(parsed.get("pass", score >= 4))
    if score >= 4 and not passed:
        passed = True
    if score <= 3 and passed:
        passed = False
    return score, passed, str(parsed.get("reason", ""))[:300]


def judge_answer(
    client: LLMClient,
    *,
    question: str,
    reference_answer: str,
    answer: str,
    reference_is_abstention: bool = False,
    model: str | None = None,
) -> JudgeVerdict:
    """Grade an answer against its reference."""
    answer_abstains = is_abstention(answer)

    # Abstention is decided by rule, not by the model: it is an exact string
    # comparison, and spending a call on it would add cost and variance.
    if answer_abstains or reference_is_abstention:
        correct = answer_abstains and reference_is_abstention
        return JudgeVerdict(
            score=5 if correct else 1,
            passed=correct,
            reason=(
                "correctly abstained"
                if correct
                else (
                    "abstained on an answerable question"
                    if answer_abstains
                    else "answered an unanswerable question"
                )
            ),
            deterministic=True,
        )

    if not answer.strip():
        return JudgeVerdict(1, False, "empty answer", deterministic=True)

    system, user = render_judge_prompt(question, reference_answer, strip_citations(answer))
    try:
        response = client.complete(
            user, system=system, model=model or client.cheap_model, max_tokens=256
        )
    except Exception as exc:  # noqa: BLE001 - one failed judgement must not end a run
        log.warning("judge.call_failed", error=str(exc))
        return JudgeVerdict(0, False, f"judge call failed: {type(exc).__name__}")

    parsed = _parse(response.text)
    if parsed is None:
        log.warning("judge.unparseable", text=response.text[:200])
        return JudgeVerdict(0, False, "unparseable judge response", response.cost_usd)

    score, passed, reason = parsed
    return JudgeVerdict(score, passed, reason, response.cost_usd, response.cached)

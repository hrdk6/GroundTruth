"""Claim verification: does each cited excerpt actually support its sentence?

This is the answer to "hallucinated citations". A model that cites `[2]` for a
sentence `[2]` does not support has produced something *worse* than an uncited
guess, because the citation makes it look checked.

How it works:

1. Split the answer into sentences, keeping each sentence's citation markers.
2. Drop sentences that assert nothing (hedges, transitions, the abstention).
3. For each remaining sentence, ask a cheap model whether the cited excerpts
   support it.
4. **A factual sentence with no citation counts as unsupported.** Without that
   rule the easiest way to score well would be to stop citing.

The output is a support fraction, which the policy in `answer.py` turns into
"return", "regenerate once", or "abstain".
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Literal

from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.generation.prompts import ABSTAIN_MESSAGE, render_verification_prompt
from app.retrieval.base import Candidate

log = get_logger(__name__)

Verdict = Literal["supported", "partially", "unsupported"]

_CITATION = re.compile(r"\[(\d+)\]")
# Split on sentence enders, but not on the dot inside "v1.28", "e.g.", or
# "kube-apiserver.yaml" -- version strings and file names are everywhere here.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(`\[])")

# Sentences that assert nothing about Kubernetes and so cannot be unsupported.
_NON_FACTUAL = re.compile(
    r"^\s*(?:however|in summary|to summarize|note that|additionally|also|"
    r"for example|see also|in short|that said|finally)\b[\s,:]*$",
    re.IGNORECASE,
)
_MIN_FACTUAL_WORDS = 4


@dataclass
class SentenceVerification:
    sentence: str
    citations: list[int]
    verdict: Verdict
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class VerificationReport:
    sentences: list[SentenceVerification] = field(default_factory=list)
    support_fraction: float = 1.0
    cost_usd: float = 0.0
    checked: int = 0
    skipped_non_factual: int = 0

    @property
    def unsupported(self) -> list[SentenceVerification]:
        return [s for s in self.sentences if s.verdict == "unsupported"]

    @property
    def citation_precision(self) -> float | None:
        """Share of *cited* sentences whose citations hold up.

        Distinct from support fraction: this measures citation honesty, so
        uncited sentences are excluded rather than counted as failures.
        """
        cited = [s for s in self.sentences if s.citations]
        if not cited:
            return None
        good = sum(1 for s in cited if s.verdict in ("supported", "partially"))
        return good / len(cited)

    def feedback(self) -> str:
        lines = [f"- {s.sentence}" for s in self.unsupported[:6]]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        return {
            "support_fraction": round(self.support_fraction, 4),
            "citation_precision": (
                round(self.citation_precision, 4) if self.citation_precision is not None else None
            ),
            "checked": self.checked,
            "skipped_non_factual": self.skipped_non_factual,
            "unsupported_count": len(self.unsupported),
            "cost_usd": round(self.cost_usd, 6),
            "sentences": [s.to_dict() for s in self.sentences],
        }


def split_sentences(answer: str) -> list[tuple[str, list[int]]]:
    """Split an answer into `(sentence, cited indices)` pairs."""
    text = " ".join(answer.split())
    if not text:
        return []

    pairs: list[tuple[str, list[int]]] = []
    for raw in _SENTENCE_SPLIT.split(text):
        sentence = raw.strip()
        if not sentence:
            continue
        citations = [int(n) for n in _CITATION.findall(sentence)]
        pairs.append((sentence, citations))
    return pairs


def is_factual(sentence: str) -> bool:
    """Whether a sentence makes a checkable claim."""
    stripped = _CITATION.sub("", sentence).strip()
    if not stripped or _NON_FACTUAL.match(stripped):
        return False
    words = re.findall(r"[A-Za-z0-9_.\-]+", stripped)
    return len(words) >= _MIN_FACTUAL_WORDS


def _parse_verdict(text: str) -> tuple[Verdict, str]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            verdict = str(parsed.get("verdict", "")).lower()
            if verdict in ("supported", "partially", "unsupported"):
                return verdict, str(parsed.get("reason", ""))  # type: ignore[return-value]
        except json.JSONDecodeError:
            pass

    lowered = text.lower()
    for verdict in ("unsupported", "partially", "supported"):
        if verdict in lowered:
            return verdict, "parsed from prose"  # type: ignore[return-value]
    # An unparseable judgement is treated as a failure to verify, not a pass.
    return "unsupported", "could not parse verifier response"


def verify_answer(
    client: LLMClient,
    answer: str,
    candidates: list[Candidate],
    *,
    model: str | None = None,
) -> VerificationReport:
    """Check every factual sentence against the excerpts it cites."""
    report = VerificationReport()

    if not answer.strip() or answer.strip().startswith(ABSTAIN_MESSAGE[:40]):
        # An abstention asserts nothing, so it is vacuously supported.
        return report

    for sentence, citations in split_sentences(answer):
        if not is_factual(sentence):
            report.skipped_non_factual += 1
            continue

        if not citations:
            report.sentences.append(
                SentenceVerification(sentence, [], "unsupported", "no citation")
            )
            continue

        # Citations are 1-based indices into the numbered context.
        excerpts = [candidates[i - 1].text for i in citations if 1 <= i <= len(candidates)]
        if not excerpts:
            report.sentences.append(
                SentenceVerification(
                    sentence, citations, "unsupported", "citation points at no excerpt"
                )
            )
            continue

        claim = _CITATION.sub("", sentence).strip()
        system, user = render_verification_prompt(claim, "\n\n---\n\n".join(excerpts))
        try:
            response = client.complete(
                user, system=system, model=model or client.cheap_model, max_tokens=128
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("verify.call_failed", error=str(exc))
            report.sentences.append(
                SentenceVerification(sentence, citations, "unsupported", "verifier call failed")
            )
            continue

        verdict, reason = _parse_verdict(response.text)
        report.cost_usd += response.cost_usd
        report.sentences.append(SentenceVerification(sentence, citations, verdict, reason))

    report.checked = len(report.sentences)
    if report.checked:
        # "partially" counts as half: it is neither a clean pass nor a
        # hallucination, and collapsing it either way distorts the metric.
        weight = {"supported": 1.0, "partially": 0.5, "unsupported": 0.0}
        report.support_fraction = sum(weight[s.verdict] for s in report.sentences) / report.checked
    return report

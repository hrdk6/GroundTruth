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

The per-sentence checks are independent, so they are issued concurrently
(`GT_LLM_MAX_CONCURRENCY`, default 4). Each is still its own cached call with
its own prompt, so concurrency changes the latency and nothing else: the
verdicts, their order, and the cache keys are exactly what a sequential run
produces.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.generation.prompts import ABSTAIN_MESSAGE, render_verification_prompt
from app.retrieval.base import Candidate

log = get_logger(__name__)

Verdict = Literal["supported", "partially", "unsupported"]

# Citation markers, in every bracket style a model actually emits. Models
# trained on multilingual corpora produce the full-width CJK forms often
# enough that matching only ASCII `[n]` silently scores a correctly-cited
# answer as having no citations at all -- which then reads as a hallucination.
_CITATION_ANY = re.compile(r"[\[【［]\s*(\d+)\s*[\]】］]")  # noqa: RUF001 - the full-width brackets are the point
_CITATION = re.compile(r"\[(\d+)\]")

# A fragment that is nothing but citation markers and punctuation.
_CITATION_ONLY = re.compile(r"^[\s.,;:]*(?:\[\d+\][\s.,;:]*)+$")
# Markers at the start of a fragment: they close the *previous* sentence.
_LEADING_CITATIONS = re.compile(r"^(?:\[\d+\][\s.,;:]*)+")

# Split on sentence enders, but not on the dot inside "v1.28", "e.g.", or
# "kube-apiserver.yaml" -- version strings and file names are everywhere here.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(`\[])")


def normalize_citations(text: str) -> str:
    """Rewrite every citation-marker style to the canonical `[n]`."""
    return _CITATION_ANY.sub(lambda m: f"[{m.group(1)}]", text)


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
    """Split an answer into `(sentence, cited indices)` pairs.

    A citation placed after its full stop -- `...253 characters. [3]` --
    belongs to the sentence it closes. The splitter breaks *before* the `[`,
    so without repair the marker either stood alone (at the end of an answer)
    or, mid-answer, was glued to the front of the *next* sentence. Both
    punished the model for citing correctly: the real claim read as uncited
    and was scored unsupported, and in the mid-answer case the next claim was
    checked against an excerpt that was never meant for it.

    So leading markers are moved back onto the sentence before them, and a
    fragment left with nothing but punctuation is dropped.
    """
    text = " ".join(normalize_citations(answer).split())
    if not text:
        return []

    pairs: list[tuple[str, list[int]]] = []
    for raw in _SENTENCE_SPLIT.split(text):
        sentence = raw.strip()
        if not sentence:
            continue

        leading = _LEADING_CITATIONS.match(sentence)
        if leading and pairs:
            markers = leading.group(0).strip()
            previous, previous_citations = pairs[-1]
            pairs[-1] = (
                f"{previous} {markers}",
                previous_citations + [int(n) for n in _CITATION.findall(markers)],
            )
            sentence = sentence[leading.end() :].strip()
            if not sentence or _CITATION_ONLY.match(sentence) or not sentence.strip(".,;: "):
                continue

        pairs.append((sentence, [int(n) for n in _CITATION.findall(sentence)]))
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


def _check_claim(
    client: LLMClient, check: tuple[str, list[int], list[str]], model: str | None
) -> tuple[SentenceVerification, float]:
    """One model call: does this sentence's cited evidence support it?"""
    sentence, citations, excerpts = check
    claim = _CITATION.sub("", sentence).strip()
    system, user = render_verification_prompt(claim, "\n\n---\n\n".join(excerpts))
    try:
        response = client.complete(
            user, system=system, model=model or client.cheap_model, max_tokens=128
        )
    except Exception as exc:  # noqa: BLE001 - one failed check must not fail the answer
        log.warning("verify.call_failed", error=str(exc))
        return SentenceVerification(sentence, citations, "unsupported", "verifier call failed"), 0.0

    verdict, reason = _parse_verdict(response.text)
    return SentenceVerification(sentence, citations, verdict, reason), response.cost_usd


def verify_answer(
    client: LLMClient,
    answer: str,
    candidates: list[Candidate],
    *,
    model: str | None = None,
    max_concurrency: int | None = None,
) -> VerificationReport:
    """Check every factual sentence against the excerpts it cites."""
    report = VerificationReport()

    if not answer.strip() or answer.strip().startswith(ABSTAIN_MESSAGE[:40]):
        # An abstention asserts nothing, so it is vacuously supported.
        return report

    # Decide what each sentence needs first; only real checks cost a call.
    # `slots` keeps sentence order, holding either a verdict decided by rule
    # or the index of the model check that will fill it.
    slots: list[SentenceVerification | int] = []
    checks: list[tuple[str, list[int], list[str]]] = []

    for sentence, citations in split_sentences(answer):
        if not is_factual(sentence):
            report.skipped_non_factual += 1
            continue

        if not citations:
            slots.append(SentenceVerification(sentence, [], "unsupported", "no citation"))
            continue

        # Citations are 1-based indices into the numbered context.
        excerpts = [candidates[i - 1].text for i in citations if 1 <= i <= len(candidates)]
        if not excerpts:
            slots.append(
                SentenceVerification(
                    sentence, citations, "unsupported", "citation points at no excerpt"
                )
            )
            continue

        slots.append(len(checks))
        checks.append((sentence, citations, excerpts))

    workers = max(1, max_concurrency or client.settings.gt_llm_max_concurrency)
    if len(checks) <= 1 or workers == 1:
        outcomes = [_check_claim(client, check, model) for check in checks]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(checks))) as pool:
            outcomes = list(pool.map(lambda check: _check_claim(client, check, model), checks))

    for slot in slots:
        if isinstance(slot, int):
            verification, cost = outcomes[slot]
            report.cost_usd += cost
            report.sentences.append(verification)
        else:
            report.sentences.append(slot)

    report.checked = len(report.sentences)
    if report.checked:
        # "partially" counts as half: it is neither a clean pass nor a
        # hallucination, and collapsing it either way distorts the metric.
        weight = {"supported": 1.0, "partially": 0.5, "unsupported": 0.0}
        report.support_fraction = sum(weight[s.verdict] for s in report.sentences) / report.checked
    return report


def segment_answer(answer: str, report: VerificationReport | None) -> list[dict[str, Any]]:
    """Every sentence of the answer, with its citations and its verdict.

    Split with the same function the verifier used, so each verdict attaches
    to exactly the sentence it judged. A client splitting the text on its own
    disagrees at the edges -- a trailing `[3]` after the full stop, a
    full-width `【1】` -- and silently drops the verdict for that sentence.

    `verdict` is None for a sentence that was not checked: verification was
    off, or the sentence asserts nothing (`factual` says which).
    """
    verdicts: dict[str, list[SentenceVerification]] = {}
    for verified in report.sentences if report else []:
        verdicts.setdefault(verified.sentence, []).append(verified)

    segments: list[dict[str, Any]] = []
    for sentence, citations in split_sentences(answer):
        queue = verdicts.get(sentence)
        checked = queue.pop(0) if queue else None
        segments.append(
            {
                "text": sentence,
                "citations": citations,
                "factual": is_factual(sentence),
                "verdict": checked.verdict if checked else None,
                "reason": checked.reason if checked else "",
            }
        )
    return segments

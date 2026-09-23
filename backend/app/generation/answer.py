"""Grounded answering: retrieve, generate, verify, then decide what to return.

The policy (PROJECT_SPEC.md S8.3) is the interesting part:

    generate -> verify -> if support < threshold: regenerate once with the
    failing sentences as feedback -> verify again -> if still below: abstain.

Abstaining is a *success* here. An answer that cites excerpts which do not
support it is worse than no answer, because the citations make it look checked.
The alternative -- returning the best available text and letting the reader
sort it out -- is what this project exists to argue against.

Conflict notes are attached rather than merged into the prose: when versions
disagree, the answer states the latest version's behaviour and appends what
changed, each with its own citation, so nothing is silently blended.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import LLMClient, get_llm_client
from app.core.logging import get_logger
from app.core.pipeline import PipelineConfig
from app.generation.prompts import ABSTAIN_MESSAGE, render_grounded_prompt
from app.generation.verify import VerificationReport, verify_answer
from app.retrieval.base import Candidate, RetrievalResult
from app.retrieval.retriever import Retriever
from app.retrieval.versioning import (
    VersionConflict,
    VersionDecision,
    detect_conflicts,
    indexed_versions,
)

log = get_logger(__name__)

_CITATION = re.compile(r"\[(\d+)\]")


@dataclass
class Citation:
    """A numbered excerpt the answer actually referred to."""

    marker: int
    chunk_id: int
    source_path: str
    version: str
    heading_path: str
    title: str
    url: str
    text: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnswerResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    version_used: str | None = None
    version_reason: str = ""
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    abstained: bool = False
    regenerated: bool = False
    retrieval: RetrievalResult | None = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "version_used": self.version_used,
            "version_reason": self.version_reason,
            "conflicts": self.conflicts,
            "verification": self.verification,
            "abstained": self.abstained,
            "regenerated": self.regenerated,
            "cost_usd": round(self.cost_usd, 6),
            "latency_ms": round(self.latency_ms, 2),
            "timings_ms": {k: round(v, 2) for k, v in self.timings_ms.items()},
            "retrieved": (
                [c.to_dict(include_text=include_text) for c in self.retrieval.candidates]
                if self.retrieval
                else []
            ),
        }


def is_abstention(text: str) -> bool:
    """Whether an answer declined to answer.

    Compared against the exact fixed sentence the prompt mandates, with a
    prefix fallback for the occasional trailing clause.
    """
    stripped = text.strip().rstrip(".").lower()
    target = ABSTAIN_MESSAGE.rstrip(".").lower()
    return stripped == target or stripped.startswith(target[:50])


def extract_citations(answer: str, candidates: list[Candidate]) -> list[Citation]:
    """Resolve `[n]` markers to the excerpts they point at, in order of use."""
    citations: list[Citation] = []
    seen: set[int] = set()

    for marker_text in _CITATION.findall(answer):
        marker = int(marker_text)
        if marker in seen or not (1 <= marker <= len(candidates)):
            continue
        seen.add(marker)
        candidate = candidates[marker - 1]
        citations.append(
            Citation(
                marker=marker,
                chunk_id=candidate.chunk_id,
                source_path=candidate.source_path,
                version=candidate.version,
                heading_path=candidate.heading_path,
                title=candidate.title,
                url=candidate.url,
                text=candidate.text,
                score=candidate.score,
            )
        )
    return citations


def format_conflict_note(conflicts: list[VersionConflict]) -> str:
    """Render conflicts as a clearly separated note, never mixed into the answer."""
    if not conflicts:
        return ""

    lines = ["", "", "**Version note:**"]
    for conflict in conflicts[:3]:
        where = conflict.heading_path or conflict.source_path
        lines.append(
            f"- This section ({where}) differs in v{conflict.other_version}. "
            f"The answer above is for v{conflict.latest_version}; "
            f"see `{conflict.source_path}` in v{conflict.other_version} for the older behaviour."
        )
    return "\n".join(lines)


class AnswerService:
    """Full query path: retrieve -> generate -> verify -> decide."""

    def __init__(self, config: PipelineConfig, *, llm_client: LLMClient | None = None) -> None:
        self.config = config
        self.llm = llm_client or get_llm_client()
        self.retriever = Retriever(config, llm_client=self.llm)

    def answer(
        self,
        session: Session,
        question: str,
        *,
        version: str | None = None,
    ) -> AnswerResult:
        import time

        started = time.perf_counter()
        cost_before = self.llm.tracker.cost_usd

        retrieval, decision = self.retriever.retrieve(session, question, version=version)
        result = self._generate_and_verify(session, question, retrieval, decision)

        result.retrieval = retrieval
        result.timings_ms = {**retrieval.timings_ms, **result.timings_ms}
        result.latency_ms = (time.perf_counter() - started) * 1000
        result.cost_usd = self.llm.tracker.cost_usd - cost_before
        return result

    def _generate_and_verify(
        self,
        session: Session,
        question: str,
        retrieval: RetrievalResult,
        decision: VersionDecision,
    ) -> AnswerResult:
        import time

        candidates = retrieval.candidates
        timings: dict[str, float] = {}

        if not candidates:
            return AnswerResult(
                answer=ABSTAIN_MESSAGE,
                version_used=decision.version,
                version_reason=decision.reason,
                abstained=True,
                verification={"support_fraction": 1.0, "checked": 0, "reason": "no excerpts"},
            )

        verification_config = self.config.verification

        started = time.perf_counter()
        answer_text = self._generate(question, candidates, decision.version)
        timings["generation"] = (time.perf_counter() - started) * 1000

        report = VerificationReport()
        regenerated = False

        if verification_config.enabled and not is_abstention(answer_text):
            started = time.perf_counter()
            report = verify_answer(
                self.llm, answer_text, candidates, model=verification_config.model
            )

            if (
                report.support_fraction < verification_config.support_threshold
                and verification_config.max_regenerations > 0
            ):
                log.info(
                    "answer.regenerating",
                    support_fraction=round(report.support_fraction, 3),
                    threshold=verification_config.support_threshold,
                )
                retry = self._generate(
                    question, candidates, decision.version, feedback=report.feedback()
                )
                regenerated = True
                if is_abstention(retry):
                    answer_text, report = retry, VerificationReport()
                else:
                    retry_report = verify_answer(
                        self.llm, retry, candidates, model=verification_config.model
                    )
                    # Keep the retry only if it is actually better.
                    if retry_report.support_fraction >= report.support_fraction:
                        answer_text, report = retry, retry_report

            timings["verification"] = (time.perf_counter() - started) * 1000

        abstained = is_abstention(answer_text)
        if (
            verification_config.enabled
            and not abstained
            and report.checked
            and report.support_fraction < verification_config.support_threshold
        ):
            # Still unsupported after the retry: abstaining is the honest outcome.
            log.info("answer.abstaining", support_fraction=round(report.support_fraction, 3))
            answer_text = ABSTAIN_MESSAGE
            abstained = True

        citations = [] if abstained else extract_citations(answer_text, candidates)

        conflicts: list[VersionConflict] = []
        if (
            self.config.versioning.conflict_detection
            and not abstained
            and not decision.explicit
            and decision.version
        ):
            started = time.perf_counter()
            conflicts = detect_conflicts(
                session,
                candidates,
                chunker_name=self.config.chunker_name,
                answer_version=decision.version,
                all_versions=indexed_versions(session, self.config.chunker_name),
            )
            timings["conflict_detection"] = (time.perf_counter() - started) * 1000
            if conflicts:
                answer_text += format_conflict_note(conflicts)

        return AnswerResult(
            answer=answer_text,
            citations=citations,
            version_used=decision.version,
            version_reason=decision.reason,
            conflicts=[c.to_dict() for c in conflicts],
            verification=report.to_dict() if verification_config.enabled else {},
            abstained=abstained,
            regenerated=regenerated,
            timings_ms=timings,
        )

    def _generate(
        self,
        question: str,
        candidates: list[Candidate],
        version: str | None,
        *,
        feedback: str | None = None,
    ) -> str:
        prompt = render_grounded_prompt(
            question,
            candidates,
            version=version,
            prompt_name=self.config.generation.prompt,
            feedback=feedback,
        )
        response = self.llm.complete(
            prompt.user,
            system=prompt.system,
            model=self.config.generation.model,
            max_tokens=self.config.generation.max_tokens,
            temperature=self.config.generation.temperature,
        )
        return response.text.strip()

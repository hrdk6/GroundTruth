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

Cost is tracked per request, on a child of the shared client. The API answers
several questions at once on worker threads, and a cost read as a delta off one
shared tracker would bill each request for its neighbours' calls.
"""

from __future__ import annotations

import re
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import LLMClient, get_llm_client
from app.core.logging import get_logger
from app.core.pipeline import PipelineConfig
from app.generation.prompts import ABSTAIN_MESSAGE, render_grounded_prompt
from app.generation.verify import (
    VerificationReport,
    normalize_citations,
    segment_answer,
    verify_answer,
)
from app.retrieval.base import Candidate, RetrievalResult
from app.retrieval.retriever import Retriever
from app.retrieval.versioning import (
    VersionConflict,
    VersionDecision,
    detect_conflicts,
    indexed_versions,
)
from app.tracing.tracer import Tracer

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
    # The answer body, with citation markers normalized to `[n]`. The version
    # note is kept apart in `conflict_note`, so a client can render conflicts
    # as structure instead of re-parsing Markdown out of the prose.
    answer: str
    citations: list[Citation] = field(default_factory=list)
    version_used: str | None = None
    version_reason: str = ""
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    conflict_note: str = ""
    verification: dict[str, Any] = field(default_factory=dict)
    # Every sentence of `answer`, with its citations and verdict, split once
    # on the server. A client that re-splits the text itself will disagree
    # with the verifier about where sentences end.
    segments: list[dict[str, Any]] = field(default_factory=list)
    abstained: bool = False
    regenerated: bool = False
    retrieval: RetrievalResult | None = None
    trace_id: str | None = None
    cost_usd: float = 0.0
    total_tokens: int = 0
    latency_ms: float = 0.0
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """The answer as a reader of plain text gets it: body plus version note.

        This is what the judge grades and what a trace stores, so both see the
        conflict note exactly as a user of the text API would.
        """
        return self.answer + self.conflict_note

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "conflict_note": self.conflict_note,
            "segments": self.segments,
            "citations": [c.to_dict() for c in self.citations],
            "version_used": self.version_used,
            "version_reason": self.version_reason,
            "conflicts": self.conflicts,
            "verification": self.verification,
            "abstained": self.abstained,
            "regenerated": self.regenerated,
            "trace_id": self.trace_id,
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
    """Resolve `[n]` markers to the excerpts they point at, in order of use.

    Normalizes bracket style first: a model that writes the full-width CJK
    form would otherwise have every citation dropped on the floor.
    """
    citations: list[Citation] = []
    seen: set[int] = set()

    for marker_text in _CITATION.findall(normalize_citations(answer)):
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
        tracer: Tracer | None = None,
    ) -> AnswerResult:
        started = time.perf_counter()
        # This request's own tracker, rolling up into the shared one.
        llm = self.llm.child()

        retrieval, decision = self.retriever.retrieve(
            session, question, version=version, tracer=tracer, llm_client=llm
        )
        result = self._generate_and_verify(
            session, question, retrieval, decision, llm=llm, tracer=tracer
        )

        result.retrieval = retrieval
        result.timings_ms = {**retrieval.timings_ms, **result.timings_ms}
        result.latency_ms = (time.perf_counter() - started) * 1000
        result.cost_usd = llm.tracker.cost_usd
        result.total_tokens = llm.tracker.total_tokens

        if tracer is not None:
            result.trace_id = tracer.flush(
                session,
                answer=result.full_text,
                abstained=result.abstained,
                version_used=result.version_used,
                latency_ms=result.latency_ms,
                cost_usd=result.cost_usd,
                total_tokens=result.total_tokens,
                meta={
                    "citations": len(result.citations),
                    "conflicts": len(result.conflicts),
                    "regenerated": result.regenerated,
                },
            )
        return result

    def _generate_and_verify(
        self,
        session: Session,
        question: str,
        retrieval: RetrievalResult,
        decision: VersionDecision,
        *,
        llm: LLMClient,
        tracer: Tracer | None = None,
    ) -> AnswerResult:
        candidates = retrieval.candidates
        timings: dict[str, float] = {}

        if not candidates:
            return AnswerResult(
                answer=ABSTAIN_MESSAGE,
                version_used=decision.version,
                version_reason=decision.reason,
                abstained=True,
                verification={"support_fraction": 1.0, "checked": 0, "reason": "no excerpts"},
                segments=segment_answer(ABSTAIN_MESSAGE, None),
            )

        verification_config = self.config.verification

        started = time.perf_counter()
        if tracer is not None:
            with tracer.span("generation", question=question, excerpts=len(candidates)) as span:
                answer_text = self._generate(llm, question, candidates, decision.version)
                span.output = {"answer": answer_text}
                span.attributes["model"] = self.config.generation.model or llm.default_model
        else:
            answer_text = self._generate(llm, question, candidates, decision.version)
        timings["generation"] = (time.perf_counter() - started) * 1000

        report = VerificationReport()
        regenerated = False

        if verification_config.enabled and not is_abstention(answer_text):
            started = time.perf_counter()
            # A span of its own: verification is often the slowest stage (one
            # model call per factual sentence), and a trace without it hides
            # where most of the latency went.
            span_context = tracer.span("verification") if tracer is not None else nullcontext()
            with span_context as span:
                report = verify_answer(
                    llm, answer_text, candidates, model=verification_config.model
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
                        llm, question, candidates, decision.version, feedback=report.feedback()
                    )
                    regenerated = True
                    if is_abstention(retry):
                        answer_text, report = retry, VerificationReport()
                    else:
                        retry_report = verify_answer(
                            llm, retry, candidates, model=verification_config.model
                        )
                        # Keep the retry only if it is actually better.
                        if retry_report.support_fraction >= report.support_fraction:
                            answer_text, report = retry, retry_report

                if span is not None:
                    span.output = {
                        "support_fraction": round(report.support_fraction, 4),
                        "checked": report.checked,
                        "unsupported": len(report.unsupported),
                        "regenerated": regenerated,
                    }
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

        answer_text = normalize_citations(answer_text)
        citations = [] if abstained else extract_citations(answer_text, candidates)

        conflicts: list[VersionConflict] = []
        conflict_note = ""
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
            conflict_note = format_conflict_note(conflicts)

        return AnswerResult(
            answer=answer_text,
            citations=citations,
            version_used=decision.version,
            version_reason=decision.reason,
            conflicts=[c.to_dict() for c in conflicts],
            conflict_note=conflict_note,
            verification=report.to_dict() if verification_config.enabled else {},
            segments=segment_answer(answer_text, report if verification_config.enabled else None),
            abstained=abstained,
            regenerated=regenerated,
            timings_ms=timings,
        )

    def _generate(
        self,
        llm: LLMClient,
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
        response = llm.complete(
            prompt.user,
            system=prompt.system,
            model=self.config.generation.model,
            max_tokens=self.config.generation.max_tokens,
            temperature=self.config.generation.temperature,
        )
        return response.text.strip()

"""Observability: model calls as spans, fallbacks as `degraded`, abstain reasons.

Each test pins a failure that used to be invisible: a call that only succeeded
on its third try, a rewrite that failed and looked like "no change", a verifier
outage that read as an answer full of hallucinations.
"""

from __future__ import annotations

import threading

import pytest

from app.core.llm import LLMCallRecord, LLMClient, ProviderResult
from app.core.pipeline import load_config
from app.generation.answer import AnswerService, citation_markers
from app.generation.prompts import ABSTAIN_MESSAGE
from app.generation.verify import REASON_CALL_FAILED, verify_answer
from app.retrieval.base import Candidate, RetrievalResult
from app.retrieval.rewrite import decompose_query, rewrite_query
from app.retrieval.versioning import VersionDecision
from app.tracing.tracer import LLM_SPAN, NullTracer, Tracer
from evals.runner import ItemResult, summarize_stages
from tests.conftest import FakeProvider, FlakyProvider


def _candidate(chunk_id: int, text: str = "The alpha widget is configured per pod.") -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        document_id=chunk_id,
        text=text,
        heading_path="Widgets",
        source_path="concepts/widgets.md",
        version="1.30",
    )


# ---------------------------------------------------------------------------
# The LLM client's observer hook
# ---------------------------------------------------------------------------
def _recording(client: LLMClient) -> tuple[LLMClient, list[LLMCallRecord]]:
    seen: list[LLMCallRecord] = []
    return client.observed(seen.append), seen


def test_every_call_is_observed_with_its_usage(llm_client: LLMClient) -> None:
    llm_client._client = FakeProvider(text="hello", input_tokens=120, output_tokens=30)
    observed, seen = _recording(llm_client)

    observed.complete("q", model="m", max_tokens=64)

    [call] = seen
    assert (call.model, call.cached, call.attempts) == ("m", False, 1)
    assert (call.input_tokens, call.output_tokens) == (120, 30)
    assert call.max_tokens == 64 and call.prompt_chars == 1
    assert call.text_preview == "hello" and call.error is None


def test_a_cache_hit_is_observed_as_cached_with_no_provider_attempts(
    llm_client: LLMClient,
) -> None:
    llm_client._client = FakeProvider()
    observed, seen = _recording(llm_client)

    observed.complete("q", model="m")
    observed.complete("q", model="m")

    assert [c.cached for c in seen] == [False, True]
    assert seen[1].attempts == 0 and seen[1].cost_usd == 0.0


def test_retries_are_counted(llm_client: LLMClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A call that succeeded on its third try has latency nothing else explains."""
    monkeypatch.setattr("app.core.llm.time.sleep", lambda _: None)
    llm_client._client = FlakyProvider(fail_times=2)
    observed, seen = _recording(llm_client)

    observed.complete("q", model="m")

    assert seen[0].attempts == 3


def test_a_failed_call_is_observed_and_still_raises(llm_client: LLMClient) -> None:
    llm_client._client = FakeProvider(error=ValueError("bad model id"))
    observed, seen = _recording(llm_client)

    with pytest.raises(ValueError, match="bad model id"):
        observed.complete("q", model="m")

    assert seen[0].error is not None and "bad model id" in seen[0].error
    assert seen[0].attempts == 1


def test_a_broken_observer_never_breaks_the_call(llm_client: LLMClient) -> None:
    def explode(_: LLMCallRecord) -> None:
        raise RuntimeError("observer bug")

    llm_client._client = FakeProvider(text="still answered")
    assert llm_client.observed(explode).complete("q", model="m").text == "still answered"


def test_observing_twice_records_each_call_once(llm_client: LLMClient) -> None:
    """The retriever observes a client the answer service already observed."""
    tracer = Tracer("q")
    llm_client._client = FakeProvider()
    once = llm_client.observed(tracer.record_llm_call)
    twice = once.observed(tracer.record_llm_call)
    assert twice is once

    twice.child().complete("q", model="m")  # a child keeps the observer
    assert [s.name for s in tracer.spans] == [LLM_SPAN]


def test_a_truncated_response_is_flagged() -> None:
    """A reasoning model that spends the budget thinking returns a fragment."""
    tracer = Tracer("q")
    from datetime import UTC, datetime

    tracer.record_llm_call(
        LLMCallRecord(
            model="m",
            provider="openai",
            started_at=datetime.now(UTC),
            latency_ms=5.0,
            cached=False,
            prompt_chars=10,
            max_tokens=128,
            stop_reason="length",
            text_preview="",
        )
    )
    attributes = tracer.spans[0].attributes
    assert attributes["truncated"] is True and attributes["empty"] is True


# ---------------------------------------------------------------------------
# The tracer
# ---------------------------------------------------------------------------
def test_model_calls_nest_under_their_stage_and_roll_up(llm_client: LLMClient) -> None:
    tracer = Tracer("q")
    llm_client._client = FakeProvider(input_tokens=100, output_tokens=40)
    client = llm_client.observed(tracer.record_llm_call)

    with tracer.span("verification") as stage:
        client.complete("a", model="m")
        client.complete("b", model="m")
        client.complete("a", model="m")  # cached

    calls = [s for s in tracer.spans if s.name == LLM_SPAN]
    assert len(calls) == 3 and {c.parent_span_id for c in calls} == {stage.id}
    assert stage.attributes["llm_calls"] == 3
    assert stage.attributes["llm_cached"] == 1
    assert stage.attributes["input_tokens"] == 300  # a cache hit replays its usage
    assert stage.attributes["llm_errors"] == 0


def test_calls_from_worker_threads_are_all_recorded() -> None:
    """Verification fans out to a pool; the stack belongs to the query thread."""
    from datetime import UTC, datetime

    tracer = Tracer("q")
    record = LLMCallRecord(
        model="m",
        provider="fake",
        started_at=datetime.now(UTC),
        latency_ms=1.0,
        cached=False,
        prompt_chars=1,
        max_tokens=1,
        text_preview="ok",
    )
    with tracer.span("verification") as stage:
        threads = [
            threading.Thread(target=lambda: [tracer.record_llm_call(record) for _ in range(50)])
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    calls = [s for s in tracer.spans if s.name == LLM_SPAN]
    assert len(calls) == 400
    assert len({c.sequence for c in tracer.spans}) == len(tracer.spans), "sequences are unique"
    assert stage.attributes["llm_calls"] == 400


def test_stage_breakdown_sums_repeated_stages_and_keeps_the_worst_status() -> None:
    tracer = NullTracer("q")
    with tracer.span("query_rewrite"):
        pass
    with tracer.span("query_rewrite") as second:
        second.status = "degraded"
    with tracer.span("dense"):
        pass

    breakdown = tracer.stage_breakdown()
    assert breakdown["query_rewrite"]["runs"] == 2
    assert breakdown["query_rewrite"]["status"] == "degraded"
    assert breakdown["dense"]["status"] == "ok"
    assert tracer.degraded_stages() == ["query_rewrite"]


def test_eval_records_summarize_stages_across_items() -> None:
    def item(ms: float, status: str = "ok") -> ItemResult:
        result = ItemResult(item_id="x", category="factual", question="q", answerable=True)
        result.stages = {"dense": {"ms": ms, "llm_calls": 0, "status": status}}
        return result

    summary = summarize_stages([item(10.0), item(30.0, "degraded"), item(20.0)])["dense"]
    assert summary["items"] == 3
    assert summary["p50_ms"] == 20.0
    assert summary["degraded_items"] == 1


# ---------------------------------------------------------------------------
# Fallbacks are recorded, not just survived
# ---------------------------------------------------------------------------
def test_a_failed_rewrite_says_so(llm_client: LLMClient) -> None:
    llm_client._client = FakeProvider(error=ValueError("model gone"))
    result = rewrite_query(llm_client, "what is an HPA?", model="m")
    assert result.rewritten == "what is an HPA?"
    assert result.fallback_reason == "call failed: ValueError"


def test_an_empty_rewrite_says_so(llm_client: LLMClient) -> None:
    llm_client._client = FakeProvider(text="   ")
    assert rewrite_query(llm_client, "q?", model="m").fallback_reason == "empty response"


def test_an_unchanged_rewrite_is_not_a_fallback(llm_client: LLMClient) -> None:
    llm_client._client = FakeProvider(text="q?")
    result = rewrite_query(llm_client, "q?", model="m")
    assert result.changed is False and result.fallback_reason is None


def test_unparseable_decomposition_is_not_reported_as_single_hop(llm_client: LLMClient) -> None:
    llm_client._client = FakeProvider(text="I think this needs two searches.")
    result = decompose_query(llm_client, "q?", model="m")
    assert result.multi_hop is False
    assert result.fallback_reason == "unparseable response"


def test_a_single_hop_answer_is_not_a_fallback(llm_client: LLMClient) -> None:
    llm_client._client = FakeProvider(text='{"multi_hop": false, "subqueries": []}')
    assert decompose_query(llm_client, "q?", model="m").fallback_reason is None


# ---------------------------------------------------------------------------
# Verification failures and abstain reasons
# ---------------------------------------------------------------------------
class _Answers:
    """Generation returns `answer`; verification returns `verdict`, or raises."""

    def __init__(self, answer: str, verdict: str | None) -> None:
        self.answer = answer
        self.verdict = verdict

    def invoke(self, **kwargs: object) -> ProviderResult:
        if "Claim:" in str(kwargs["prompt"]):
            if self.verdict is None:
                raise RuntimeError("verifier is down")
            return ProviderResult(text=f'{{"verdict": "{self.verdict}"}}')
        return ProviderResult(text=self.answer, input_tokens=10, output_tokens=5)


def _answer(llm_client: LLMClient, candidates: list[Candidate], tracer: Tracer | None = None):  # type: ignore[no-untyped-def]
    service = AnswerService(load_config("full"), llm_client=llm_client)
    return service._generate_and_verify(
        None,  # type: ignore[arg-type]
        "How is the widget configured?",
        RetrievalResult(candidates=candidates, query="q"),
        VersionDecision("1.30", explicit=True),
        llm=llm_client,
        tracer=tracer,
    )


def test_verifier_failures_are_counted_apart_from_unsupported_verdicts(
    llm_client: LLMClient,
) -> None:
    llm_client._client = _Answers("", verdict=None)
    report = verify_answer(
        llm_client, "The alpha widget is configured per pod [1].", [_candidate(1)], model="m"
    )
    assert report.sentences[0].reason == REASON_CALL_FAILED
    assert report.support_fraction == 0.0
    assert report.verifier_errors == 1
    assert report.support_if_errors_passed == 1.0


@pytest.mark.parametrize(
    ("answer", "verdict", "candidates", "reason"),
    [
        ("anything", "supported", [], "no_excerpts"),
        (ABSTAIN_MESSAGE, "supported", [1], "model_declined"),
        ("The alpha widget is configured per pod [1].", "unsupported", [1], "unsupported"),
        # The verifier never answered: the answer may be fine, nobody checked.
        ("The alpha widget is configured per pod [1].", None, [1], "unverifiable"),
        ("The alpha widget is configured per pod [1].", "supported", [1], None),
    ],
)
def test_every_abstention_records_why(
    llm_client: LLMClient,
    answer: str,
    verdict: str | None,
    candidates: list[int],
    reason: str | None,
) -> None:
    llm_client._client = _Answers(answer, verdict)
    result = _answer(llm_client, [_candidate(i) for i in candidates])
    assert result.abstain_reason == reason
    assert result.abstained is (reason is not None)


def test_a_verifier_outage_marks_verification_degraded(llm_client: LLMClient) -> None:
    llm_client._client = _Answers("The alpha widget is configured per pod [1].", verdict=None)
    tracer = Tracer("q")
    _answer(llm_client, [_candidate(1)], tracer=tracer)

    verification = [s for s in tracer.spans if s.name == "verification"]
    assert verification and all(s.status == "degraded" for s in verification)
    assert verification[0].output["verifier_errors"] == 1
    assert "verification" in tracer.degraded_stages()


def test_an_invented_citation_number_is_visible_in_the_generation_span(
    llm_client: LLMClient,
) -> None:
    """`extract_citations` drops `[7]` of six excerpts silently; the trace must not."""
    llm_client._client = _Answers("The alpha widget is configured per pod [1] [7].", "supported")
    tracer = Tracer("q")
    _answer(llm_client, [_candidate(1)], tracer=tracer)

    generation = next(s for s in tracer.spans if s.name == "generation")
    assert generation.attributes["markers"] == [1, 7]
    assert generation.attributes["out_of_range"] == [7]
    assert generation.attributes["declined"] is False


def test_citation_markers_normalize_full_width_brackets() -> None:
    assert citation_markers("A 【2】 and [3].", excerpts=2) == {
        "markers": [2, 3],
        "out_of_range": [3],
    }

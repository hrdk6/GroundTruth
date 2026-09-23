"""Prompts, kept in one place and versioned by name.

`GenerationConfig.prompt` names an entry in `PROMPTS`, so changing a prompt is a
config change with a new name rather than an edit to a string somewhere -- which
matters because the LLM cache keys on prompt text. Editing a prompt in place
while keeping its name would leave earlier cached responses looking current.

Design notes on the grounded prompt:

* Context chunks are numbered `[1]..[n]` with version and source visible, so the
  model can cite and so a reader can check the citation.
* The abstention sentence is fixed and exact, because abstention is *measured*:
  a fuzzy "I'm not sure" would have to be detected with a classifier instead of
  a string comparison.
* The model is told the version it is answering for. Without that, it happily
  blends releases, which is the failure this project exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.base import Candidate

ABSTAIN_MESSAGE = "I don't have enough information in the documentation to answer that."

GROUNDED_SYSTEM_V1 = f"""You answer questions about Kubernetes using only the documentation excerpts provided.

Rules:
1. Use ONLY the numbered excerpts. Never use prior knowledge about Kubernetes, even if you are confident it is correct.
2. Cite an excerpt for every factual sentence, inline, like [1] or [2][3]. A sentence with no citation will be treated as unsupported.
3. If the excerpts do not contain the answer, reply with exactly this sentence and nothing else:
{ABSTAIN_MESSAGE}
4. Answer for the documentation version stated below. Do not mix information from other versions.
5. Prefer the exact field names, flags, and values as they appear in the excerpts. Do not paraphrase an API field name.
6. Be concise. Do not restate the question or add a preamble."""

VERIFY_SYSTEM = """You check whether a documentation excerpt supports a specific claim.

Respond with strict JSON and nothing else:
{"verdict": "supported"} or {"verdict": "partially"} or {"verdict": "unsupported"}

Definitions:
- "supported": the excerpt states the claim, or states it in equivalent words.
- "partially": the excerpt is about the claim and agrees, but is missing a specific detail the claim asserts (a number, a field name, a condition).
- "unsupported": the excerpt does not state the claim, contradicts it, or is about something else.

Judge only against the excerpt. Your own knowledge of Kubernetes is irrelevant here."""

JUDGE_SYSTEM = """You grade an answer to a Kubernetes documentation question against a reference answer.

Respond with strict JSON and nothing else:
{"score": 1-5, "pass": true/false, "reason": "one short sentence"}

Scale:
5 - fully correct and complete; matches the reference on every substantive point.
4 - correct, but missing a minor detail present in the reference.
3 - partially correct; a substantive point is missing or vague.
2 - mostly incorrect; one small element is right.
1 - incorrect, contradicts the reference, or answers a different question.

"pass" is true for 4 and 5.

Grade on substance, not on wording, length, or formatting. An answer that reaches the reference's conclusion by different words is correct. An abstention ("not enough information") is correct ONLY if the reference is also an abstention."""


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str
    numbered_sources: list[Candidate]


def format_context(candidates: list[Candidate], *, max_chars_per_chunk: int = 4000) -> str:
    """Number the excerpts and show where each came from."""
    blocks: list[str] = []
    for position, candidate in enumerate(candidates, start=1):
        text = candidate.text
        if len(text) > max_chars_per_chunk:
            text = text[:max_chars_per_chunk] + "\n[... excerpt truncated ...]"
        header = f"[{position}] source: {candidate.source_path} | version: {candidate.version}"
        if candidate.heading_path:
            header += f" | section: {candidate.heading_path}"
        blocks.append(f"{header}\n{text}")
    return "\n\n---\n\n".join(blocks)


def render_grounded_prompt(
    question: str,
    candidates: list[Candidate],
    *,
    version: str | None,
    prompt_name: str = "grounded_v1",
    feedback: str | None = None,
) -> RenderedPrompt:
    """Build the grounded-answer prompt.

    `feedback` is set on the one permitted regeneration, carrying the specific
    sentences that failed verification so the retry has something to act on.
    """
    if prompt_name not in PROMPTS:
        raise ValueError(f"Unknown prompt {prompt_name!r}; available: {sorted(PROMPTS)}")

    system = PROMPTS[prompt_name]
    context = format_context(candidates) if candidates else "(no excerpts were retrieved)"
    version_line = (
        f"Documentation version: {version}" if version else "Documentation version: unspecified"
    )

    parts = [version_line, "", "Excerpts:", context, "", f"Question: {question}"]
    if feedback:
        parts += [
            "",
            "Your previous answer had unsupported statements:",
            feedback,
            "",
            "Rewrite the answer. Keep only what the excerpts support, cite every factual "
            f"sentence, and if the excerpts genuinely do not answer the question, reply "
            f"with exactly: {ABSTAIN_MESSAGE}",
        ]
    parts += ["", "Answer:"]

    return RenderedPrompt(system=system, user="\n".join(parts), numbered_sources=list(candidates))


def render_verification_prompt(claim: str, excerpt: str) -> tuple[str, str]:
    user = f"Excerpt:\n{excerpt[:4000]}\n\nClaim: {claim}\n\nJSON:"
    return VERIFY_SYSTEM, user


def render_judge_prompt(question: str, reference: str, answer: str) -> tuple[str, str]:
    user = (
        f"Question: {question}\n\n"
        f"Reference answer:\n{reference}\n\n"
        f"Answer to grade:\n{answer}\n\n"
        "JSON:"
    )
    return JUDGE_SYSTEM, user


PROMPTS: dict[str, str] = {
    "grounded_v1": GROUNDED_SYSTEM_V1,
}

"""Query rewriting and multi-hop decomposition. Both optional, both cheap-model.

These are the two stages that spend an API call before retrieval, so each one
has to earn its latency and cost in a Phase 3 experiment rather than being
switched on because it sounds sophisticated.

**Rewriting** expands abbreviations and adds likely exact terms ("HPA" ->
"HorizontalPodAutoscaler"), which helps dense retrieval and helps lexical search
a great deal. The original query is kept for the lexical leg by default: a
rewrite can drop the exact token the user typed, and that token is often the
only thing lexical search had to work with.

**Decomposition** splits a genuinely multi-part question into sub-queries. The
classifier is deliberately conservative -- decomposing a single-hop question
wastes calls and dilutes the candidate set with off-topic chunks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.core.llm import LLMClient
from app.core.logging import get_logger

log = get_logger(__name__)

REWRITE_SYSTEM = """You rewrite user questions to improve search over Kubernetes documentation.

Rules:
- Expand abbreviations and acronyms to their full API names (HPA -> HorizontalPodAutoscaler, PVC -> PersistentVolumeClaim, SA -> ServiceAccount).
- Add the exact field names, flags, kubectl subcommands, or error strings the answer would contain.
- Preserve every specific term the user typed; never drop one.
- Do not answer the question, explain, or add commentary.
- Return only the rewritten search query, on a single line."""

DECOMPOSE_SYSTEM = """You decide whether a question about Kubernetes needs multiple searches.

A question needs decomposition only when answering it requires facts from two or more distinct topics that would not appear together on one documentation page.

Return strict JSON, nothing else:
{"multi_hop": false, "subqueries": []}
or
{"multi_hop": true, "subqueries": ["...", "..."]}

Rules:
- At most 3 subqueries.
- Each subquery must be independently searchable.
- Prefer {"multi_hop": false} when uncertain: an unnecessary split makes retrieval worse."""


@dataclass
class RewriteResult:
    original: str
    rewritten: str
    changed: bool
    cost_usd: float = 0.0
    cached: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "original": self.original,
            "rewritten": self.rewritten,
            "changed": self.changed,
            "cost_usd": round(self.cost_usd, 6),
            "cached": self.cached,
        }


@dataclass
class DecompositionResult:
    original: str
    multi_hop: bool
    subqueries: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    cached: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "original": self.original,
            "multi_hop": self.multi_hop,
            "subqueries": self.subqueries,
            "cost_usd": round(self.cost_usd, 6),
            "cached": self.cached,
        }


def rewrite_query(client: LLMClient, question: str, *, model: str | None = None) -> RewriteResult:
    """Expand a question into a better search query. Failure returns the original."""
    try:
        response = client.complete(
            f"Question: {question}\n\nRewritten search query:",
            system=REWRITE_SYSTEM,
            model=model or client.cheap_model,
            max_tokens=256,
        )
    except Exception as exc:  # noqa: BLE001 - a rewrite failure must not fail the query
        log.warning("rewrite.failed", error=str(exc))
        return RewriteResult(question, question, changed=False)

    rewritten = response.text.strip().splitlines()[0].strip() if response.text.strip() else ""
    # A rewrite that returns nothing, or an essay, is worse than no rewrite.
    if not rewritten or len(rewritten) > 4 * len(question) + 200:
        return RewriteResult(question, question, False, response.cost_usd, response.cached)

    return RewriteResult(
        original=question,
        rewritten=rewritten,
        changed=rewritten.lower() != question.lower(),
        cost_usd=response.cost_usd,
        cached=response.cached,
    )


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model response."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def decompose_query(
    client: LLMClient, question: str, *, model: str | None = None, max_subqueries: int = 3
) -> DecompositionResult:
    """Split a multi-part question. Any failure degrades to single-hop."""
    try:
        response = client.complete(
            f"Question: {question}\n\nJSON:",
            system=DECOMPOSE_SYSTEM,
            model=model or client.cheap_model,
            max_tokens=512,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("decompose.failed", error=str(exc))
        return DecompositionResult(question, multi_hop=False)

    parsed = _extract_json(response.text)
    if not parsed or not parsed.get("multi_hop"):
        return DecompositionResult(question, False, [], response.cost_usd, response.cached)

    raw = parsed.get("subqueries") or []
    subqueries = [
        str(s).strip() for s in raw if isinstance(s, (str, int, float)) and str(s).strip()
    ]
    subqueries = subqueries[:max_subqueries]

    if len(subqueries) < 2:
        # "Multi-hop" with fewer than two parts is a classifier error.
        return DecompositionResult(question, False, [], response.cost_usd, response.cached)

    return DecompositionResult(
        original=question,
        multi_hop=True,
        subqueries=subqueries,
        cost_usd=response.cost_usd,
        cached=response.cached,
    )

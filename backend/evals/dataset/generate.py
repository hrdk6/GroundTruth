"""Golden set generation.

One generator per category, because the categories genuinely need different
strategies -- an LLM asked for "a hard Kubernetes question" produces bland
factual questions and nothing that stresses exact-term retrieval or version
awareness.

The `version_sensitive` generator is the interesting one and the reason the
corpus has three release branches: it **diffs the same file across versions**
and only generates a question when the text actually changed. The answer is
then known to differ by version, which is exactly what makes the item a test of
version awareness rather than of general retrieval.

Everything generated here is `curated: false`. Only items a human has accepted
in `curate.py` count toward a reported result (PROJECT_SPEC.md §9.1) -- an
LLM-written question with an LLM-written answer is a draft, not ground truth.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.ingestion.parse import ParsedDocument, parse_file
from evals.dataset.schema import Category, GoldenItem, GoldEvidence

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
FACTUAL_SYSTEM = """You write evaluation questions for a Kubernetes documentation search system.

Given an excerpt, write ONE question that the excerpt fully answers.

Return strict JSON, nothing else:
{"question": "...", "answer": "...", "quote": "..."}

- "question": natural, specific, and answerable ONLY from this excerpt. Never mention "the excerpt" or "the documentation".
- "answer": a complete answer in one or two sentences.
- "quote": the exact substring of the excerpt (10-25 words, copied verbatim) that contains the answer.

If the excerpt is navigational, a stub, or has no substantive content, return {"skip": true}."""

EXACT_TERM_SYSTEM = """You write evaluation questions that test EXACT-TERM retrieval over Kubernetes documentation.

Given an excerpt, write ONE question centred on a specific literal string in it: an API field name, a command-line flag, a kubectl subcommand, an error message, an annotation key, or a default value.

Return strict JSON, nothing else:
{"question": "...", "answer": "...", "quote": "...", "term": "..."}

- "term": the exact literal string the question turns on (e.g. "terminationGracePeriodSeconds", "--dry-run=client").
- "question": must require knowing that exact term; it should be hard to answer by paraphrase.
- "quote": verbatim substring of the excerpt, 10-25 words, containing the answer.

If the excerpt contains no such literal term, return {"skip": true}."""

TABLE_CODE_SYSTEM = """You write evaluation questions whose answer lives in a table or a code/YAML block.

Given an excerpt containing a table or code block, write ONE question that can only be answered by reading that table or block.

Return strict JSON, nothing else:
{"question": "...", "answer": "...", "quote": "..."}

- "quote": verbatim substring from inside the table or code block, 10-25 words.
- The question must require a specific value, field, or row -- not the general topic.

If the excerpt has no table or code block with substantive content, return {"skip": true}."""

VERSION_SYSTEM = """You write evaluation questions about how Kubernetes documentation CHANGED between two versions.

You are given the same documentation section in an older and a newer version, and the diff between them.

Return strict JSON, nothing else:
{"question": "...", "old_answer": "...", "new_answer": "...", "old_quote": "...", "new_quote": "..."}

- "question": asks about the behaviour, WITHOUT mentioning a version number. It must have a different correct answer in each version.
- "old_answer"/"new_answer": the answer for each version, one or two sentences.
- "old_quote"/"new_quote": verbatim substrings (10-25 words) from the respective versions.

Only generate a question if the change is SUBSTANTIVE -- a changed default, a renamed field, a new requirement, removed behaviour. Return {"skip": true} for typo fixes, rewording, link changes, or formatting."""

MULTI_HOP_SYSTEM = """You write evaluation questions that require reading TWO different Kubernetes documentation pages.

You are given excerpts from two related pages.

Return strict JSON, nothing else:
{"question": "...", "answer": "...", "quote_a": "...", "quote_b": "..."}

- "question": cannot be answered from either excerpt alone; it genuinely needs a fact from each.
- "quote_a"/"quote_b": verbatim substrings (10-25 words) from excerpt A and excerpt B respectively.

If the two excerpts are unrelated, or one alone answers the question, return {"skip": true}."""

UNANSWERABLE_SYSTEM = """You write evaluation questions that a Kubernetes documentation search system should REFUSE to answer.

Given an excerpt for topical flavour, write ONE question that sounds like a reasonable Kubernetes question but is NOT answerable from the Kubernetes documentation.

Return strict JSON, nothing else:
{"question": "...", "reason": "..."}

Good kinds of unanswerable question:
- specific operational advice the docs do not give ("what CPU limit should I set for a Java service under 2000 rps")
- vendor- or product-specific questions outside kubernetes.io
- questions about versions that do not exist yet
- questions about internal implementation the docs do not describe
- pricing, benchmarks, or comparisons with other products

The question must be plausible and on-topic. Do NOT write nonsense or trick questions."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    else:
        brace = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not brace:
            return None
        stripped = brace.group(0)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _item_id(category: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:10]
    return f"{category}-{digest}"


def _quote_is_present(quote: str, text: str) -> bool:
    """Reject a hallucinated quote before it becomes unverifiable gold."""
    from evals.dataset.schema import normalize_quote

    return bool(quote) and normalize_quote(quote) in normalize_quote(text)


@dataclass
class Section:
    """A slice of a document big enough to answer a question from."""

    document: ParsedDocument
    heading_path: str
    text: str

    @property
    def has_code(self) -> bool:
        return "```" in self.text

    @property
    def has_table(self) -> bool:
        return bool(re.search(r"^\s*\|.*\|\s*$", self.text, re.MULTILINE))


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def split_sections(document: ParsedDocument, *, min_chars: int = 400) -> list[Section]:
    """Split a parsed document into heading-delimited sections."""
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    in_fence = False

    def path() -> str:
        return " > ".join([document.title, *(h for _, h in stack)])

    current_path = path()

    for line in document.text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            current.append(line)
            continue

        heading = None if in_fence else _HEADING.match(line)
        if heading:
            body = "\n".join(current).strip()
            if len(body) >= min_chars:
                sections.append(Section(document, current_path, body))
            current = []
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
            current_path = path()
        else:
            current.append(line)

    body = "\n".join(current).strip()
    if len(body) >= min_chars:
        sections.append(Section(document, current_path, body))
    return sections


def load_documents(root: Path, version: str, *, limit: int | None = None) -> list[ParsedDocument]:
    paths = sorted(p for p in root.rglob("*.md") if p.is_file())
    if limit:
        paths = paths[:limit]
    documents = []
    for path in paths:
        parsed = parse_file(path, root=root, version=version)
        if parsed is not None:
            documents.append(parsed)
    return documents


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------
def _generate_from_section(
    client: LLMClient,
    section: Section,
    *,
    category: Category,
    system: str,
    model: str | None = None,
) -> GoldenItem | None:
    """Shared path for the single-section generators."""
    user = f"Excerpt from {section.document.source_path} ({section.heading_path}):\n\n{section.text[:6000]}\n\nJSON:"
    try:
        response = client.complete(user, system=system, model=model, max_tokens=700)
    except Exception as exc:  # noqa: BLE001 - one bad section must not stop generation
        log.warning("generate.call_failed", category=category, error=str(exc))
        return None

    parsed = _extract_json(response.text)
    if not parsed or parsed.get("skip") or not parsed.get("question"):
        return None

    quote = str(parsed.get("quote", ""))
    if not _quote_is_present(quote, section.text):
        log.debug("generate.quote_not_in_source", category=category)
        return None

    return GoldenItem(
        id=_item_id(category, section.document.source_path, str(parsed["question"])),
        question=str(parsed["question"]).strip(),
        category=category,
        reference_answer=str(parsed.get("answer", "")).strip(),
        gold_evidence=[
            GoldEvidence(
                source_path=section.document.source_path,
                version=section.document.version,
                heading_path=section.heading_path,
                key_quote=quote,
            )
        ],
        version=section.document.version,
        answerable=True,
        curated=False,
        meta={
            "generator": category,
            "term": parsed.get("term"),
            "cost_usd": round(response.cost_usd, 6),
        },
    )


def generate_factual(
    client: LLMClient, sections: list[Section], *, count: int, model: str | None = None
) -> list[GoldenItem]:
    items: list[GoldenItem] = []
    for section in sections:
        if len(items) >= count:
            break
        item = _generate_from_section(
            client, section, category="factual", system=FACTUAL_SYSTEM, model=model
        )
        if item:
            items.append(item)
    return items


def generate_exact_term(
    client: LLMClient, sections: list[Section], *, count: int, model: str | None = None
) -> list[GoldenItem]:
    # Sections with backticks are the ones that name fields, flags, and commands.
    candidates = [s for s in sections if "`" in s.text or s.has_code]
    items: list[GoldenItem] = []
    for section in candidates:
        if len(items) >= count:
            break
        item = _generate_from_section(
            client, section, category="exact_term", system=EXACT_TERM_SYSTEM, model=model
        )
        if item:
            items.append(item)
    return items


def generate_table_or_code(
    client: LLMClient, sections: list[Section], *, count: int, model: str | None = None
) -> list[GoldenItem]:
    candidates = [s for s in sections if s.has_code or s.has_table]
    items: list[GoldenItem] = []
    for section in candidates:
        if len(items) >= count:
            break
        item = _generate_from_section(
            client, section, category="table_or_code", system=TABLE_CODE_SYSTEM, model=model
        )
        if item:
            items.append(item)
    return items


# --- version-sensitive: the diff-driven generator ---------------------------
@dataclass
class VersionDiff:
    source_path: str
    old_version: str
    new_version: str
    old_text: str
    new_text: str
    similarity: float
    diff: str


def find_version_diffs(
    old_documents: list[ParsedDocument],
    new_documents: list[ParsedDocument],
    *,
    min_similarity: float = 0.5,
    max_similarity: float = 0.98,
    limit: int | None = None,
) -> list[VersionDiff]:
    """Pages that changed materially between two releases.

    The similarity band matters. Above `max_similarity` the change is a typo or
    a link fix and no interesting question exists. Below `min_similarity` the
    page was rewritten wholesale, and the "same section, two versions" framing
    stops being true -- the diff is then noise rather than a changed fact.
    """
    old_by_path = {d.source_path: d for d in old_documents}
    diffs: list[VersionDiff] = []

    for new in new_documents:
        old = old_by_path.get(new.source_path)
        if old is None or old.content_hash == new.content_hash:
            continue

        matcher = difflib.SequenceMatcher(None, old.text, new.text)
        similarity = matcher.quick_ratio()
        if not (min_similarity <= similarity <= max_similarity):
            continue

        unified = "\n".join(
            list(
                difflib.unified_diff(
                    old.text.splitlines(),
                    new.text.splitlines(),
                    fromfile=f"v{old.version}",
                    tofile=f"v{new.version}",
                    lineterm="",
                    n=2,
                )
            )[:200]
        )
        # A diff of only additions with no removals is usually new content
        # rather than changed behaviour; keep it, but the model still filters.
        if not unified.strip():
            continue

        diffs.append(
            VersionDiff(
                source_path=new.source_path,
                old_version=old.version,
                new_version=new.version,
                old_text=old.text,
                new_text=new.text,
                similarity=similarity,
                diff=unified,
            )
        )

    diffs.sort(key=lambda d: d.similarity)  # most-changed first
    return diffs[:limit] if limit else diffs


def generate_version_sensitive(
    client: LLMClient, diffs: list[VersionDiff], *, count: int, model: str | None = None
) -> list[GoldenItem]:
    """One item per materially-changed page, carrying evidence from both versions."""
    items: list[GoldenItem] = []

    for diff in diffs:
        if len(items) >= count:
            break

        user = (
            f"File: {diff.source_path}\n\n"
            f"Diff (v{diff.old_version} -> v{diff.new_version}):\n{diff.diff[:4000]}\n\n"
            f"JSON:"
        )
        try:
            response = client.complete(user, system=VERSION_SYSTEM, model=model, max_tokens=800)
        except Exception as exc:  # noqa: BLE001
            log.warning("generate.version_call_failed", error=str(exc))
            continue

        parsed = _extract_json(response.text)
        if not parsed or parsed.get("skip") or not parsed.get("question"):
            continue

        old_quote = str(parsed.get("old_quote", ""))
        new_quote = str(parsed.get("new_quote", ""))
        if not _quote_is_present(new_quote, diff.new_text):
            continue

        # The item is graded against the NEWER version; the older quote is kept
        # in meta so the conflict-handling path has something to check against.
        items.append(
            GoldenItem(
                id=_item_id("version_sensitive", diff.source_path, str(parsed["question"])),
                question=str(parsed["question"]).strip(),
                category="version_sensitive",
                reference_answer=str(parsed.get("new_answer", "")).strip(),
                gold_evidence=[
                    GoldEvidence(
                        source_path=diff.source_path,
                        version=diff.new_version,
                        heading_path="",
                        key_quote=new_quote,
                    )
                ],
                version=diff.new_version,
                answerable=True,
                curated=False,
                notes=f"Answer differs in v{diff.old_version}.",
                meta={
                    "generator": "version_diff",
                    "old_version": diff.old_version,
                    "old_answer": str(parsed.get("old_answer", "")),
                    "old_quote": old_quote,
                    "old_quote_verified": _quote_is_present(old_quote, diff.old_text),
                    "similarity": round(diff.similarity, 4),
                    "cost_usd": round(response.cost_usd, 6),
                },
            )
        )

    return items


def generate_multi_hop(
    client: LLMClient,
    documents: list[ParsedDocument],
    sections: list[Section],
    *,
    count: int,
    model: str | None = None,
    seed: int = 0,
) -> list[GoldenItem]:
    """Pair pages that link to each other, then ask something needing both."""
    by_path = {d.source_path: d for d in documents}
    sections_by_path: dict[str, list[Section]] = {}
    for section in sections:
        sections_by_path.setdefault(section.document.source_path, []).append(section)

    link_pattern = re.compile(r"\]\(https://kubernetes\.io/docs/([^)#]+)")
    pairs: list[tuple[Section, Section]] = []

    for document in documents:
        for match in link_pattern.finditer(document.text):
            target = match.group(1).strip("/")
            # Map a docs URL back to a repo path, both spellings.
            for candidate in (f"{target}.md", f"{target}/_index.md"):
                other = by_path.get(candidate)
                if other is None or other.source_path == document.source_path:
                    continue
                a = sections_by_path.get(document.source_path)
                b = sections_by_path.get(other.source_path)
                if a and b:
                    pairs.append((a[0], b[0]))
                break

    random.Random(seed).shuffle(pairs)
    items: list[GoldenItem] = []

    for section_a, section_b in pairs:
        if len(items) >= count:
            break

        user = (
            f"Excerpt A from {section_a.document.source_path} ({section_a.heading_path}):\n"
            f"{section_a.text[:3000]}\n\n---\n\n"
            f"Excerpt B from {section_b.document.source_path} ({section_b.heading_path}):\n"
            f"{section_b.text[:3000]}\n\nJSON:"
        )
        try:
            response = client.complete(user, system=MULTI_HOP_SYSTEM, model=model, max_tokens=700)
        except Exception as exc:  # noqa: BLE001
            log.warning("generate.multihop_call_failed", error=str(exc))
            continue

        parsed = _extract_json(response.text)
        if not parsed or parsed.get("skip") or not parsed.get("question"):
            continue

        quote_a = str(parsed.get("quote_a", ""))
        quote_b = str(parsed.get("quote_b", ""))
        # Both quotes must be real, or the item is not actually multi-hop.
        if not (
            _quote_is_present(quote_a, section_a.text)
            and _quote_is_present(quote_b, section_b.text)
        ):
            continue

        items.append(
            GoldenItem(
                id=_item_id("multi_hop", section_a.document.source_path, str(parsed["question"])),
                question=str(parsed["question"]).strip(),
                category="multi_hop",
                reference_answer=str(parsed.get("answer", "")).strip(),
                gold_evidence=[
                    GoldEvidence(
                        source_path=section_a.document.source_path,
                        version=section_a.document.version,
                        heading_path=section_a.heading_path,
                        key_quote=quote_a,
                    ),
                    GoldEvidence(
                        source_path=section_b.document.source_path,
                        version=section_b.document.version,
                        heading_path=section_b.heading_path,
                        key_quote=quote_b,
                    ),
                ],
                version=section_a.document.version,
                answerable=True,
                curated=False,
                meta={"generator": "multi_hop", "cost_usd": round(response.cost_usd, 6)},
            )
        )

    return items


def generate_unanswerable(
    client: LLMClient, sections: list[Section], *, count: int, model: str | None = None
) -> list[GoldenItem]:
    """Plausible questions the documentation does not answer.

    These carry no gold evidence, so they are excluded from retrieval metrics
    and drive abstention precision/recall instead.
    """
    items: list[GoldenItem] = []
    for section in sections:
        if len(items) >= count:
            break

        user = (
            f"Topic excerpt from {section.document.source_path}:\n\n{section.text[:2500]}\n\nJSON:"
        )
        try:
            response = client.complete(
                user, system=UNANSWERABLE_SYSTEM, model=model, max_tokens=400
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("generate.unanswerable_call_failed", error=str(exc))
            continue

        parsed = _extract_json(response.text)
        if not parsed or not parsed.get("question"):
            continue

        items.append(
            GoldenItem(
                id=_item_id("unanswerable", section.document.source_path, str(parsed["question"])),
                question=str(parsed["question"]).strip(),
                category="unanswerable",
                reference_answer=(
                    "I don't have enough information in the documentation to answer that."
                ),
                gold_evidence=[],
                version=None,
                answerable=False,
                curated=False,
                notes=str(parsed.get("reason", "")),
                meta={"generator": "unanswerable", "cost_usd": round(response.cost_usd, 6)},
            )
        )

    return items

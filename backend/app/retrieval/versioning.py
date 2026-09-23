"""Version detection and cross-version conflict detection.

Two separate jobs:

* **Detection** -- which release the question is about. Regex, not an LLM call:
  the patterns are few and unambiguous, and spending a model call plus its
  latency on "does this string contain 1.28" would be indefensible.

* **Conflict detection** -- when the user named no version and the answer
  differs between releases. Handled by retrieving across all versions and
  comparing chunks that share a `(source_path, heading_path)` identity. The
  same section of the same page in two releases is the same claim; if the text
  differs materially, that is a genuine version conflict rather than two
  unrelated passages.

The rule from PROJECT_SPEC.md S8.4 that drives this: never silently blend
content from different versions into one answer.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Chunk, Document
from app.retrieval.base import Candidate

log = get_logger(__name__)

# "1.28", "v1.28", "k8s 1.28", "Kubernetes 1.28", "release-1.28", "1.28.3"
_VERSION_PATTERN = re.compile(
    r"(?:(?:kubernetes|k8s|release|version|v)[\s\-_]*)?\bv?(\d+\.\d+)(?:\.\d+)?\b",
    re.IGNORECASE,
)
# A bare "1.28" is only a version if it is not part of a larger number or a date.
_BARE_NUMBER_CONTEXT = re.compile(r"(?:kubernetes|k8s|release|version|v)\b", re.IGNORECASE)
# ...and not a quantity: "1.25 CPUs", "1.50 GiB", "1.10x", "1.20%".
_UNIT_AFTER = re.compile(
    r"^\s*(?:%|x\b|[kmgt]i?b?\b|[kmgt]i\b|bytes?\b|cpus?\b|cores?\b|vcpus?\b|ms\b|"
    r"s\b|sec(?:ond)?s?\b|min(?:ute)?s?\b|h(?:ou)?rs?\b|replicas?\b|nodes?\b|pods?\b)",
    re.IGNORECASE,
)


def _looks_like_bare_release(question: str, match: re.Match[str]) -> bool:
    """Whether an uncued `1.28` is a Kubernetes release rather than a number.

    PROJECT_SPEC.md S8.4 lists a bare "1.28" as an explicit version, and users
    do write "how does this work in 1.28?". Requiring a cue word made that
    question silently answer from the latest release instead. The guard is
    shape plus context: major version 1, a two-digit minor (every release
    since 1.10), and no unit after it, so "1.5 GB" and "1.25 CPUs" stay
    quantities.
    """
    major, minor = match.group(1).split(".")
    if major != "1" or len(minor) != 2:
        return False
    before = question[max(0, match.start() - 1) : match.start()]
    if before and (before.isdigit() or before in ".,$"):
        return False
    return not _UNIT_AFTER.match(question[match.end() :])


@dataclass
class VersionDecision:
    version: str | None
    explicit: bool
    detected: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "explicit": self.explicit,
            "detected": self.detected,
            "reason": self.reason,
        }


def detect_versions(question: str) -> list[str]:
    """Every version-looking token in the question, in order of appearance."""
    found: list[str] = []
    for match in _VERSION_PATTERN.finditer(question):
        candidate = match.group(1)
        prefix = question[max(0, match.start() - 20) : match.start()]
        # Require a version-ish cue for bare numbers, so "1.5 GB of memory"
        # and "1.2 cores" do not read as Kubernetes releases.
        has_cue = bool(_BARE_NUMBER_CONTEXT.search(prefix)) or match.group(0).lower().startswith(
            ("v", "k8s", "kubernetes", "release", "version")
        )
        if (has_cue or _looks_like_bare_release(question, match)) and candidate not in found:
            found.append(candidate)
    return found


def resolve_version(
    question: str,
    *,
    available: list[str],
    requested: str | None = None,
    default: str = "latest",
    detect: bool = True,
) -> VersionDecision:
    """Decide which version to answer from.

    Precedence: an explicit API argument, then a version named in the question,
    then the configured default.
    """
    if not available:
        return VersionDecision(None, False, [], "no versions indexed")

    newest = max(available, key=lambda v: tuple(int(p) for p in v.split(".")))

    if requested:
        if requested in available:
            return VersionDecision(requested, True, [requested], "requested by caller")
        return VersionDecision(
            newest, False, [requested], f"requested {requested} is not indexed; using {newest}"
        )

    detected = detect_versions(question) if detect else []
    for candidate in detected:
        if candidate in available:
            return VersionDecision(candidate, True, detected, "named in the question")

    if detected:
        # The user named a version we do not have. Saying so beats quietly
        # answering about a different release.
        return VersionDecision(
            newest,
            False,
            detected,
            f"question mentions {detected[0]}, which is not indexed; using {newest}",
        )

    if default != "latest" and default in available:
        return VersionDecision(default, False, [], f"configured default {default}")
    return VersionDecision(newest, False, [], "no version given; using latest indexed")


def indexed_versions(session: Session, chunker_name: str | None = None) -> list[str]:
    """Versions that actually have chunks, newest last."""
    statement = select(Chunk.version).distinct()
    if chunker_name:
        statement = statement.where(Chunk.chunker_name == chunker_name)
    versions = list(session.execute(statement).scalars())
    return sorted(versions, key=lambda v: tuple(int(p) for p in v.split(".")))


@dataclass
class VersionConflict:
    """One section whose content differs between releases."""

    source_path: str
    heading_path: str
    latest_version: str
    other_version: str
    latest_text: str
    other_text: str
    similarity: float

    def to_dict(self, max_text: int = 600) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "heading_path": self.heading_path,
            "latest_version": self.latest_version,
            "other_version": self.other_version,
            "latest_text": self.latest_text[:max_text],
            "other_text": self.other_text[:max_text],
            "similarity": round(self.similarity, 4),
        }


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def closest_counterparts(text: str, others: list[tuple[str, str]]) -> dict[str, tuple[float, str]]:
    """For each other version, the most similar `(text, version)` row to `text`.

    A long section is several chunks under one heading. Comparing a chunk to
    *each* of them reported chunk 1 of 1.30 against chunk 2 of 1.26 as a
    version conflict -- for any section long enough to split. Each version is
    represented by its closest counterpart instead, so only a section with *no*
    close counterpart in that version counts as changed.
    """
    latest = _normalize(text)
    closest: dict[str, tuple[float, str]] = {}
    if not latest:
        return closest
    for other_text, version in others:
        other = _normalize(other_text)
        if not other:
            continue
        similarity = difflib.SequenceMatcher(None, latest, other).ratio()
        if version not in closest or similarity > closest[version][0]:
            closest[version] = (similarity, other_text)
    return closest


def detect_conflicts(
    session: Session,
    candidates: list[Candidate],
    *,
    chunker_name: str,
    answer_version: str,
    all_versions: list[str],
    similarity_threshold: float = 0.92,
    max_sections: int = 5,
) -> list[VersionConflict]:
    """Find retrieved sections whose text differs in other indexed versions.

    Only sections that actually made it into the answer context are checked:
    the question is "does what I am about to say depend on the version", not
    "how did the corpus change overall".
    """
    others = [v for v in all_versions if v != answer_version]
    if not others or not candidates:
        return []

    conflicts: list[VersionConflict] = []
    checked: set[tuple[str, str]] = set()

    for candidate in candidates[:max_sections]:
        key = (candidate.source_path, candidate.heading_path)
        if key in checked:
            continue
        checked.add(key)

        rows = session.execute(
            select(Chunk.text, Chunk.version)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Document.source_path == candidate.source_path,
                Chunk.heading_path == candidate.heading_path,
                Chunk.chunker_name == chunker_name,
                Chunk.version.in_(others),
                Document.deleted_at.is_(None),
            )
        ).all()

        closest = closest_counterparts(candidate.text, [(row.text, row.version) for row in rows])
        for other_version, (similarity, other_text) in closest.items():
            if similarity < similarity_threshold:
                conflicts.append(
                    VersionConflict(
                        source_path=candidate.source_path,
                        heading_path=candidate.heading_path,
                        latest_version=answer_version,
                        other_version=other_version,
                        latest_text=candidate.text,
                        other_text=other_text,
                        similarity=similarity,
                    )
                )

    # Most-changed first: those are the ones worth telling the reader about.
    conflicts.sort(key=lambda c: c.similarity)
    return conflicts

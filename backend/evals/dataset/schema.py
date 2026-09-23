"""Golden dataset schema and IO.

The load-bearing decision is **`gold_evidence`, not `gold_chunk_ids`**.

A chunk id is an artifact of one chunking run. Re-chunk the corpus with
different parameters -- which Phase 3 does deliberately -- and every id in the
dataset points at something else, or at nothing. The labels would silently rot,
and the comparison the whole project rests on would be meaningless.

So gold is recorded as `(source_path, heading_path, version, key_quote)`:
coordinates in the *documentation*, not in the database. A retrieved chunk
matches if it comes from the right page and version and contains the quote.
That survives re-chunking, and it is also what a human curator can actually
verify by opening the page.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.core.settings import REPO_ROOT

GOLDEN_DIR = REPO_ROOT / "data" / "golden"

Category = Literal[
    "factual",
    "exact_term",
    "version_sensitive",
    "multi_hop",
    "table_or_code",
    "unanswerable",
]

CATEGORIES: tuple[Category, ...] = (
    "factual",
    "exact_term",
    "version_sensitive",
    "multi_hop",
    "table_or_code",
    "unanswerable",
)

# Target counts from PROJECT_SPEC.md S9.1.
TARGET_COUNTS: dict[Category, int] = {
    "factual": 80,
    "exact_term": 50,
    "version_sensitive": 60,
    "multi_hop": 40,
    "table_or_code": 30,
    "unanswerable": 40,
}

Split = Literal["dev", "test"]


def normalize_quote(text: str) -> str:
    """Canonical form for quote matching.

    Whitespace and case differences between the curator's copy-paste and the
    stored chunk must not decide whether a retrieval counted as a hit.
    """
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class GoldEvidence:
    """Where the answer lives, in documentation coordinates."""

    source_path: str
    version: str
    heading_path: str = ""
    key_quote: str = ""

    def matches(self, *, source_path: str, version: str, heading_path: str, text: str) -> bool:
        """Whether a retrieved chunk contains this evidence.

        The quote is the real test; heading path is only a tiebreaker, because
        a chunker may legitimately assign a slightly different heading to the
        same passage.
        """
        if self.source_path != source_path or self.version != version:
            return False
        if self.key_quote:
            return normalize_quote(self.key_quote) in normalize_quote(text)
        # With no quote, fall back to the section.
        return not self.heading_path or self.heading_path == heading_path

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GoldenItem:
    id: str
    question: str
    category: Category
    reference_answer: str
    gold_evidence: list[GoldEvidence] = field(default_factory=list)
    version: str | None = None
    answerable: bool = True
    curated: bool = False
    split: Split = "dev"
    notes: str = ""
    # Free-form provenance: which generator made it, from which document.
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["gold_evidence"] = [e.to_dict() for e in self.gold_evidence]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldenItem:
        evidence = [GoldEvidence(**e) for e in data.get("gold_evidence", [])]
        return cls(
            id=data["id"],
            question=data["question"],
            category=data["category"],
            reference_answer=data.get("reference_answer", ""),
            gold_evidence=evidence,
            version=data.get("version"),
            answerable=bool(data.get("answerable", True)),
            curated=bool(data.get("curated", False)),
            split=data.get("split", "dev"),
            notes=data.get("notes", ""),
            meta=data.get("meta", {}),
        )


@dataclass
class GoldenDataset:
    items: list[GoldenItem]
    version: str = "v1"
    path: Path | None = None

    def __len__(self) -> int:
        return len(self.items)

    def split(self, split: Split | None) -> GoldenDataset:
        if split is None:
            return self
        return GoldenDataset([i for i in self.items if i.split == split], self.version, self.path)

    def curated_only(self) -> GoldenDataset:
        """Only curated items count toward reported results (S9.1)."""
        return GoldenDataset([i for i in self.items if i.curated], self.version, self.path)

    def by_category(self) -> dict[str, list[GoldenItem]]:
        out: dict[str, list[GoldenItem]] = {}
        for item in self.items:
            out.setdefault(item.category, []).append(item)
        return out

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in sorted(self.by_category().items())}


def load_dataset(path: str | Path, *, curated_only: bool = True) -> GoldenDataset:
    """Read a JSONL golden set."""
    resolved = Path(path)
    if not resolved.is_absolute() and not resolved.exists():
        candidate = GOLDEN_DIR / resolved
        if candidate.exists():
            resolved = candidate
        elif (REPO_ROOT / resolved).exists():
            resolved = REPO_ROOT / resolved

    if not resolved.exists():
        available = (
            sorted(p.name for p in GOLDEN_DIR.glob("*.jsonl")) if GOLDEN_DIR.exists() else []
        )
        raise FileNotFoundError(f"No golden set at {resolved}. Available: {available}")

    items: list[GoldenItem] = []
    for line_number, line in enumerate(resolved.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        try:
            items.append(GoldenItem.from_dict(json.loads(stripped)))
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError(f"{resolved.name}:{line_number} is not a valid item: {exc}") from exc

    dataset = GoldenDataset(items, version=resolved.stem, path=resolved)
    return dataset.curated_only() if curated_only else dataset


def save_dataset(dataset: GoldenDataset, path: str | Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = GOLDEN_DIR / resolved
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as fh:
        for item in dataset.items:
            fh.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
    return resolved

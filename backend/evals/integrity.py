"""Gold-evidence integrity: can this index score a hit at all?

Retrieval metrics only mean something if every piece of gold evidence is
*matchable* -- if at least one indexed chunk of the right page, in the right
version, under the active chunker, actually contains the gold quote. When that
fails, a retrieval that found exactly the right passage still scores a miss,
and the metric is measuring the index rather than the retriever.

That is not hypothetical. The first `fixed` chunker built chunk text with
`tokenizer.decode`, which lowercased it and spaced out its punctuation, so
only 8 of the golden set's 26 quotes could match *any* fixed-size chunk. The
baseline's recall had a ceiling of about 0.3 that nobody had measured, and the
"+0.500 from structure-aware chunking" headline was mostly that ceiling lifting.
The check that existed -- quotes exist in the source *document* -- passed the
whole time, because it looked one layer too high.

So every evaluation now audits its own gold before scoring, records the result,
and reports `recall_ceiling`: the best recall any retriever could achieve over
this index. A ceiling below 1.0 is a bug in the chunker or the golden set, not
a retrieval result, and the CI gate fails on it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pipeline import PipelineConfig
from app.models import Chunk, Document
from evals.dataset.schema import GoldenDataset, normalize_quote

Status = Literal["ok", "page_not_indexed", "quote_not_in_any_chunk"]


@dataclass
class EvidenceIssue:
    item_id: str
    source_path: str
    version: str
    key_quote: str
    status: Status

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class IntegrityReport:
    """Whether the index can express every gold hit the dataset asks for."""

    chunker_name: str
    evidence_total: int = 0
    evidence_matchable: int = 0
    items_scored: int = 0
    items_matchable: int = 0
    issues: list[EvidenceIssue] = field(default_factory=list)

    @property
    def recall_ceiling(self) -> float:
        """The best recall@k any retriever could reach, for any k.

        An item counts only when *all* its evidence is matchable, mirroring
        how recall@k itself treats multi-hop items.
        """
        return self.items_matchable / self.items_scored if self.items_scored else 1.0

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, object]:
        return {
            "chunker_name": self.chunker_name,
            "evidence_total": self.evidence_total,
            "evidence_matchable": self.evidence_matchable,
            "items_scored": self.items_scored,
            "items_matchable": self.items_matchable,
            "recall_ceiling": round(self.recall_ceiling, 4),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def audit_gold_evidence(
    session: Session, config: PipelineConfig, dataset: GoldenDataset
) -> IntegrityReport:
    """Check every piece of gold evidence against the chunks it must match."""
    report = IntegrityReport(chunker_name=config.chunker_name)
    texts_by_page: dict[tuple[str, str], list[str]] = {}

    def page_texts(source_path: str, version: str) -> list[str]:
        key = (source_path, version)
        if key not in texts_by_page:
            rows = session.execute(
                select(Chunk.text)
                .join(Document, Document.id == Chunk.document_id)
                .where(
                    Document.source_path == source_path,
                    Chunk.version == version,
                    Chunk.chunker_name == config.chunker_name,
                    Document.deleted_at.is_(None),
                )
            ).scalars()
            texts_by_page[key] = [normalize_quote(text) for text in rows]
        return texts_by_page[key]

    for item in dataset.items:
        if not item.gold_evidence:
            continue  # unanswerable: no evidence, excluded from retrieval metrics
        report.items_scored += 1
        all_matchable = True

        for evidence in item.gold_evidence:
            report.evidence_total += 1
            texts = page_texts(evidence.source_path, evidence.version)

            status: Status
            if not texts:
                status = "page_not_indexed"
            elif evidence.key_quote and not any(
                normalize_quote(evidence.key_quote) in text for text in texts
            ):
                status = "quote_not_in_any_chunk"
            else:
                status = "ok"

            if status == "ok":
                report.evidence_matchable += 1
            else:
                all_matchable = False
                report.issues.append(
                    EvidenceIssue(
                        item_id=item.id,
                        source_path=evidence.source_path,
                        version=evidence.version,
                        key_quote=evidence.key_quote,
                        status=status,
                    )
                )

        if all_matchable:
            report.items_matchable += 1

    return report

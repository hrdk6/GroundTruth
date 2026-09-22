"""Pipeline configuration: the experiment variables, loaded from `configs/*.yaml`.

Every knob that could change a retrieval or generation result lives here, so an
experiment is fully described by `(this config, git SHA, dataset version)`.

Two properties are load-bearing:

* **Frozen.** Nothing mutates a config mid-run, so the hash recorded in an
  experiment file always describes the code path that actually executed.
* **Hashed.** `config_hash` is a stable digest of the resolved values (not the
  YAML text), so reformatting a file doesn't invent a "new" config, while a
  changed `k` does.

Defaults are deliberately the *naive* choice — every improvement from Phase 3
onward has to be switched on explicitly by a config, which keeps `baseline.yaml`
honest and makes each experiment a single visible diff.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.settings import REPO_ROOT

CONFIGS_DIR = REPO_ROOT / "configs"

ChunkerName = Literal["fixed", "structure_aware"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ChunkingConfig(_Frozen):
    chunker: ChunkerName = "fixed"
    max_tokens: int = Field(default=512, gt=0)
    overlap_tokens: int = Field(default=64, ge=0)
    min_tokens: int = Field(default=32, ge=0)
    # structure_aware only: prepend "Pods > Lifecycle > Probes" to the embedded
    # text so a chunk carries its position in the document, not just its words.
    prepend_heading_path: bool = True

    @model_validator(mode="after")
    def _overlap_fits(self) -> ChunkingConfig:
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")
        return self


class EmbeddingConfig(_Frozen):
    model: str = "BAAI/bge-small-en-v1.5"
    dimension: int = Field(default=384, gt=0)
    batch_size: int = Field(default=64, gt=0)
    normalize: bool = True
    # bge retrieval models are trained with an instruction prefix on the *query*
    # side only; embedding documents with it measurably hurts.
    query_prefix: str = "Represent this sentence for searching relevant passages: "
    document_prefix: str = ""


class DenseConfig(_Frozen):
    enabled: bool = True
    k: int = Field(default=20, gt=0)


class LexicalConfig(_Frozen):
    enabled: bool = False
    k: int = Field(default=20, gt=0)
    # Postgres FTS config name used for to_tsvector/to_tsquery.
    text_search_config: str = "english"


class FusionConfig(_Frozen):
    enabled: bool = False
    rrf_k: int = Field(default=60, gt=0)


class RerankConfig(_Frozen):
    enabled: bool = False
    model: str = "BAAI/bge-reranker-base"
    batch_size: int = Field(default=32, gt=0)


class QueryRewriteConfig(_Frozen):
    enabled: bool = False
    model: str | None = None  # None -> settings.gt_cheap_model
    # Keep the original query in the lexical leg: a rewrite can drop the exact
    # token the user actually typed, which is the one lexical search needs.
    keep_original_for_lexical: bool = True


class DecompositionConfig(_Frozen):
    enabled: bool = False
    model: str | None = None
    max_subqueries: int = Field(default=3, gt=0, le=5)


class RetrievalConfig(_Frozen):
    dense: DenseConfig = DenseConfig()
    lexical: LexicalConfig = LexicalConfig()
    fusion: FusionConfig = FusionConfig()
    rerank: RerankConfig = RerankConfig()
    query_rewrite: QueryRewriteConfig = QueryRewriteConfig()
    decomposition: DecompositionConfig = DecompositionConfig()
    k_final: int = Field(default=5, gt=0)
    version_filter: bool = True

    @model_validator(mode="after")
    def _at_least_one_leg(self) -> RetrievalConfig:
        if not (self.dense.enabled or self.lexical.enabled):
            raise ValueError("retrieval needs at least one of dense/lexical enabled")
        if self.fusion.enabled and not (self.dense.enabled and self.lexical.enabled):
            raise ValueError("fusion requires both dense and lexical to be enabled")
        return self


class GenerationConfig(_Frozen):
    model: str | None = None  # None -> settings.gt_generation_model
    prompt: str = "grounded_v1"
    max_tokens: int = Field(default=1024, gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)


class VerificationConfig(_Frozen):
    enabled: bool = False
    model: str | None = None
    # Fraction of factual sentences that must be judged `supported` before an
    # answer is returned as-is.
    support_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    max_regenerations: int = Field(default=1, ge=0, le=3)


class VersioningConfig(_Frozen):
    # Which corpus version to answer from when the question names none.
    default_version: str = "latest"
    detect_from_question: bool = True
    conflict_detection: bool = False


class PipelineConfig(_Frozen):
    """A complete, reproducible description of one RAG pipeline."""

    name: str
    description: str = ""
    chunking: ChunkingConfig = ChunkingConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    generation: GenerationConfig = GenerationConfig()
    verification: VerificationConfig = VerificationConfig()
    versioning: VersioningConfig = VersioningConfig()

    @property
    def config_hash(self) -> str:
        """Stable 12-char digest of the resolved values, ignoring `description`."""
        payload = self.model_dump(mode="json", exclude={"description"})
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    @property
    def chunker_name(self) -> str:
        """Identity of a chunk set in the DB: strategy + its parameters.

        Several chunkings coexist in `chunks` so experiments can compare them
        without re-ingesting, which means the key has to include the settings,
        not just the strategy name.
        """
        c = self.chunking
        return f"{c.chunker}-{c.max_tokens}-{c.overlap_tokens}"

    def to_record(self) -> dict[str, Any]:
        """Serialized form embedded in every experiment result file."""
        return {
            "name": self.name,
            "config_hash": self.config_hash,
            "chunker_name": self.chunker_name,
            "values": self.model_dump(mode="json"),
        }


def load_config(path_or_name: str | Path) -> PipelineConfig:
    """Load a pipeline config by path, or by bare name from `configs/`.

    `load_config("hybrid")` and `load_config("configs/hybrid.yaml")` are the same.
    """
    path = Path(path_or_name)
    if not path.suffix:
        path = CONFIGS_DIR / f"{path}.yaml"
    if not path.is_absolute() and not path.exists():
        candidate = REPO_ROOT / path
        if candidate.exists():
            path = candidate
    if not path.exists():
        available = sorted(p.stem for p in CONFIGS_DIR.glob("*.yaml"))
        raise FileNotFoundError(f"No config at {path}. Available in configs/: {available}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.setdefault("name", path.stem)
    return PipelineConfig.model_validate(raw)


def list_configs() -> list[str]:
    return sorted(p.stem for p in CONFIGS_DIR.glob("*.yaml"))

"""ORM models.

Every model must be imported here: `migrations/env.py` relies on this module to
populate `Base.metadata` before Alembic autogenerates a diff. A model that is
not imported is a table Alembic will silently propose dropping.
"""

from app.models.base import Base, TimestampMixin
from app.models.corpus import EMBEDDING_DIM, Chunk, Document, IngestionRun
from app.models.tracing import Feedback, Span, Trace

__all__ = [
    "EMBEDDING_DIM",
    "Base",
    "Chunk",
    "Document",
    "Feedback",
    "IngestionRun",
    "Span",
    "TimestampMixin",
    "Trace",
]

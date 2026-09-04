"""Local sentence-transformers embeddings (ADR-003).

768 dimensions, cosine distance. The dimension is fixed for the life of the
Qdrant collection — changing the model requires a full rebuild, not a
migration.

The model is loaded once per process and reused. It costs ~420 MB on disk and
roughly 500 MB resident, so loading it per call would be ruinous.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import TYPE_CHECKING

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.core.logging import get_logger
from config.settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from sentence_transformers import SentenceTransformer

logger = get_logger(__name__)

# all-mpnet-base-v2 truncates beyond this many word-pieces SILENTLY — no error,
# just a vector for the first part of the text. Chunk sizing exists to stay
# under it (docs/features/document-processing.md).
MAX_SEQUENCE_TOKENS = 384

EMBED_BATCH_SIZE = 32

_lock = threading.Lock()


class EmbeddingError(AppError):
    status_code = 503
    error_code = ErrorCode.EMBEDDING_FAILED
    message = "The embedding model failed."


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Load once. First call downloads ~420 MB if the cache is cold."""
    from sentence_transformers import SentenceTransformer

    logger.info("Loading embedding model", extra={"model": settings.EMBEDDING_MODEL})
    model = SentenceTransformer(settings.EMBEDDING_MODEL)

    actual = model.get_sentence_embedding_dimension()
    if actual != settings.EMBEDDING_DIM:
        # Fail loudly: a mismatch here would create a collection whose vectors
        # can never be searched by this model.
        raise EmbeddingError(
            f"Model dimension {actual} does not match EMBEDDING_DIM "
            f"{settings.EMBEDDING_DIM}. The Qdrant collection would be unusable."
        )

    logger.info("Embedding model ready", extra={"dimension": actual})
    return model


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    model = get_model()
    try:
        # SentenceTransformer.encode is not documented as thread-safe; the
        # worker may run --pool=threads, so serialise access.
        with _lock:
            vectors = model.encode(
                texts,
                batch_size=EMBED_BATCH_SIZE,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
    except Exception as exc:
        raise EmbeddingError(f"Embedding failed: {type(exc).__name__}") from exc

    return [v.tolist() for v in vectors]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


def warm_up() -> bool:
    """Load the model ahead of first use. Returns False if unavailable."""
    try:
        get_model()
        return True
    except Exception as exc:
        logger.warning("Embedding model unavailable", extra={"error": str(exc)})
        return False

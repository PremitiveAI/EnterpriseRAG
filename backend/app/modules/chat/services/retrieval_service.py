"""Retrieval for chat (spec §32, §33, docs/qdrant/collections.md).

Ordinary service, never an agent (§8). Agent 2 proposes what to search for;
this module decides what is *allowed* to be searched and performs the search.
That split is deliberate: authorisation is enforced in the application and
never delegated to a model (§33).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.modules.documents.models import DocumentCategory
from app.vector import repository as vector_repo
from app.vector.embeddings import embed_text
from config.settings import settings

logger = get_logger(__name__)

# The encoder truncates silently past its window (ADR-003), so a pasted essay
# is cut here rather than being quietly half-embedded.
MAX_QUERY_CHARS = 2000


class SearchUnavailable(AppError):
    status_code = 503
    error_code = ErrorCode.SEARCH_FAILED
    message = "Document search is temporarily unavailable."


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_name: str
    text: str
    score: float
    rank: int
    page_number: int | None = None
    section: str | None = None
    category_slug: str | None = None


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    applied_filters: dict[str, str] = field(default_factory=dict)
    dropped_filters: list[str] = field(default_factory=list)
    query_used: str = ""

    @property
    def found(self) -> bool:
        return bool(self.chunks)


class RetrievalService:
    """Retrieval, bound to one organization.

    ``public_only`` narrows the corpus to published documents. It is set by the
    public chatbot service and never taken from a request - it is the only thing
    separating an organization's published material from its private material.
    """

    def __init__(
        self, db: Session, organization_id: UUID, *, public_only: bool = False
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.public_only = public_only

    def search(
        self, query: str, *, proposed_filters: dict[str, str] | None = None
    ) -> RetrievalResult:
        text = (query or "").strip()[:MAX_QUERY_CHARS]
        if not text:
            return RetrievalResult(query_used="")

        applied, dropped = self._validate_filters(proposed_filters or {})

        try:
            vector = embed_text(text)
        except Exception as exc:
            raise SearchUnavailable(f"Could not embed the question: {type(exc).__name__}") from exc

        try:
            hits = vector_repo.search(
                self.organization_id,
                vector,
                public_only=self.public_only,
                # Fewer passages for the public chatbot. It is not a smaller
                # search - the same vectors are scored - it is a smaller prompt,
                # and the prompt is most of what the composer's latency is made
                # of. A brief answer does not need five passages to write.
                limit=(
                    settings.PUBLIC_RETRIEVAL_TOP_K
                    if self.public_only
                    else settings.RETRIEVAL_TOP_K
                ),
                categories=[applied["category_slug"]] if "category_slug" in applied else None,
                language=applied.get("language"),
                document_types=(
                    [applied["document_type"]] if "document_type" in applied else None
                ),
            )
        except Exception as exc:
            raise SearchUnavailable(f"Search failed: {type(exc).__name__}") from exc

        chunks = [
            RetrievedChunk(
                chunk_id=str(hit.payload.get("chunk_id") or ""),
                document_id=str(hit.payload.get("document_id") or ""),
                document_name=str(hit.payload.get("document_name") or "Untitled"),
                text=str(hit.payload.get("text") or ""),
                score=float(hit.score),
                rank=index + 1,
                page_number=hit.payload.get("page_number"),
                section=hit.payload.get("section"),
                category_slug=hit.payload.get("category_slug"),
            )
            for index, hit in enumerate(hits)
        ]

        logger.info(
            "Retrieval complete",
            extra={
                # The question itself is never logged in production (§39).
                "hits": len(chunks),
                "top_score": round(chunks[0].score, 4) if chunks else None,
                "filters_applied": sorted(applied),
                "filters_dropped": dropped,
            },
        )

        return RetrievalResult(
            chunks=chunks, applied_filters=applied, dropped_filters=dropped, query_used=text
        )

    # ------------------------------------------------------------------ #

    def _validate_filters(self, proposed: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        """Check proposed filters against reality before they reach Qdrant.

        An invented `category_slug` matching no document would silently
        eliminate every result — a search that "found nothing" for a question
        the corpus can answer. So a filter that cannot be verified is dropped,
        never passed through (docs/qdrant/collections.md).
        """
        applied: dict[str, str] = {}
        dropped: list[str] = []

        slug = (proposed.get("category_slug") or "").strip().lower()
        if slug:
            exists = self.db.execute(
                select(DocumentCategory.id)
                .where(DocumentCategory.slug == slug)
                .where(DocumentCategory.is_active.is_(True))
            ).scalar_one_or_none()
            if exists:
                applied["category_slug"] = slug
            else:
                dropped.append("category_slug")

        language = (proposed.get("language") or "").strip().lower()
        if language:
            # Two- and three-letter ISO codes only. Anything else is a model
            # returning "English" where a code was asked for.
            if 2 <= len(language) <= 3 and language.isalpha():
                applied["language"] = language
            else:
                dropped.append("language")

        document_type = (proposed.get("document_type") or "").strip().lower()
        if document_type:
            if len(document_type) <= 64:
                applied["document_type"] = document_type
            else:
                dropped.append("document_type")

        return applied, dropped


def resolve_document_ids(db: Session, chunks: list[RetrievedChunk]) -> dict[str, UUID]:
    """Map retrieved chunk payloads to real document ids.

    `message_sources.document_id` is a foreign key, so a payload pointing at a
    document that no longer exists must be dropped rather than inserted.
    """
    from app.modules.documents.models import Document

    candidates: set[UUID] = set()
    for chunk in chunks:
        try:
            candidates.add(UUID(chunk.document_id))
        except (ValueError, AttributeError):
            continue

    if not candidates:
        return {}

    rows = db.execute(select(Document.id).where(Document.id.in_(candidates))).scalars()
    live = {row for row in rows}
    return {str(document_id): document_id for document_id in live}

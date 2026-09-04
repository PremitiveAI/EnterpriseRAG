"""Vector persistence and retrieval (docs/qdrant/collections.md)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from qdrant_client.http import models as qm

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.vector.client import collection_for, get_client, point_id_for
from config.settings import settings

logger = get_logger(__name__)


class IndexingError(AppError):
    status_code = 503
    error_code = ErrorCode.VECTOR_INDEXING_FAILED
    message = "The document could not be indexed."


@dataclass
class ChunkPayload:
    # Redundant given the collection split, and carried anyway as a cross-check:
    # an audit can then prove a stray point did not arrive from another tenant.
    organization_id: str
    # NOT redundant. This is the filter that separates the public chatbot's
    # corpus from the organization's own documents.
    is_public: bool
    document_id: str
    chunk_id: str
    chunk_index: int
    text: str
    document_name: str
    document_type: str | None
    category_slug: str | None
    language: str | None
    tags: list[str]
    page_number: int | None
    section: str | None
    status: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "is_public": self.is_public,
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "document_name": self.document_name,
            "document_type": self.document_type,
            "category_slug": self.category_slug,
            "language": self.language,
            "tags": self.tags,
            "page_number": self.page_number,
            "section": self.section,
            "status": self.status,
            "created_at": self.created_at,
        }


@dataclass
class SearchHit:
    score: float
    payload: dict[str, Any]


def _is_missing_collection(exc: Exception) -> bool:
    """Qdrant reports an absent collection as a 404 inside its own error type."""
    text = str(exc).lower()
    return "not found" in text or "doesn't exist" in text or "404" in text


def delete_document_vectors(organization_id: UUID | str, document_id: UUID | str) -> None:
    """Remove every point for a document, by payload filter.

    Called on delete (§30) and BEFORE re-indexing on reprocess (§29). The second
    case is what stops a shortened document leaving orphaned tail chunks
    searchable forever.
    """
    try:
        get_client().delete(
            collection_name=collection_for(organization_id),
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="document_id", match=qm.MatchValue(value=str(document_id))
                        )
                    ]
                )
            ),
            wait=True,
        )
    except Exception as exc:
        if _is_missing_collection(exc):
            # Nothing indexed for this tenant yet. Deleting from an absent
            # collection is a no-op, not an error - otherwise a document that
            # never reached the indexing stage could never be reprocessed.
            logger.info("No collection to clear",
                        extra={"document_id": str(document_id)})
            return
        raise IndexingError(f"Could not delete vectors: {type(exc).__name__}") from exc


def upsert_chunks(
    organization_id: UUID | str,
    document_id: UUID | str,
    vectors: list[list[float]],
    payloads: list[ChunkPayload],
) -> int:
    if len(vectors) != len(payloads):
        raise IndexingError("Vector and payload counts differ.")
    if not vectors:
        return 0

    points = [
        qm.PointStruct(
            # Deterministic: a retry upserts over the same id rather than
            # doubling the chunks (§23).
            id=point_id_for(document_id, payload.chunk_index),
            vector=vector,
            payload=payload.as_dict(),
        )
        for vector, payload in zip(vectors, payloads, strict=True)
    ]

    try:
        get_client().upsert(
            collection_name=collection_for(organization_id), points=points, wait=True
        )
    except Exception as exc:
        raise IndexingError(f"Could not index vectors: {type(exc).__name__}") from exc

    logger.info("Indexed", extra={"document_id": str(document_id), "points": len(points)})
    return len(points)


def count_document_vectors(organization_id: UUID | str, document_id: UUID | str) -> int:
    try:
        result = get_client().count(
            collection_name=collection_for(organization_id),
            count_filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="document_id", match=qm.MatchValue(value=str(document_id))
                    )
                ]
            ),
            exact=True,
        )
        return result.count
    except Exception as exc:
        if _is_missing_collection(exc):
            return 0
        raise IndexingError(f"Could not count vectors: {type(exc).__name__}") from exc


def search(
    organization_id: UUID | str,
    query_vector: list[float],
    *,
    public_only: bool = False,
    limit: int | None = None,
    categories: list[str] | None = None,
    language: str | None = None,
    tags: list[str] | None = None,
    document_types: list[str] | None = None,
) -> list[SearchHit]:
    """Filtered similarity search.

    The status filter is ALWAYS applied and is applied inside the query rather
    than by discarding results afterwards — so a deleted, failed or duplicate
    document never occupies one of the top-K slots (§33).
    """
    must: list[qm.FieldCondition] = [
        qm.FieldCondition(key="status", match=qm.MatchValue(value="COMPLETED"))
    ]

    # The public chatbot's corpus. Set by the public service, never taken from
    # a request: it is the only thing separating published documents from an
    # organization's private ones.
    if public_only:
        must.append(qm.FieldCondition(key="is_public", match=qm.MatchValue(value=True)))

    if categories:
        must.append(qm.FieldCondition(key="category_slug", match=qm.MatchAny(any=categories)))
    if document_types:
        must.append(qm.FieldCondition(key="document_type", match=qm.MatchAny(any=document_types)))
    if language:
        must.append(qm.FieldCondition(key="language", match=qm.MatchValue(value=language)))
    if tags:
        must.append(qm.FieldCondition(key="tags", match=qm.MatchAny(any=tags)))

    try:
        hits = get_client().search(
            collection_name=collection_for(organization_id),
            query_vector=query_vector,
            query_filter=qm.Filter(must=must),
            limit=limit or settings.RETRIEVAL_TOP_K,
            # Without a threshold an unrelated question still returns the five
            # least-bad chunks, and the composer receives irrelevant context
            # that reads as plausible (§34).
            score_threshold=settings.RETRIEVAL_MIN_SCORE,
            with_payload=True,
        )
    except Exception as exc:
        if _is_missing_collection(exc):
            # Nothing indexed for this tenant yet. The correct answer to a
            # question against an empty corpus is a grounded refusal, not a
            # 503 - and never a fallback to another collection.
            logger.info("No collection to search",
                        extra={"organization_id": str(organization_id)})
            return []
        raise IndexingError(f"Search failed: {type(exc).__name__}") from exc

    return [SearchHit(score=h.score, payload=h.payload or {}) for h in hits]


def set_document_payload(organization_id: UUID | str, document_id: UUID | str,
                         fields: dict[str, Any]) -> None:
    """Rewrite payload fields on every point of one document.

    Used by a metadata-only edit (§29). The vectors are untouched — the text has
    not changed, so re-embedding would burn a minute of GPU-less CPU to produce
    identical numbers.

    Forgetting this call is subtle rather than loud: search keeps working, but
    citations render the old document name and category filters match the old
    value. Right in the database, wrong on screen.
    """
    if not fields:
        return
    try:
        get_client().set_payload(
            collection_name=collection_for(organization_id),
            payload=fields,
            points=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="document_id", match=qm.MatchValue(value=str(document_id))
                    )
                ]
            ),
            wait=True,
        )
    except Exception as exc:
        raise IndexingError(f"Could not update payload: {type(exc).__name__}") from exc

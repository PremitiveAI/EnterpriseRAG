"""Qdrant client and collection lifecycle (ADR-006, docs/qdrant/collections.md)."""

from __future__ import annotations

from functools import lru_cache
from uuid import NAMESPACE_URL, UUID, uuid5

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.core.logging import get_logger
from config.settings import settings

logger = get_logger(__name__)

# Namespace for deterministic point ids. Fixed forever — changing it would
# orphan every existing vector.
NAMESPACE_DOCUMENT = uuid5(NAMESPACE_URL, "enterprise-rag/document-chunk")

PAYLOAD_INDEXES: tuple[tuple[str, qm.PayloadSchemaType], ...] = (
    ("document_id", qm.PayloadSchemaType.KEYWORD),
    ("category_slug", qm.PayloadSchemaType.KEYWORD),
    ("document_type", qm.PayloadSchemaType.KEYWORD),
    ("language", qm.PayloadSchemaType.KEYWORD),
    ("tags", qm.PayloadSchemaType.KEYWORD),
    ("status", qm.PayloadSchemaType.KEYWORD),
)


class VectorStoreUnavailable(AppError):
    status_code = 503
    error_code = ErrorCode.SEARCH_FAILED
    message = "The vector store is unavailable."


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    return QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY or None,
        timeout=30,
    )


def point_id_for(document_id: UUID | str, chunk_index: int) -> str:
    """Deterministic, so a Celery retry upserts instead of duplicating (§23)."""
    return str(uuid5(NAMESPACE_DOCUMENT, f"{document_id}:{chunk_index}"))


def collection_for(organization_id: UUID | str) -> str:
    """The Qdrant collection for a tenant.

    ``org_`` plus the UUID with hyphens removed - deterministic, so no lookup
    table is needed and no two tenants can resolve to the same name.

    One collection per organization rather than a shared collection with an
    ``organization_id`` payload filter: both isolate correctly when the code is
    correct, but a forgotten filter returns EVERY tenant's chunks, while a
    wrongly-chosen collection returns nothing. For a requirement stated as
    mandatory, that difference is the whole argument (ADR-006 superseded for
    the multi-tenant case).
    """
    return f"org_{str(organization_id).replace('-', '')}"


def ensure_collection(organization_id: UUID | str) -> None:
    """Create the collection once, only if absent.

    NEVER recreate_collection: it DELETES the collection. At module scope it
    would wipe the index on every start and every --reload, silently — the
    collection still exists afterwards and simply contains nothing (ADR-006).
    """
    client = get_client()
    name = collection_for(organization_id)

    try:
        exists = client.collection_exists(name)
    except Exception as exc:
        raise VectorStoreUnavailable(f"Could not reach Qdrant: {type(exc).__name__}") from exc

    if not exists:
        client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(
                size=settings.EMBEDDING_DIM, distance=qm.Distance.COSINE
            ),
        )
        logger.info("Created collection",
                    extra={"collection": name, "dimension": settings.EMBEDDING_DIM})

    for field, schema in PAYLOAD_INDEXES:
        try:
            client.create_payload_index(name, field_name=field, field_schema=schema)
        except Exception:
            # Already present. Qdrant has no create-if-absent for indexes.
            pass


def delete_collection(organization_id: UUID | str) -> None:
    """Drop a tenant's collection.

    The only destructive collection call outside tests, and it exists because
    deleting an organization must not leave its vectors searchable.
    """
    name = collection_for(organization_id)
    try:
        get_client().delete_collection(name)
        logger.info("Collection dropped", extra={"collection": name})
    except Exception as exc:
        logger.warning("Could not drop collection",
                       extra={"collection": name, "error_type": type(exc).__name__})


def collection_info(organization_id: UUID | str) -> dict[str, object]:
    client = get_client()
    try:
        info = client.get_collection(collection_for(organization_id))
        return {
            "collection": collection_for(organization_id),
            "points": info.points_count,
            "status": str(info.status),
        }
    except Exception as exc:
        raise VectorStoreUnavailable(f"Could not read collection: {type(exc).__name__}") from exc


def is_available() -> bool:
    try:
        get_client().get_collections()
        return True
    except Exception:
        return False

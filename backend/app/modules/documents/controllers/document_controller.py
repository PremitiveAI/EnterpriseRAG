"""Translates document-management requests into service calls (spec §12).

Query-parameter parsing and the response envelope live here. No business rule
does.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.modules.documents.models import DocumentStatus
from app.modules.documents.repositories.document_repository import SORTABLE, ListQuery
from app.modules.documents.schemas.management import DocumentUpdateRequest
from app.modules.documents.services.management_service import (
    DocumentManagementService,
    RequestContext,
)
from app.storage.base import StorageService
from config.settings import settings


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def context_from(request: Request, actor) -> RequestContext:
    """Audit context for the acting subject.

    Untyped on purpose: the actor is an OrganizationAdmin on tenant routes and a
    User on Super Admin routes, and both only need an id here.
    """
    return RequestContext(
        user_id=actor.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )


class DocumentController:
    def __init__(
        self, db: Session, storage: StorageService, organization_id: UUID
    ) -> None:
        self.organization_id = organization_id
        self.service = DocumentManagementService(db, storage, organization_id)

    # --- Read ---------------------------------------------------------- #

    def list(
        self,
        *,
        search: str | None,
        status: list[str] | None,
        category_id: list[UUID] | None,
        document_type: str | None,
        language: str | None,
        tag: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        sort: str,
        order: str,
        page: int,
        page_size: int,
        include_deleted: bool,
    ) -> dict:
        query = ListQuery(
            # From the token. Never from the request - a caller cannot name
            # an organization (docs/release-2/features/tenant-isolation.md).
            organization_id=self.organization_id,
            search=search or None,
            status=_parse_statuses(status),
            category_id=list(category_id or []),
            document_type=document_type or None,
            language=language or None,
            tag=tag or None,
            created_from=created_from,
            created_to=_end_of_day(created_to),
            sort=_parse_sort(sort),
            order=_parse_order(order),
            page=max(1, page),
            page_size=min(max(1, page_size), settings.MAX_PAGE_SIZE),
            include_deleted=include_deleted,
        )
        return {"success": True, "data": self.service.list(query).model_dump(mode="json")}

    def detail(self, document_id: UUID) -> dict:
        return {"success": True,
                "data": self.service.detail(document_id).model_dump(mode="json")}

    def filter_options(self) -> dict:
        return {"success": True,
                "data": self.service.filter_options().model_dump(mode="json")}

    # --- Mutations ----------------------------------------------------- #

    def update(
        self, document_id: UUID, payload: DocumentUpdateRequest, request: Request, actor
    ) -> dict:
        detail = self.service.update(document_id, payload, context_from(request, actor))
        return {"success": True, "message": "Document updated.",
                "data": detail.model_dump(mode="json")}

    def publish(self, document_id: UUID, is_public: bool, request: Request, actor) -> dict:
        detail = self.service.set_public(document_id, is_public, context_from(request, actor))
        return {
            "success": True,
            "message": "Document published." if is_public else "Document unpublished.",
            "data": detail.model_dump(mode="json"),
        }

    def delete(self, document_id: UUID, request: Request, actor) -> dict:
        data = self.service.delete(document_id, context_from(request, actor))
        return {"success": True, "message": "Document deleted.", "data": data}

    def reprocess(self, document_id: UUID, request: Request, actor) -> dict:
        result = self.service.reprocess(document_id, context_from(request, actor))
        return {"success": True, "message": "Document queued for reprocessing.",
                "data": result.model_dump(mode="json")}


# --- Parameter parsing --------------------------------------------------- #


def _parse_statuses(values: list[str] | None) -> list[DocumentStatus]:
    if not values:
        return []
    parsed: list[DocumentStatus] = []
    for value in values:
        try:
            parsed.append(DocumentStatus(value.strip().upper()))
        except ValueError as exc:
            raise ValidationError(
                f"Unknown status '{value}'.",
                error_code=ErrorCode.VALIDATION_ERROR,
                details={"allowed": [str(s) for s in DocumentStatus]},
            ) from exc
    return parsed


def _end_of_day(value: datetime | None) -> datetime | None:
    """Make a date-only `created_to` cover the whole day.

    `?created_to=2026-08-20` parses to midnight, which would exclude everything
    uploaded that day — a range of "today to today" returning nothing reads as
    a broken filter rather than as an off-by-one.
    """
    if value is None:
        return None
    if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
        return value + timedelta(days=1) - timedelta(microseconds=1)
    return value


def _parse_sort(value: str) -> str:
    """Rejected outright rather than silently defaulted.

    Silently falling back to created_at would hide a client bug behind a list
    that looks fine but is ordered by something the caller did not ask for.
    """
    if value not in SORTABLE:
        raise ValidationError(
            f"Cannot sort by '{value}'.",
            details={"allowed": sorted(SORTABLE)},
        )
    return value


def _parse_order(value: str) -> str:
    if value not in {"asc", "desc"}:
        raise ValidationError(f"Unknown order '{value}'.",
                              details={"allowed": ["asc", "desc"]})
    return value

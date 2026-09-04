"""Document routes. Path definitions and dependency wiring only (spec §12)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.principal import current_org_admin, current_organization_id
from app.modules.documents.controllers.document_controller import (
    DocumentController,
    context_from,
)
from app.modules.documents.controllers.status_controller import StatusController
from app.modules.documents.controllers.upload_controller import UploadController
from app.modules.documents.schemas.management import (
    DocumentUpdateRequest,
    PublishRequest,
)
from app.modules.documents.schemas.upload import UploadLimits
from app.modules.documents.services.management_service import DocumentManagementService
from app.storage.local import get_storage
from config.settings import settings

router = APIRouter(prefix="/admin/documents", tags=["documents"])
config_router = APIRouter(prefix="/config", tags=["config"])


@router.post("/upload", response_model=None)
def upload(
    request: Request,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(current_organization_id),
    admin = Depends(current_org_admin),
) -> dict:
    return UploadController(db, get_storage(), organization_id).upload(files, request, admin)


# Declared BEFORE /{document_id}: a UUID path parameter would otherwise try to
# parse "categories" and answer 422 instead of serving the taxonomy.
@router.get("/categories", response_model=None)
def categories(
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(current_organization_id),
) -> dict:
    """Everything the filter bar needs, in one request rather than four."""
    return DocumentController(db, get_storage(), organization_id).filter_options()


@router.get("", response_model=None)
def list_documents(
    search: str | None = Query(default=None, max_length=200),
    status: list[str] | None = Query(default=None),
    category_id: list[UUID] | None = Query(default=None),
    document_type: str | None = Query(default=None, max_length=64),
    language: str | None = Query(default=None, max_length=8),
    tag: str | None = Query(default=None, max_length=64),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    sort: str = Query(default="created_at"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=settings.DEFAULT_PAGE_SIZE, ge=1),
    include_deleted: bool = Query(default=False),
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(current_organization_id),
) -> dict:
    return DocumentController(db, get_storage(), organization_id).list(
        search=search,
        status=status,
        category_id=category_id,
        document_type=document_type,
        language=language,
        tag=tag,
        created_from=created_from,
        created_to=created_to,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
        include_deleted=include_deleted,
    )


@router.get("/{document_id}", response_model=None)
def document_detail(
    document_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(current_organization_id),
) -> dict:
    return DocumentController(db, get_storage(), organization_id).detail(document_id)


@router.patch("/{document_id}", response_model=None)
def update_document(
    document_id: UUID,
    payload: DocumentUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(current_organization_id),
    admin = Depends(current_org_admin),
) -> dict:
    """Metadata only — never re-embeds (§29)."""
    return DocumentController(db, get_storage(), organization_id).update(document_id, payload, request, admin)


@router.patch("/{document_id}/publish", response_model=None)
def publish_document(
    document_id: UUID,
    payload: PublishRequest,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(current_organization_id),
    admin = Depends(current_org_admin),
) -> dict:
    """Expose a document to the organization's public chatbot, or withdraw it.

    Opt-in per document: uploading never publishes.
    """
    return DocumentController(db, get_storage(), organization_id).publish(
        document_id, payload.is_public, request, admin
    )


@router.delete("/{document_id}", response_model=None)
def delete_document(
    document_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(current_organization_id),
    admin = Depends(current_org_admin),
) -> dict:
    """Soft delete plus vector removal (§30)."""
    return DocumentController(db, get_storage(), organization_id).delete(document_id, request, admin)


@router.post("/{document_id}/reprocess", status_code=202, response_model=None)
def reprocess_document(
    document_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(current_organization_id),
    admin = Depends(current_org_admin),
) -> dict:
    return DocumentController(db, get_storage(), organization_id).reprocess(document_id, request, admin)


@router.get("/{document_id}/download", response_model=None)
def download_document(
    document_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(current_organization_id),
    admin = Depends(current_org_admin),
) -> StreamingResponse:
    service = DocumentManagementService(db, get_storage(), organization_id)
    download = service.download(document_id, context_from(request, admin))

    return StreamingResponse(
        download.stream,
        media_type=download.mime_type,
        headers={
            # attachment, not inline: an uploaded HTML or SVG file rendered
            # inline would execute in this origin.
            "Content-Disposition": f'attachment; filename="{download.file_name}"',
            "Content-Length": str(download.size),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{document_id}/status", response_model=None)
def document_status(
    document_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(current_organization_id),
) -> dict:
    """Polling target. Stops when `is_terminal` is true (spec §44)."""
    return StatusController(db, organization_id).get(document_id)


@config_router.get("/upload-limits", response_model=None)
def upload_limits() -> dict:
    """Served so client-side validation cannot drift from server rules (§16)."""
    limits = UploadLimits(
        max_document_size_mb=settings.MAX_DOCUMENT_SIZE_MB,
        max_image_size_mb=settings.MAX_IMAGE_SIZE_MB,
        max_files_per_batch=settings.MAX_FILES_PER_BATCH,
        document_extensions=sorted(settings.ALLOWED_DOCUMENT_EXTENSIONS),
        image_extensions=sorted(settings.ALLOWED_IMAGE_EXTENSIONS),
        legacy_extensions=sorted(settings.LEGACY_EXTENSIONS),
    )
    return {"success": True, "data": limits.model_dump(mode="json")}

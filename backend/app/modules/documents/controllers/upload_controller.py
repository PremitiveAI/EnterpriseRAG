"""Translates the upload request into UploadService calls (spec §12)."""

from __future__ import annotations

from fastapi import Request, UploadFile
from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.modules.documents.services.upload_service import UploadService
from app.storage.base import StorageService
from config.settings import settings


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


class UploadController:
    def __init__(self, db: Session, storage: StorageService,
                 organization_id: UUID) -> None:
        self.service = UploadService(db, storage, organization_id)

    def upload(self, files: list[UploadFile], request: Request, actor) -> dict:
        # Whole-request failures are real errors. A per-file failure is data,
        # returned inside a 200 alongside the files that succeeded (§19).
        if not files:
            raise ValidationError(
                "No files were provided.",
                error_code=ErrorCode.NO_FILES_PROVIDED,
                status_code=400,
            )

        if len(files) > settings.MAX_FILES_PER_BATCH:
            raise ValidationError(
                f"At most {settings.MAX_FILES_PER_BATCH} files may be uploaded at once.",
                error_code=ErrorCode.TOO_MANY_FILES,
                status_code=400,
                details={"submitted": len(files), "limit": settings.MAX_FILES_PER_BATCH},
            )

        summary = self.service.upload_batch(
            list(files),
            user_id=actor.id,
            request_id=getattr(request.state, "request_id", None),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
        )

        return {
            "success": True,
            "message": _summarise(summary.accepted, summary.duplicates, summary.rejected),
            "data": summary.model_dump(mode="json"),
        }


def _summarise(accepted: int, duplicates: int, rejected: int) -> str:
    parts = [f"{accepted} accepted"]
    if duplicates:
        parts.append(f"{duplicates} duplicate{'s' if duplicates != 1 else ''}")
    if rejected:
        parts.append(f"{rejected} rejected")
    return ", ".join(parts) + "."

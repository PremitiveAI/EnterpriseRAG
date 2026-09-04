"""Translates Super Admin category requests into service calls (spec §12)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.documents.schemas.category import (
    CreateCategoryRequest,
    UpdateCategoryRequest,
)
from app.modules.documents.services.category_service import CategoryService


class CategoryController:
    def __init__(self, db: Session, super_admin_id: UUID) -> None:
        self.service = CategoryService(db, super_admin_id)

    def list(self) -> dict:
        return {"success": True, "data": self.service.list().model_dump(mode="json")}

    def create(self, payload: CreateCategoryRequest) -> dict:
        category = self.service.create(payload)
        return {"success": True, "message": "Category created.",
                "data": category.model_dump(mode="json")}

    def update(self, category_id: UUID, payload: UpdateCategoryRequest) -> dict:
        category = self.service.update(category_id, payload)
        return {"success": True, "message": "Category updated.",
                "data": category.model_dump(mode="json")}

    def delete(self, category_id: UUID) -> dict:
        result = self.service.delete(category_id)
        # The message tells the caller which of the two things happened; a bare
        # "Category deleted." would be a lie half the time.
        message = (
            "Category deleted."
            if result.deleted
            else f"Category deactivated - {result.document_count} document(s) still use it."
        )
        return {"success": True, "message": message, "data": result.model_dump(mode="json")}

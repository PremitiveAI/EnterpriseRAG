"""Super Admin category master routes (spec §12).

Mounted under ``/super-admin`` alongside the organization routes even though the
model lives in the documents module: the taxonomy is a document concern, but
managing it is a Super Admin power. Organization Admins keep Release 1's
read-only ``/admin/documents/categories``, which returns active rows only.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.principal import Principal, require_super_admin
from app.modules.documents.controllers.category_controller import CategoryController
from app.modules.documents.schemas.category import (
    CreateCategoryRequest,
    UpdateCategoryRequest,
)

router = APIRouter(prefix="/super-admin", tags=["super-admin"])


def controller(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_super_admin),
) -> CategoryController:
    return CategoryController(db, principal.subject_id)


@router.get("/categories", response_model=None)
def list_categories(ctrl: CategoryController = Depends(controller)) -> dict:
    """Including inactive - this screen is where they get reactivated."""
    return ctrl.list()


@router.post("/categories", status_code=201, response_model=None)
def create_category(
    payload: CreateCategoryRequest,
    ctrl: CategoryController = Depends(controller),
) -> dict:
    return ctrl.create(payload)


@router.patch("/categories/{category_id}", response_model=None)
def update_category(
    category_id: UUID,
    payload: UpdateCategoryRequest,
    ctrl: CategoryController = Depends(controller),
) -> dict:
    """Name, description, order and active state. Never the slug."""
    return ctrl.update(category_id, payload)


@router.delete("/categories/{category_id}", response_model=None)
def delete_category(
    category_id: UUID,
    ctrl: CategoryController = Depends(controller),
) -> dict:
    """Hard delete when unused, deactivation when documents still carry it."""
    return ctrl.delete(category_id)

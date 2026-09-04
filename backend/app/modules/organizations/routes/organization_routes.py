"""Super Admin routes. Path definitions and dependency wiring only (spec §12).

Every route here requires ``subject_type = SUPER_ADMIN``. An Organization Admin
token is rejected with 403 by the dependency, before any handler runs.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.principal import Principal, require_super_admin
from app.modules.organizations.controllers.organization_controller import (
    OrganizationController,
)
from app.modules.organizations.schemas.organization import (
    CreateAdminRequest,
    CreateOrganizationRequest,
    UpdateAdminRequest,
    UpdateOrganizationRequest,
)

router = APIRouter(prefix="/super-admin", tags=["super-admin"])


def controller(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_super_admin),
) -> OrganizationController:
    return OrganizationController(db, principal.subject_id)


# --- Organizations -------------------------------------------------------- #


@router.post("/organizations", status_code=201, response_model=None)
def create_organization(
    payload: CreateOrganizationRequest,
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    """Creates the organization AND provisions its Qdrant collection."""
    return ctrl.create(payload)


@router.get("/organizations", response_model=None)
def list_organizations(
    search: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1),
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    return ctrl.list(search=search, status=status, page=page, page_size=page_size)


@router.get("/organizations/{organization_id}", response_model=None)
def organization_detail(
    organization_id: UUID,
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    """Counts and configuration only - never document titles or chat content.

    That is the Super Admin privacy guarantee, enforced by what this returns.
    """
    return ctrl.detail(organization_id)


@router.patch("/organizations/{organization_id}", response_model=None)
def update_organization(
    organization_id: UUID,
    payload: UpdateOrganizationRequest,
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    return ctrl.update(organization_id, payload)


@router.post("/organizations/{organization_id}/suspend", response_model=None)
def suspend_organization(
    organization_id: UUID,
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    """Reversible. Blocks login and public chat; retains all data."""
    return ctrl.suspend(organization_id)


@router.post("/organizations/{organization_id}/activate", response_model=None)
def activate_organization(
    organization_id: UUID,
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    return ctrl.activate(organization_id)


@router.delete("/organizations/{organization_id}", response_model=None)
def delete_organization(
    organization_id: UUID,
    force: bool = Query(default=False),
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    """Soft delete plus dropping the collection.

    `force` is required when documents exist: deleting a tenant is
    unrecoverable in a way suspension is not.
    """
    return ctrl.delete(organization_id, force=force)


# --- Organization admins -------------------------------------------------- #


@router.post("/organizations/{organization_id}/admins", status_code=201,
             response_model=None)
def create_admin(
    organization_id: UUID,
    payload: CreateAdminRequest,
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    return ctrl.create_admin(organization_id, payload)


@router.get("/organizations/{organization_id}/admins", response_model=None)
def list_admins(
    organization_id: UUID,
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    return ctrl.list_admins(organization_id)


@router.patch("/admins/{admin_id}", response_model=None)
def update_admin(
    admin_id: UUID,
    payload: UpdateAdminRequest,
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    """Changing an email or mobile writes its own audit action - a Super Admin
    who repoints an identifier can receive that admin's OTP."""
    return ctrl.update_admin(admin_id, payload)


@router.delete("/admins/{admin_id}", response_model=None)
def deactivate_admin(
    admin_id: UUID,
    ctrl: OrganizationController = Depends(controller),
) -> dict:
    return ctrl.deactivate_admin(admin_id)

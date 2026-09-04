"""The authenticated subject, resolved once per request.

Release 2 has two kinds of caller and they are not interchangeable:

* **Super Admin** — a ``users`` row. Manages organizations. Carries **no**
  ``organization_id``, so organization data routes reject it structurally
  rather than by a permission check.
* **Organization Admin** — an ``organization_admins`` row. Belongs to exactly
  one organization and carries it in the token.

Everything downstream takes the tenant from here. No service and no repository
reads an organization id from a path, query string or request body on an
authenticated route (docs/release-2/features/tenant-isolation.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import ForbiddenError, UnauthorizedError


@dataclass(frozen=True)
class Principal:
    """Who is calling, and which tenant they belong to."""

    subject_id: UUID
    subject_type: str                     # SUPER_ADMIN | ORG_ADMIN
    organization_id: UUID | None = None   # None for a Super Admin
    email: str | None = None

    @property
    def is_super_admin(self) -> bool:
        return self.subject_type == "SUPER_ADMIN"

    @property
    def is_org_admin(self) -> bool:
        return self.subject_type == "ORG_ADMIN"

    def require_organization(self) -> UUID:
        """The tenant, or 403.

        Called by every organization-scoped service. A Super Admin reaching one
        of those routes fails here — they have no organization, and inventing a
        default would be exactly the cross-tenant hole this class exists to
        prevent.
        """
        if self.organization_id is None:
            raise ForbiddenError(
                "This endpoint is scoped to an organization. "
                "A Super Admin has no organization context."
            )
        return self.organization_id


def current_principal(request: Request) -> Principal:
    """The authenticated subject, built by AuthMiddleware."""
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise UnauthorizedError()
    return principal


def require_super_admin(
    principal: Principal = Depends(current_principal),
) -> Principal:
    if not principal.is_super_admin:
        raise ForbiddenError("Super Admin access is required.")
    return principal


def require_org_admin(
    principal: Principal = Depends(current_principal),
) -> Principal:
    if not principal.is_org_admin:
        raise ForbiddenError("This endpoint is for Organization Admins.")
    return principal


def current_organization_id(
    principal: Principal = Depends(require_org_admin),
) -> UUID:
    """The tenant for the current request.

    Injected into organization-scoped routes so the handler never has to reach
    for it, and never has an opportunity to take it from the request instead.
    """
    return principal.require_organization()


def current_org_admin(
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """The Organization Admin row, live on this request's session."""
    from app.modules.organizations.repositories.admin_repository import (
        OrganizationAdminRepository,
    )

    admin = OrganizationAdminRepository(db).get(principal.subject_id)
    if admin is None or not admin.is_active:
        raise UnauthorizedError("This account is no longer active.")
    return admin

"""Database access for users and audit logs. No business rules here."""

from __future__ import annotations

from datetime import datetime, timezone
from ipaddress import ip_address
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.auth.models import ActorType, AuditLog, User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, user_id: UUID) -> User | None:
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        # Email is stored lowercased, so lookups normalise the same way.
        stmt = select(User).where(User.email == email.strip().lower())
        return self.db.execute(stmt).scalar_one_or_none()

    def create(self, *, email: str, password_hash: str, full_name: str) -> User:
        user = User(
            email=email.strip().lower(),
            password_hash=password_hash,
            full_name=full_name.strip(),
            is_active=True,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def touch_last_login(self, user: User) -> None:
        user.last_login_at = datetime.now(timezone.utc)
        self.db.flush()

    def count(self) -> int:
        return self.db.execute(select(func.count()).select_from(User)).scalar_one()


def coerce_ip(value: str | None) -> str | None:
    """Return a valid IP address, or None.

    ``audit_logs.ip_address`` is INET, so a non-IP value raises on insert and
    would turn an audited action into a 500. The value can be attacker-supplied
    — X-Forwarded-For is a request header — so it is validated rather than
    trusted, and a bad one is dropped instead of failing the request.
    """
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


class AuditRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: UUID | None = None,
        user_id: UUID | None = None,
        actor_type: str | None = None,
        organization_id: UUID | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Record one action.

        Release 2 has two actor types with their own foreign keys, so the id is
        routed to the right column rather than stored loosely. ``user_id`` is
        kept as the parameter name because every existing call site uses it;
        ``actor_type`` decides where it lands, defaulting to an Organization
        Admin because that is who performs almost every audited action.
        """
        kind = actor_type or (ActorType.ORG_ADMIN if user_id else ActorType.SYSTEM)

        entry = AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_type=kind,
            super_admin_id=user_id if kind == ActorType.SUPER_ADMIN else None,
            organization_admin_id=user_id if kind == ActorType.ORG_ADMIN else None,
            organization_id=organization_id,
            request_id=request_id,
            ip_address=coerce_ip(ip_address),
            user_agent=(user_agent or "")[:512] or None,
            metadata_=metadata or {},
        )
        self.db.add(entry)
        self.db.flush()
        return entry

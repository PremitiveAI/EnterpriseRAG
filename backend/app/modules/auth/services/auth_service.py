"""Authentication business logic.

Knows nothing about HTTP — the same service is callable from a route or a
script (docs/architecture/backend-architecture.md).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.cache import redis_client
from app.core.exceptions import (
    AccountInactiveError,
    ForbiddenError,
    InvalidCredentialsError,
    RefreshTokenInvalidError,
    UnauthorizedError,
)
from app.core.principal import Principal
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
    waste_password_comparison,
)
from app.modules.auth.models import ActorType, AuditAction, User
from app.modules.auth.repositories.user_repository import AuditRepository, UserRepository
from config.settings import settings

logger = get_logger(__name__)


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.audit = AuditRepository(db)

    # --- Login ---------------------------------------------------------- #

    def authenticate(
        self,
        email: str,
        password: str,
        *,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User, str, str, str]:
        """Returns ``(user, access_token, refresh_token, refresh_jti)``."""
        user = self.users.get_by_email(email)

        if user is None:
            # Spend the same time as a real verification so an unknown email
            # cannot be distinguished from a wrong password by timing.
            waste_password_comparison()
            self._audit_failure(email, "unknown_email", request_id, ip_address, user_agent)
            raise InvalidCredentialsError()

        if not verify_password(password, user.password_hash):
            self._audit_failure(email, "bad_password", request_id, ip_address, user_agent)
            raise InvalidCredentialsError()

        if not user.is_active:
            self._audit_failure(email, "inactive", request_id, ip_address, user_agent)
            raise AccountInactiveError()

        access = create_access_token(str(user.id))
        refresh, jti = create_refresh_token(str(user.id))

        self.users.touch_last_login(user)
        self.audit.record(
            actor_type=ActorType.SUPER_ADMIN,
            action=AuditAction.LOGIN,
            entity_type="user",
            entity_id=user.id,
            user_id=user.id,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()

        logger.info("Login succeeded", extra={"user_id": str(user.id)})
        return user, access, refresh, jti

    def _audit_failure(
        self,
        email: str,
        reason: str,
        request_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        # The email is recorded because a failed-login audit is useless without
        # it. The password never is.
        self.audit.record(
            actor_type=ActorType.SUPER_ADMIN,
            action=AuditAction.LOGIN_FAILED,
            entity_type="user",
            user_id=None,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"email": email.strip().lower(), "reason": reason},
        )
        self.db.commit()
        logger.warning("Login failed", extra={"reason": reason})

    # --- Refresh / logout ------------------------------------------------ #

    def refresh_access_token(self, refresh_token: str) -> tuple[User, str]:
        payload = decode_token(refresh_token, "refresh")
        jti = payload.get("jti", "")

        if redis_client.is_refresh_jti_revoked(jti):
            raise RefreshTokenInvalidError()

        user = self.users.get_by_id(UUID(payload["sub"]))
        if user is None or not user.is_active:
            raise RefreshTokenInvalidError()

        return user, create_access_token(str(user.id))

    def logout(
        self,
        refresh_token: str | None,
        *,
        user_id: UUID | None = None,
        request_id: str | None = None,
    ) -> None:
        if refresh_token:
            try:
                payload = decode_token(refresh_token, "refresh")
                redis_client.revoke_refresh_jti(
                    payload.get("jti", ""), settings.REFRESH_TOKEN_DAYS * 86_400
                )
            except RefreshTokenInvalidError:
                # Already invalid — logout is still a success from the caller's
                # point of view.
                pass

        if user_id is not None:
            self.audit.record(
                actor_type=ActorType.SUPER_ADMIN,
                action=AuditAction.LOGOUT,
                entity_type="user",
                entity_id=user_id,
                user_id=user_id,
                request_id=request_id,
            )
            self.db.commit()

    # --- Current user ---------------------------------------------------- #

    def user_from_access_token(self, token: str) -> User:
        payload = decode_token(token, "access")
        user = self.users.get_by_id(UUID(payload["sub"]))
        if user is None:
            raise UnauthorizedError("User no longer exists.")
        if not user.is_active:
            raise AccountInactiveError()
        return user

    # --- Admin bootstrap (scripts/create_admin.py) ----------------------- #

    def create_admin(self, *, email: str, password: str, full_name: str) -> User:
        user = self.users.create(
            email=email, password_hash=hash_password(password), full_name=full_name
        )
        self.db.commit()
        return user


# --------------------------------------------------------------------------- #
# Release 2: one token, two possible subjects.
# --------------------------------------------------------------------------- #


def resolve_principal(db: Session, token: str) -> Principal:
    """Turn an access token into the caller it represents.

    A token with ``subject_type = ORG_ADMIN`` must also carry an
    ``organization_id``. A token missing it is rejected rather than treated as
    a Super Admin - silently widening a caller's scope on a malformed claim is
    exactly the failure this function exists to prevent.
    """
    payload = decode_token(token, "access")
    subject_type = payload.get("subject_type", "SUPER_ADMIN")
    subject_id = UUID(payload["sub"])

    if subject_type == "ORG_ADMIN":
        from app.modules.organizations.repositories.admin_repository import (
            OrganizationAdminRepository,
        )

        raw_org = payload.get("organization_id")
        if not raw_org:
            raise UnauthorizedError("Token is missing its organization.")

        admin = OrganizationAdminRepository(db).get(subject_id)
        if admin is None or not admin.is_active:
            raise UnauthorizedError("This account is no longer active.")

        # The organization is re-checked on every request, not just at login:
        # suspending an organization must take effect immediately rather than
        # when the admin's token happens to expire.
        organization = admin.organization
        if organization is None or not organization.is_active:
            raise ForbiddenError("This organization is not active.")

        if str(organization.id) != str(raw_org):
            raise UnauthorizedError("Token organization does not match the account.")

        return Principal(
            subject_id=admin.id,
            subject_type="ORG_ADMIN",
            organization_id=organization.id,
            email=admin.email,
        )

    user = UserRepository(db).get_by_id(subject_id)
    if user is None:
        raise UnauthorizedError("User no longer exists.")
    if not user.is_active:
        raise AccountInactiveError()

    return Principal(subject_id=user.id, subject_type="SUPER_ADMIN", email=user.email)

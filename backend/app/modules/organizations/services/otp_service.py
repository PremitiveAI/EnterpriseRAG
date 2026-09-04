"""OTP issue and verification (docs/release-2/features/otp-authentication.md).

Release 2 does **no dispatch**. The code is recorded in the database and, on
development and staging, is the fixed value ``1111``. SMS and email arrive in
Release 3.

> 🔴 That makes Organization Admin login in Release 2 *not an authentication
> boundary*: anyone who knows an admin's email can log in. It is acceptable
> only because this deployment is localhost-only, and the guard in
> :func:`generate_code` is what stops it ever reaching production.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.core.error_codes import ErrorCode
from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.modules.organizations.models import IdentifierType, OrganizationAdmin
from app.modules.organizations.repositories.admin_repository import (
    OrganizationAdminRepository,
    OtpRepository,
    normalise_identifier,
)
from config.settings import settings

logger = get_logger(__name__)

STATIC_OTP = "1111"
OTP_LENGTH = 4
EXPIRY_MINUTES = 5
MAX_ATTEMPTS = 5

# Requests per identifier inside the expiry window. Prevents flooding one
# inbox, which is the abuse an attempt cap alone does not cover.
MAX_REQUESTS_PER_WINDOW = 5


class OtpError(AppError):
    """A failed verification.

    Its own code rather than the generic UNAUTHORIZED: a bad OTP is not "you
    need a token", and the route-enumeration test cannot tell those apart if
    they share a code.
    """

    status_code = 401
    error_code = ErrorCode.OTP_INVALID
    message = "That code is not valid."


@dataclass
class OtpIssue:
    expires_in_seconds: int
    # Returned ONLY outside production, so the login screen can show the fixed
    # development code instead of the admin guessing at it.
    development_code: str | None = None


@dataclass
class OtpVerification:
    admin: OrganizationAdmin


def generate_code() -> str:
    """The OTP for this environment.

    The static code is impossible in production. A misconfigured environment
    flag would otherwise mean anyone who knows an email address can log in as
    that administrator, so this fails loudly rather than degrading quietly.
    """
    if settings.is_production:
        return "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))

    if settings.ENVIRONMENT not in ("development", "staging"):
        raise RuntimeError(
            f"Refusing to issue a static OTP in environment "
            f"{settings.ENVIRONMENT!r}. Only development and staging may use it."
        )
    return STATIC_OTP


class OtpService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.admins = OrganizationAdminRepository(db)
        self.otps = OtpRepository(db)

    # ------------------------------------------------------------------ #

    def request(self, identifier: str, *, ip_address: str | None = None) -> OtpIssue:
        """Issue a code. Never reveals whether the identifier exists."""
        value, kind = normalise_identifier(identifier)
        if not value:
            raise OtpError("An email address or mobile number is required.")

        window_start = datetime.now(timezone.utc) - timedelta(minutes=EXPIRY_MINUTES)
        if self.otps.count_recent(value, since=window_start) >= MAX_REQUESTS_PER_WINDOW:
            raise AppError(
                "Too many codes requested. Try again shortly.",
                status_code=429,
                error_code=ErrorCode.RATE_LIMIT_EXCEEDED,
            )

        admin = self.admins.get_by_identifier(value)
        eligible = (
            admin is not None
            and admin.is_active
            and admin.organization is not None
            and admin.organization.is_active
        )

        code = generate_code()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=EXPIRY_MINUTES)

        # A row is written even when nothing matched. Short-circuiting would
        # make an unknown identifier measurably faster to reject than a known
        # one - an enumeration oracle - and this also records the probe.
        self.otps.create(
            identifier=value,
            identifier_type=kind,
            otp_hash=hash_password(code),
            expires_at=expires_at,
            organization_admin_id=admin.id if eligible else None,
            ip_address=ip_address,
        )
        self.db.commit()

        logger.info(
            "OTP issued",
            # The identifier is a personal identifier and the code is a
            # credential. Neither is logged (spec §39).
            extra={"identifier_type": str(kind), "matched": bool(eligible)},
        )

        return OtpIssue(
            expires_in_seconds=EXPIRY_MINUTES * 60,
            development_code=None if settings.is_production else code,
        )

    def verify(self, identifier: str, code: str) -> OtpVerification:
        value, _ = normalise_identifier(identifier)
        request = self.otps.newest_pending(value)

        if request is None:
            raise OtpError("That code has expired or was never issued.")

        if request.attempt_count >= MAX_ATTEMPTS:
            raise OtpError("Too many incorrect attempts. Request a new code.")

        # Increment BEFORE checking, and commit either way. Incrementing only on
        # failure would let a crash mid-verify hand back a free attempt.
        request.attempt_count += 1
        self.db.commit()

        supplied = (code or "").strip()
        if not supplied or not verify_password(supplied, request.otp_hash):
            raise OtpError()

        if request.organization_admin_id is None:
            # The code was correct for a row that matched no admin - only
            # reachable if someone guessed a code issued for an unknown
            # identifier. Same error, no information.
            raise OtpError()

        admin = self.admins.get(request.organization_admin_id)
        if admin is None or not admin.is_active:
            raise OtpError()
        if admin.organization is None or not admin.organization.is_active:
            raise AppError(
                "This organization is not active.",
                status_code=403,
                error_code=ErrorCode.FORBIDDEN,
            )

        request.is_verified = True
        self.admins.touch_login(admin)
        self.db.commit()

        logger.info(
            "OTP verified",
            extra={"organization_id": str(admin.organization_id)},
        )
        return OtpVerification(admin=admin)


def constant_time_equals(a: str, b: str) -> bool:
    """Kept for callers comparing plain codes; the hash path is already safe."""
    return hmac.compare_digest(a.encode(), b.encode())

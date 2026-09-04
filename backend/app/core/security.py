"""Password hashing and JWT issue/verify."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from app.core.exceptions import RefreshTokenInvalidError, TokenExpiredError, UnauthorizedError
from config.settings import settings

TokenType = Literal["access", "refresh"]

# Two authenticated subjects, and they are not interchangeable: a Super Admin
# token carries no organization, so organization routes reject it structurally
# rather than by a permission check.
SubjectType = Literal["SUPER_ADMIN", "ORG_ADMIN"]

# A pre-computed hash used to equalise timing when the user does not exist.
# Without it, an unknown email returns measurably faster than a wrong password
# and the difference leaks which accounts exist.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equalisation-placeholder", bcrypt.gensalt(rounds=12))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    ).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def waste_password_comparison() -> None:
    """Spend the same time as a real verification, for a non-existent user."""
    bcrypt.checkpw(b"timing-equalisation-placeholder", _DUMMY_HASH)


def _create_token(
    subject: str,
    token_type: TokenType,
    expires: timedelta,
    *,
    subject_type: SubjectType = "SUPER_ADMIN",
    organization_id: str | None = None,
) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    jti = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "subject_type": subject_type,
        "jti": jti,
        "iat": now,
        "exp": now + expires,
    }
    # The tenant lives in the SIGNED token and nowhere else. It is never read
    # from a path, query or body on an authenticated route
    # (docs/release-2/features/tenant-isolation.md).
    if organization_id is not None:
        payload["organization_id"] = organization_id

    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, jti


def create_access_token(
    user_id: str,
    *,
    subject_type: SubjectType = "SUPER_ADMIN",
    organization_id: str | None = None,
) -> str:
    token, _ = _create_token(
        user_id, "access", timedelta(minutes=settings.ACCESS_TOKEN_MINUTES),
        subject_type=subject_type, organization_id=organization_id,
    )
    return token


def create_refresh_token(
    user_id: str,
    *,
    subject_type: SubjectType = "SUPER_ADMIN",
    organization_id: str | None = None,
) -> tuple[str, str]:
    """Returns ``(token, jti)``. The jti is what the denylist stores."""
    return _create_token(
        user_id, "refresh", timedelta(days=settings.REFRESH_TOKEN_DAYS),
        subject_type=subject_type, organization_id=organization_id,
    )


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            leeway=30,  # tolerate modest clock skew
        )
    except jwt.ExpiredSignatureError as exc:
        if expected_type == "refresh":
            raise RefreshTokenInvalidError() from exc
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        if expected_type == "refresh":
            raise RefreshTokenInvalidError() from exc
        raise UnauthorizedError("Malformed token.") from exc

    if payload.get("type") != expected_type:
        # An access token must never be usable as a refresh token, or vice versa.
        if expected_type == "refresh":
            raise RefreshTokenInvalidError()
        raise UnauthorizedError("Wrong token type.")

    if not payload.get("sub"):
        raise UnauthorizedError("Token is missing a subject.")

    return payload

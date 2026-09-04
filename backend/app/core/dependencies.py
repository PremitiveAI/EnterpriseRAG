"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.modules.auth.models import User
from app.modules.auth.repositories.user_repository import UserRepository


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """The authenticated admin.

    AuthMiddleware has already validated the token and attached ``user_id``;
    this re-loads the row on the request's own session so handlers work with a
    live, attached instance.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise UnauthorizedError()

    user = UserRepository(db).get_by_id(user_id)
    if user is None:
        raise UnauthorizedError("User no longer exists.")
    return user


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)

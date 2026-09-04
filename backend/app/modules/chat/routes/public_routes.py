"""Public chatbot routes. The only unauthenticated endpoints in the system.

The organization id comes from the URL here - the single place in the
application where that is true - and it is paid for by restricting the corpus
to published documents. See public_chat_service.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.chat.schemas.public import (
    PublicChatRequest,
    PublicChatResponse,
    PublicConfigResponse,
    PublicSourceRef,
)
from app.modules.chat.services.public_chat_service import PublicChatService

router = APIRouter(prefix="/public", tags=["public"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.get("/{organization_id}/config", response_model=None)
def public_config(organization_id: UUID, db: Session = Depends(get_db)) -> dict:
    """What the widget needs to render. 404 when the chatbot is not available."""
    config = PublicChatService(db).config(organization_id)
    return {
        "success": True,
        "data": PublicConfigResponse(
            organization_name=config.organization_name, greeting=config.greeting
        ).model_dump(mode="json"),
    }


@router.post("/{organization_id}/chat", response_model=None)
def public_chat(
    organization_id: UUID,
    payload: PublicChatRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Ask a question against the organization's PUBLISHED documents.

    A grounded refusal returns 200, not an error - it is a correct outcome.
    """
    answer = PublicChatService(db).ask(
        organization_id,
        message=payload.message,
        session_id=payload.session_id,
        ip_address=_client_ip(request),
    )
    return {
        "success": True,
        "data": PublicChatResponse(
            answer=answer.answer,
            is_grounded=answer.is_grounded,
            session_id=answer.session_id,
            sources=[
                PublicSourceRef(document_name=s.document_name, page=s.page)
                for s in answer.sources
            ],
        ).model_dump(mode="json"),
    }

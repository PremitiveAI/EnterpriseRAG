"""Chat routes. Path definitions and dependency wiring only (spec §12)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.principal import current_org_admin
from app.modules.chat.controllers.chat_controller import ChatController
from app.modules.chat.schemas.chat import (
    CreateConversationRequest,
    RenameConversationRequest,
    SendMessageRequest,
)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/conversations", response_model=None)
def create_conversation(
    payload: CreateConversationRequest,
    db: Session = Depends(get_db),
    admin = Depends(current_org_admin),
) -> dict:
    return ChatController(db, admin).create(payload)


@router.get("/conversations", response_model=None)
def list_conversations(
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1),
    db: Session = Depends(get_db),
    admin = Depends(current_org_admin),
) -> dict:
    """Sidebar query. Empty conversations are excluded, so repeatedly clicking
    New Chat does not litter the list."""
    return ChatController(db, admin).list(search=search, page=page, page_size=page_size)


@router.get("/conversations/{conversation_id}", response_model=None)
def conversation_detail(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    admin = Depends(current_org_admin),
) -> dict:
    return ChatController(db, admin).detail(conversation_id)


@router.patch("/conversations/{conversation_id}", response_model=None)
def rename_conversation(
    conversation_id: UUID,
    payload: RenameConversationRequest,
    db: Session = Depends(get_db),
    admin = Depends(current_org_admin),
) -> dict:
    return ChatController(db, admin).rename(conversation_id, payload)


@router.delete("/conversations/{conversation_id}", response_model=None)
def delete_conversation(
    conversation_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin = Depends(current_org_admin),
) -> dict:
    """Soft delete. Messages and their citations survive."""
    return ChatController(db, admin).delete(conversation_id, request)


@router.post("/conversations/{conversation_id}/messages", response_model=None)
def send_message(
    conversation_id: UUID,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
    admin = Depends(current_org_admin),
) -> dict:
    """Ask a question.

    A grounded refusal returns 200 with `is_grounded: false` — that is a correct
    outcome, not an error (§34).
    """
    return ChatController(db, admin).ask(conversation_id, payload)

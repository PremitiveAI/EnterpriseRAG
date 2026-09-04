"""Translates chat requests into service calls (spec §12)."""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.modules.chat.schemas.chat import (
    CreateConversationRequest,
    RenameConversationRequest,
    SendMessageRequest,
)
from app.modules.chat.services.chat_service import ChatService
from app.modules.chat.services.conversation_service import ConversationService

MAX_PAGE_SIZE = 100


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


class ChatController:
    def __init__(self, db: Session, admin) -> None:
        """Bound to the calling Organization Admin and their tenant.

        Both come from the token via the dependency, so no handler has an
        opportunity to take an organization from the request.
        """
        self.db = db
        self.admin = admin
        self.organization_id = admin.organization_id
        self.conversations = ConversationService(db, admin.organization_id, admin.id)
        self.chat = ChatService(
            db, admin.organization_id, organization_admin_id=admin.id
        )

    # --- Conversations ------------------------------------------------- #

    def create(self, payload: CreateConversationRequest) -> dict:
        summary = self.conversations.create(payload.title)
        return {"success": True, "data": summary.model_dump(mode="json")}

    def list(self, *, search: str | None, page: int, page_size: int) -> dict:
        result = self.conversations.list(
            search=search or None,
            page=max(1, page),
            page_size=min(max(1, page_size), MAX_PAGE_SIZE),
        )
        return {"success": True, "data": result.model_dump(mode="json")}

    def detail(self, conversation_id: UUID) -> dict:
        detail = self.conversations.detail(conversation_id)
        return {"success": True, "data": detail.model_dump(mode="json")}

    def rename(
        self, conversation_id: UUID, payload: RenameConversationRequest
    ) -> dict:
        summary = self.conversations.rename(conversation_id, payload.title)
        return {"success": True, "message": "Conversation renamed.",
                "data": summary.model_dump(mode="json")}

    def delete(self, conversation_id: UUID, request: Request) -> dict:
        data = self.conversations.delete(
            conversation_id,
            request_id=getattr(request.state, "request_id", None),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
        )
        return {"success": True, "message": "Conversation deleted.", "data": data}

    # --- Messages ------------------------------------------------------ #

    def ask(self, conversation_id: UUID, payload: SendMessageRequest) -> dict:
        answer = self.chat.ask(conversation_id, payload.content)

        # A refusal is a SUCCESSFUL response (§34): success stays true, and
        # NO_RELEVANT_CONTEXT rides along inside the data.
        return {
            "success": True,
            "data": answer.model_dump(mode="json"),
        }

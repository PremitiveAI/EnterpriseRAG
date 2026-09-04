"""Conversation lifecycle (docs/features/conversation-management.md).

PostgreSQL alone is sufficient to reconstruct every conversation. Redis is
touched here only to drop a key on delete.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.modules.auth.models import AuditAction
from app.modules.auth.repositories.user_repository import AuditRepository
from app.modules.chat.models import Conversation
from app.modules.chat.repositories.conversation_repository import ConversationRepository
from app.modules.chat.schemas.chat import (
    ConversationDetail,
    ConversationListResponse,
    ConversationSummary,
    MessageResponse,
    SourceRef,
)
from app.modules.chat.services.context_service import ContextService
from app.modules.documents.models import Document

logger = get_logger(__name__)


class ConversationService:
    def __init__(self, db: Session, organization_id: UUID, organization_admin_id: UUID) -> None:
        """Bound to one tenant and one admin for the life of the request."""
        self.db = db
        self.organization_id = organization_id
        self.organization_admin_id = organization_admin_id
        self.conversations = ConversationRepository(db)
        self.context = ContextService(self.conversations)
        self.audit = AuditRepository(db)

    def create(self, title: str | None = None) -> ConversationSummary:
        conversation = self.conversations.create(
            self.organization_id,
            organization_admin_id=self.organization_admin_id,
            title=title,
        )
        self.db.commit()
        return self._summary(conversation)

    def list(
        self, *, search: str | None = None, page: int = 1, page_size: int = 50
    ) -> ConversationListResponse:
        items, total = self.conversations.list(
            self.organization_id,
            self.organization_admin_id,
            search=search, page=page, page_size=page_size,
        )
        return ConversationListResponse(
            items=[self._summary(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
        )

    def detail(self, conversation_id: UUID) -> ConversationDetail:
        conversation = self._require(conversation_id)
        messages = self.conversations.messages(conversation.id)

        # One lookup for every cited document, rather than one per chip.
        document_ids = {
            source.document_id for message in messages for source in message.sources
        }
        documents = {
            document.id: document
            for document in self.db.query(Document).filter(Document.id.in_(document_ids)).all()
        } if document_ids else {}

        return ConversationDetail(
            id=conversation.id,
            title=conversation.title,
            message_count=conversation.message_count,
            last_message_at=conversation.last_message_at,
            created_at=conversation.created_at,
            messages=[
                MessageResponse(
                    id=message.id,
                    role=str(message.role),
                    content=message.content,
                    is_grounded=message.is_grounded,
                    error_code=message.error_code,
                    latency_ms=message.latency_ms,
                    created_at=message.created_at,
                    sources=[
                        SourceRef(
                            document_id=source.document_id,
                            document_name=(
                                documents[source.document_id].original_file_name
                                if source.document_id in documents else "Deleted document"
                            ),
                            chunk_id=source.chunk_id,
                            page=source.page_number,
                            score=source.score,
                            rank=source.rank,
                            # The citation still resolves; the UI greys the chip
                            # rather than linking to a document that is gone.
                            document_deleted=(
                                source.document_id not in documents
                                or documents[source.document_id].deleted_at is not None
                            ),
                        )
                        for source in sorted(message.sources, key=lambda s: s.rank)
                    ],
                )
                for message in messages
            ],
        )

    def rename(self, conversation_id: UUID, title: str) -> ConversationSummary:
        conversation = self._require(conversation_id)
        self.conversations.rename(conversation, title)
        self.db.commit()
        return self._summary(conversation)

    def delete(self, conversation_id: UUID, **audit_context) -> dict:
        conversation = self._require(conversation_id)

        # Messages and message_sources survive: the record of what was cited
        # outlives the conversation it was cited in.
        self.conversations.soft_delete(conversation)
        self.audit.record(
            organization_id=self.organization_id,
            action=AuditAction.CONVERSATION_DELETED,
            entity_type="conversation",
            entity_id=conversation.id,
            user_id=self.organization_admin_id,
            **audit_context,
        )
        self.db.commit()

        self.context.drop(
            self.organization_id,
            f"admin:{self.organization_admin_id}",
            conversation.id,
        )

        return {"conversation_id": str(conversation.id), "deleted": True}

    # ------------------------------------------------------------------ #

    def _require(self, conversation_id: UUID) -> Conversation:
        conversation = self.conversations.get(
            self.organization_id, conversation_id,
            organization_admin_id=self.organization_admin_id,
        )
        if conversation is None:
            raise NotFoundError(
                "No conversation with that id.",
                error_code=ErrorCode.CONVERSATION_NOT_FOUND,
            )
        return conversation

    @staticmethod
    def _summary(conversation: Conversation) -> ConversationSummary:
        return ConversationSummary(
            id=conversation.id,
            title=conversation.title,
            message_count=conversation.message_count,
            last_message_at=conversation.last_message_at,
            created_at=conversation.created_at,
        )

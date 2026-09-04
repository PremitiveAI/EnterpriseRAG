"""Database access for conversations, messages and citations (spec §36).

PostgreSQL is the permanent source of truth here. Redis caches recent turns and
nothing else — every method in this file works with Redis switched off (§9).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.modules.chat.models import ChatMessage, Conversation, MessageRole, MessageSource

TITLE_MAX_CHARS = 60


def derive_title(first_message: str) -> str:
    """Truncate on a word boundary, so a title never ends mid-word."""
    cleaned = " ".join(first_message.split())
    if len(cleaned) <= TITLE_MAX_CHARS:
        return cleaned or "New conversation"

    cut = cleaned[:TITLE_MAX_CHARS]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return f"{cut}…"


class ConversationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Conversations ------------------------------------------------- #

    def create(
        self,
        organization_id: UUID,
        *,
        organization_admin_id: UUID | None = None,
        visitor_session_id: str | None = None,
        title: str | None = None,
    ) -> Conversation:
        """Owned by an admin OR by an anonymous visitor - never both, never
        neither. The database CHECK enforces it; this signature makes the
        choice explicit at the call site."""
        conversation = Conversation(
            organization_id=organization_id,
            organization_admin_id=organization_admin_id,
            visitor_session_id=visitor_session_id,
            title=title or "New conversation",
        )
        self.db.add(conversation)
        self.db.flush()
        return conversation

    def get(
        self,
        organization_id: UUID,
        conversation_id: UUID,
        *,
        organization_admin_id: UUID | None = None,
        visitor_session_id: str | None = None,
    ) -> Conversation | None:
        """Scoped to the tenant AND the owner.

        A colleague's conversation in the same organization is also a miss - the
        caller turns that into 404, so an id cannot be probed for existence.
        """
        stmt = (
            select(Conversation)
            .where(Conversation.organization_id == organization_id)
            .where(Conversation.id == conversation_id)
            .where(Conversation.deleted_at.is_(None))
        )
        if organization_admin_id is not None:
            stmt = stmt.where(Conversation.organization_admin_id == organization_admin_id)
        if visitor_session_id is not None:
            stmt = stmt.where(Conversation.visitor_session_id == visitor_session_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def list(
        self,
        organization_id: UUID,
        organization_admin_id: UUID,
        *,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Conversation], int]:
        stmt = (
            select(Conversation)
            .where(Conversation.organization_id == organization_id)
            .where(Conversation.organization_admin_id == organization_admin_id)
            .where(Conversation.deleted_at.is_(None))
            # An empty conversation is not history. "New Chat" clicked five
            # times must not litter the sidebar with five blank rows.
            .where(Conversation.message_count > 0)
        )
        if search:
            stmt = stmt.where(Conversation.title.ilike(f"%{search.strip()}%"))

        total = self.db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()

        stmt = stmt.order_by(
            Conversation.last_message_at.desc().nullslast(), Conversation.id.desc()
        ).offset((page - 1) * page_size).limit(page_size)

        return list(self.db.execute(stmt).scalars()), total

    def soft_delete(self, conversation: Conversation) -> None:
        # Messages and their sources are NOT deleted: the record of what was
        # cited stays intact even after the conversation is gone from the list.
        conversation.deleted_at = datetime.now(timezone.utc)
        self.db.flush()

    def rename(self, conversation: Conversation, title: str) -> None:
        conversation.title = title.strip()[:255] or conversation.title
        self.db.flush()

    # --- Messages ------------------------------------------------------ #

    def messages(self, conversation_id: UUID) -> list[ChatMessage]:
        """Messages with their citations in ONE query.

        selectinload rather than lazy loading: a 40-message conversation would
        otherwise issue 40 extra queries just to render its source chips.
        """
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .options(selectinload(ChatMessage.sources))
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
        return list(self.db.execute(stmt).scalars())

    def recent_turns(self, conversation_id: UUID, limit: int) -> list[ChatMessage]:
        """The last N messages, oldest-first.

        This is the Redis-miss path: the context is rebuilt from here, which is
        why losing Redis costs one query and nothing else (§9).
        """
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
        )
        return list(reversed(list(self.db.execute(stmt).scalars())))

    def add_message(
        self,
        conversation: Conversation,
        *,
        role: MessageRole,
        content: str,
        is_grounded: bool | None = None,
        latency_ms: int | None = None,
        retrieval_count: int | None = None,
        error_code: str | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            conversation_id=conversation.id,
            role=role,
            content=content,
            is_grounded=is_grounded,
            latency_ms=latency_ms,
            retrieval_count=retrieval_count,
            error_code=error_code,
        )
        self.db.add(message)

        # Denormalised, so the sidebar never counts rows.
        conversation.message_count = (conversation.message_count or 0) + 1
        conversation.last_message_at = datetime.now(timezone.utc)

        self.db.flush()
        return message

    def add_sources(self, message: ChatMessage, sources: list[dict]) -> list[MessageSource]:
        rows = [
            MessageSource(
                message_id=message.id,
                document_id=source["document_id"],
                chunk_id=source.get("chunk_id"),
                page_number=source.get("page_number"),
                score=source["score"],
                rank=index + 1,
            )
            for index, source in enumerate(sources)
        ]
        self.db.add_all(rows)
        self.db.flush()
        return rows

    def first_user_message(self, conversation_id: UUID) -> ChatMessage | None:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .where(ChatMessage.role == MessageRole.USER)
            .order_by(ChatMessage.created_at)
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def purge_empty(self, organization_id: UUID, organization_admin_id: UUID) -> int:
        """Remove conversations that never received a message.

        Lazy creation means a row exists from the moment New Chat is clicked;
        this is the sweep that stops those accumulating.
        """
        result = self.db.execute(
            delete(Conversation)
            .where(Conversation.organization_id == organization_id)
            .where(Conversation.organization_admin_id == organization_admin_id)
            .where(Conversation.message_count == 0)
            .where(or_(Conversation.last_message_at.is_(None), Conversation.message_count == 0))
        )
        return result.rowcount or 0

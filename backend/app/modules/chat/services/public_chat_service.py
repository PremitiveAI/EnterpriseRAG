"""The public chatbot (docs/release-2/features/public-chatbot.md).

The only unauthenticated path in the system. Two facts belong together and are
stated once here:

* the organization id comes from the URL, which is untrusted input, and
* the corpus is narrowed to ``is_public = true``.

That is not a coincidence. Anywhere the tenant is untrusted, the corpus must be
the published one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError, NotFoundError
from app.core.logging import get_logger
from app.core.rate_limit import check, parse_rule
from app.modules.chat.repositories.conversation_repository import ConversationRepository
from app.modules.chat.services.chat_service import ChatService
from app.modules.organizations.models import Organization
from app.modules.organizations.repositories.organization_repository import (
    OrganizationRepository,
)

logger = get_logger(__name__)

MAX_MESSAGE_CHARS = 1000
DEFAULT_PUBLIC_RATE_LIMIT = "20/minute"


@dataclass
class PublicSource:
    """What a visitor is allowed to see about a citation.

    Names and pages only - never document_id or chunk_id. Internal identifiers
    are useless to a visitor and give an attacker a map.
    """

    document_name: str
    page: int | None = None


@dataclass
class PublicAnswer:
    answer: str
    is_grounded: bool
    session_id: str
    sources: list[PublicSource] = field(default_factory=list)


@dataclass
class PublicConfig:
    organization_name: str
    greeting: str


class PublicChatService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.organizations = OrganizationRepository(db)
        self.conversations = ConversationRepository(db)

    # ------------------------------------------------------------------ #

    def resolve(self, organization_id: UUID) -> Organization:
        """The organization, or 404.

        Unknown, suspended and chat-disabled are deliberately indistinguishable:
        telling them apart would confirm which organization ids exist.
        """
        organization = self.organizations.get(organization_id)
        if (
            organization is None
            or not organization.is_active
            or not organization.public_chat_enabled
        ):
            raise NotFoundError(
                "No chatbot is available at this address.",
                error_code=ErrorCode.NOT_FOUND,
            )
        return organization

    def config(self, organization_id: UUID) -> PublicConfig:
        organization = self.resolve(organization_id)
        return PublicConfig(
            organization_name=organization.name,
            greeting=(
                organization.public_chat_greeting
                or f"Hi — ask me about {organization.name}'s published documents."
            ),
        )

    def ask(
        self,
        organization_id: UUID,
        *,
        message: str,
        session_id: str,
        ip_address: str | None = None,
    ) -> PublicAnswer:
        organization = self.resolve(organization_id)

        text = (message or "").strip()
        if not text:
            raise AppError("A question is required.", status_code=422,
                           error_code=ErrorCode.VALIDATION_ERROR)
        if len(text) > MAX_MESSAGE_CHARS:
            raise AppError(
                f"Questions are limited to {MAX_MESSAGE_CHARS} characters.",
                status_code=422, error_code=ErrorCode.VALIDATION_ERROR,
            )

        self._enforce_limits(organization, ip_address)

        conversation = self._conversation_for(organization, session_id)

        # public_only is set HERE, by the service that knows the caller is
        # anonymous. It is never a request parameter - a visitor cannot ask for
        # the private corpus by passing a flag.
        chat = ChatService(
            self.db,
            organization.id,
            visitor_session_id=session_id,
            public_only=True,
        )
        answer = chat.ask(conversation.id, text)

        logger.info(
            "Public chat answered",
            extra={
                "organization_id": str(organization.id),
                "grounded": answer.is_grounded,
                "sources": len(answer.sources),
            },
        )

        return PublicAnswer(
            answer=answer.answer,
            is_grounded=answer.is_grounded,
            session_id=session_id,
            # Deliberately projected down: the internal response carries ids
            # the visitor has no business seeing.
            sources=[
                PublicSource(document_name=s.document_name, page=s.page)
                for s in answer.sources
            ],
        )

    # --- Internals ------------------------------------------------------ #

    def _enforce_limits(self, organization: Organization, ip_address: str | None) -> None:
        """Two axes, because they stop different things.

        The per-organization limit bounds a tenant's own spend; the per-IP limit
        stops one abusive visitor consuming it.
        """
        rule = parse_rule(
            organization.rate_limit_public_chat or DEFAULT_PUBLIC_RATE_LIMIT,
            scope="ip",
        )

        for key in (
            f"public_chat:{organization.id}",
            f"public_chat_ip:{organization.id}:{ip_address or 'unknown'}",
        ):
            decision = check(key, rule)
            if not decision.allowed:
                raise AppError(
                    "Too many questions just now. Try again shortly.",
                    status_code=429,
                    error_code=ErrorCode.RATE_LIMIT_EXCEEDED,
                    details={"retry_after_seconds": decision.retry_after},
                )

    def _conversation_for(self, organization: Organization, session_id: str):
        """One conversation per visitor session, created on first question.

        Looked up by session rather than by id: a visitor has no conversation id
        to hand back, only the session token their browser generated.
        """
        from sqlalchemy import select

        from app.modules.chat.models import Conversation

        conversation = self.db.execute(
            select(Conversation)
            .where(Conversation.organization_id == organization.id)
            .where(Conversation.visitor_session_id == session_id)
            .where(Conversation.deleted_at.is_(None))
            .order_by(Conversation.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if conversation is None:
            conversation = self.conversations.create(
                organization.id,
                visitor_session_id=session_id,
                title="Website visitor",
            )
            self.db.commit()
        return conversation

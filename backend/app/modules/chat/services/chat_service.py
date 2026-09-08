"""The chat request path (spec §31-§36, docs/features/rag-chat.md).

Orchestration only. Retrieval, context and the two agents each live in their own
module; this decides the order and what happens when one of them fails.

The ordering that matters:

    validate → persist question → load context → AGENT 2 → validate filters
    → embed → search → nothing found? RETURN, AGENT 3 IS NOT CALLED
    → AGENT 3 → intersect citations → persist → respond
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.modules.ai import llm_config, query_planner, response_composer
from app.modules.chat.models import Conversation, MessageRole
from app.modules.chat.repositories.conversation_repository import (
    ConversationRepository,
    derive_title,
)
from app.modules.chat.schemas.chat import AnswerResponse, SourceRef
from app.modules.chat.services.context_service import ContextService, Turn
from app.modules.chat.services.retrieval_service import (
    RetrievalResult,
    RetrievalService,
    SearchUnavailable,
)
from app.modules.documents.models import DocumentCategory
from app.utils.pii import contains_identifier, mask_for_display
from config.settings import settings

logger = get_logger(__name__)

NOT_FOUND_ANSWER = response_composer.NOT_FOUND_ANSWER


@dataclass
class _Prepared:
    conversation: Conversation
    question: str


class ChatService:
    """Chat, bound to one organization and one caller.

    ``public_only`` is what distinguishes the visitor chatbot from an
    Organization Admin's own chat: an admin sees the whole corpus, a visitor
    sees only what has been published.
    """

    def __init__(
        self,
        db: Session,
        organization_id: UUID,
        *,
        organization_admin_id: UUID | None = None,
        visitor_session_id: str | None = None,
        public_only: bool = False,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.organization_admin_id = organization_admin_id
        self.visitor_session_id = visitor_session_id
        # Kept, not just forwarded: it selects the public corpus AND the brief,
        # short-timeout answer. One flag, because they are one decision - this
        # caller is an anonymous visitor.
        self.public_only = public_only
        self.conversations = ConversationRepository(db)
        self.context = ContextService(self.conversations)
        self.retrieval = RetrievalService(db, organization_id, public_only=public_only)

    # ------------------------------------------------------------------ #

    @property
    def context_subject(self) -> str:
        """The Redis context namespace for this caller.

        Admins and visitors share a key space, so the two are distinguished
        explicitly rather than by hoping their ids never collide.
        """
        if self.organization_admin_id is not None:
            return f"admin:{self.organization_admin_id}"
        return f"visitor:{self.visitor_session_id}"

    def ask(self, conversation_id: UUID, content: str) -> AnswerResponse:
        started = time.perf_counter()
        prepared = self._prepare(conversation_id, content)
        conversation, question = prepared.conversation, prepared.question

        # 2. Persist the question BEFORE anything can fail. A question that
        #    produced an error is still part of the conversation, and losing it
        #    would make the transcript lie about what was asked.
        user_message = self.conversations.add_message(
            conversation, role=MessageRole.USER, content=question
        )
        if conversation.message_count == 1:
            conversation.title = derive_title(question)
        self.db.commit()

        history = [turn.as_dict() for turn in 
                   self.context.load(
                       self.organization_id, 
                       self.context_subject, 
                       conversation.id
                    )
                ]

        # 3. Agent 2, but only when there is something for it to do.
        #
        #    The planner exists to resolve follow-ups: "what about for
        #    contractors?" embeds to nothing useful until it is rewritten
        #    against the previous turn. On the FIRST message of a conversation
        #    there is no previous turn, so it is being asked to rewrite a
        #    standalone question into a standalone question - a full LLM
        #    round-trip, up to 25 seconds, to hand back what it was given.
        #
        #    That first message is the one a website visitor is waiting on, so
        #    this is where the wait was worst and the work was least.
        if history:
            plan = query_planner.plan(
                question, history=history, taxonomy=self._taxonomy()
            )
        else:
            plan = query_planner.QueryPlan(search_query=question)

        # 4. Retrieve. Filters are validated inside the service — a proposed
        #    filter is never trusted (§33).
        try:
            retrieval = self.retrieval.search(
                plan.search_query, proposed_filters=plan.filters
            )
        except SearchUnavailable:
            # 503. The question is already persisted, so the transcript is intact.
            self.conversations.add_message(
                conversation, role=MessageRole.ASSISTANT,
                content="Document search is temporarily unavailable. Please try again.",
                is_grounded=False, error_code=str(ErrorCode.SEARCH_FAILED),
            )
            self.db.commit()
            raise

        # 5. Nothing above threshold: return the refusal directly. Agent 3 is
        #    NOT called — that removes the opportunity to answer unsupported.
        if not retrieval.found:
            return self._refuse(conversation, question, retrieval, started)

        # 6. Agent 3, then verify its citations against what was supplied.
        chunks = [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "document_name": chunk.document_name,
                "text": chunk.text,
                "page_number": chunk.page_number,
            }
            for chunk in retrieval.chunks
        ]
        composed = response_composer.compose(question, chunks, brief=self.public_only)

        # Identity numbers are masked before the answer is stored or returned
        # (docs/security/pii-handling.md § Display). The retrieved passages can
        # be OCR of a PAN or Aadhaar card, and both the composer and the
        # templated fallback quote them. Only values that VALIDATE are masked,
        # so an invoice number is not mangled into asterisks.
        answer_text = mask_for_display(composed.answer)
        touched_identity = contains_identifier(composed.answer)

        if not composed.is_grounded:
            return self._refuse(
                conversation, question, retrieval, started,
                answer=answer_text,
                degraded=composed.degraded or llm_config.is_serving_stale(),
            )

        sources = self._sources_for(retrieval, composed.cited_chunk_ids)
        latency = int((time.perf_counter() - started) * 1000)

        assistant = self.conversations.add_message(
            conversation, role=MessageRole.ASSISTANT, content=answer_text,
            is_grounded=True, latency_ms=latency, retrieval_count=len(retrieval.chunks),
        )
        self.conversations.add_sources(
            assistant,
            [
                {
                    "document_id": source.document_id,
                    "chunk_id": source.chunk_id,
                    "page_number": source.page,
                    "score": source.score,
                }
                for source in sources
            ],
        )
        self.db.commit()

        self.context.append(
            self.organization_id, self.context_subject, conversation.id,
            Turn(role=str(MessageRole.USER), content=question),
            Turn(role=str(MessageRole.ASSISTANT), content=answer_text),
        )

        logger.info(
            "Answered",
            extra={
                # The question and answer are never logged in production (§39).
                "conversation_id": str(conversation.id),
                "grounded": True,
                "retrieved": len(retrieval.chunks),
                "cited": len(sources),
                "degraded": composed.degraded or plan.degraded or llm_config.is_serving_stale(),
                # Recorded as a flag, never as the value itself (§39).
                "identity_masked": touched_identity,
                "latency_ms": latency,
                "filters_dropped": retrieval.dropped_filters,
            },
        )

        return AnswerResponse(
            message_id=assistant.id,
            conversation_id=conversation.id,
            answer=answer_text,
            is_grounded=True,
            sources=sources,
            latency_ms=latency,
            # A configuration that could not be re-confirmed is a degraded answer
            # too, even when both agents succeeded (ADR-010 §5).
            degraded=composed.degraded or plan.degraded or llm_config.is_serving_stale(),
        )

    # --- Steps --------------------------------------------------------- #

    def _prepare(self, conversation_id: UUID, content: str) -> _Prepared:
        conversation = self.conversations.get(
            self.organization_id,
            conversation_id,
            organization_admin_id=self.organization_admin_id,
            visitor_session_id=self.visitor_session_id,
        )
        if conversation is None:
            raise NotFoundError(
                "No conversation with that id.",
                error_code=ErrorCode.CONVERSATION_NOT_FOUND,
            )

        question = (content or "").strip()
        if not question:
            raise ValidationError(
                "A message cannot be empty.",
                error_code=ErrorCode.MESSAGE_EMPTY,
                status_code=400,
            )
        if len(question) > settings.MAX_MESSAGE_CHARS:
            raise ValidationError(
                f"A message may be at most {settings.MAX_MESSAGE_CHARS} characters.",
                error_code=ErrorCode.MESSAGE_TOO_LONG,
                status_code=400,
                details={"limit": settings.MAX_MESSAGE_CHARS, "submitted": len(question)},
            )

        return _Prepared(conversation=conversation, question=question)

    # def _taxonomy(self) -> list[str]:
    #     rows = self.db.execute(
    #         select(DocumentCategory.slug, DocumentCategory.description).where(DocumentCategory.is_active.is_(True))
    #     ).scalars()
    #     return list(rows)
    def _taxonomy(self) -> list[str]:
        rows = self.db.execute(
            select(DocumentCategory.slug, DocumentCategory.description)
            .where(DocumentCategory.is_active.is_(True))
        ).all()
        return [f"{row.slug}: {row.description}" for row in rows]

    def _refuse(
        self,
        conversation: Conversation,
        question: str,
        retrieval: RetrievalResult,
        started: float,
        *,
        answer: str | None = None,
        degraded: bool = False,
    ) -> AnswerResponse:
        """§34's refusal. A correct outcome, so it carries HTTP 200."""
        text = answer or NOT_FOUND_ANSWER
        latency = int((time.perf_counter() - started) * 1000)

        message = self.conversations.add_message(
            conversation, role=MessageRole.ASSISTANT, content=text,
            is_grounded=False, latency_ms=latency,
            retrieval_count=len(retrieval.chunks),
            # Accompanies a SUCCESSFUL response — the one code that does.
            error_code=str(ErrorCode.NO_RELEVANT_CONTEXT),
        )
        self.db.commit()

        self.context.append(
            self.organization_id, self.context_subject, conversation.id,
            Turn(role=str(MessageRole.USER), content=question),
            Turn(role=str(MessageRole.ASSISTANT), content=text),
        )

        logger.info(
            "Refused for lack of grounding",
            extra={
                "conversation_id": str(conversation.id),
                "retrieved": len(retrieval.chunks),
                "filters_dropped": retrieval.dropped_filters,
            },
        )

        return AnswerResponse(
            message_id=message.id,
            conversation_id=conversation.id,
            answer=text,
            is_grounded=False,
            sources=[],
            latency_ms=latency,
            error_code=str(ErrorCode.NO_RELEVANT_CONTEXT),
            degraded=degraded,
        )

    def _sources_for(
        self, retrieval: RetrievalResult, cited_chunk_ids: list[str]
    ) -> list[SourceRef]:
        """Build citations from chunks that were actually retrieved AND cited.

        Document ids are resolved against the database first: a payload can
        outlive its row, and `message_sources.document_id` is a foreign key.
        """
        from app.modules.chat.services.retrieval_service import resolve_document_ids

        live = resolve_document_ids(self.db, retrieval.chunks)
        cited = set(cited_chunk_ids)

        sources: list[SourceRef] = []
        rank = 0
        for chunk in retrieval.chunks:
            if chunk.chunk_id not in cited:
                continue
            document_id = live.get(chunk.document_id)
            if document_id is None:
                logger.warning("Citation dropped — document row is gone",
                               extra={"document_id": chunk.document_id})
                continue

            rank += 1
            sources.append(
                SourceRef(
                    document_id=document_id,
                    document_name=chunk.document_name,
                    chunk_id=self._as_uuid(chunk.chunk_id),
                    page=chunk.page_number,
                    section=chunk.section,
                    score=round(chunk.score, 4),
                    rank=rank,
                )
            )
        return sources

    @staticmethod
    def _as_uuid(value: str) -> UUID | None:
        try:
            return UUID(value)
        except (ValueError, AttributeError, TypeError):
            return None

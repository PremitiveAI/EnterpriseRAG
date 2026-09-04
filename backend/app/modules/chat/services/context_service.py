"""Recent conversation context (spec §36, docs/features/conversation-management.md).

Redis holds the last N turns so a follow-up does not need a database read.
**A miss is not an error.** On a miss the turns are rebuilt from PostgreSQL and
the key repopulated — the system is fully correct with Redis switched off, only
slower by one query (§9).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

import redis

from app.cache.redis_client import get_redis
from app.core.logging import get_logger
from app.modules.chat.models import MessageRole
from app.modules.chat.repositories.conversation_repository import ConversationRepository
from config.settings import settings

logger = get_logger(__name__)


@dataclass
class Turn:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def key_for(organization_id: UUID, subject: str, conversation_id: UUID) -> str:
    """``chat:{organization}:{subject}:{conversation}``.

    Namespaced by tenant so a key-collision bug cannot serve one organization's
    conversation context to another. ``subject`` is ``admin:<id>`` or
    ``visitor:<session>``.
    """
    return f"chat:{organization_id}:{subject}:{conversation_id}"


class ContextService:
    def __init__(self, conversations: ConversationRepository) -> None:
        self.conversations = conversations

    def load(self, organization_id: UUID, subject: str, conversation_id: UUID) -> list[Turn]:
        cached = self._read_cache(organization_id, subject, conversation_id)
        if cached is not None:
            return cached

        turns = self._from_database(conversation_id)
        self._write_cache(organization_id, subject, conversation_id, turns)
        return turns

    def append(self, organization_id: UUID, subject: str, conversation_id: UUID, *new_turns: Turn) -> None:
        turns = self.load(organization_id, subject, conversation_id)
        turns.extend(new_turns)
        # Trimmed on write, so the key can never grow past the window even if
        # the setting is lowered later.
        self._write_cache(organization_id, subject, conversation_id, turns[-self._window() :])

    def drop(self, organization_id: UUID, subject: str, conversation_id: UUID) -> None:
        try:
            get_redis().delete(key_for(organization_id, subject, conversation_id))
        except redis.RedisError as exc:
            # A stale key expires within the TTL and only affects a deleted
            # conversation, so this must not fail the delete.
            logger.warning("Could not drop chat context", extra={"error": str(exc)})

    # --- Internals ----------------------------------------------------- #

    def _window(self) -> int:
        return max(1, settings.CHAT_CONTEXT_TURNS)

    def _from_database(self, conversation_id: UUID) -> list[Turn]:
        messages = self.conversations.recent_turns(conversation_id, self._window())
        return [
            Turn(role=str(message.role), content=message.content)
            for message in messages
            if message.role in (MessageRole.USER, MessageRole.ASSISTANT)
        ]

    def _read_cache(
        self, organization_id: UUID, subject: str, conversation_id: UUID) -> list[Turn] | None:
        try:
            raw = get_redis().get(key_for(organization_id, subject, conversation_id))
        except redis.RedisError as exc:
            logger.warning("Chat context unavailable; rebuilding from PostgreSQL",
                           extra={"error": str(exc)})
            return None

        if raw is None:
            return None

        try:
            payload = json.loads(raw)
            return [Turn(role=item["role"], content=item["content"]) for item in payload]
        except (json.JSONDecodeError, KeyError, TypeError):
            # A corrupt key is treated as a miss rather than an error: the
            # authoritative copy is one query away.
            logger.warning("Discarding malformed chat context")
            return None

    def _write_cache(
        self, organization_id: UUID, subject: str, conversation_id: UUID, turns: list[Turn]) -> None:
        try:
            get_redis().setex(
                key_for(organization_id, subject, conversation_id),
                settings.CHAT_CONTEXT_TTL_SECONDS,
                json.dumps([turn.as_dict() for turn in turns]),
            )
        except redis.RedisError as exc:
            logger.warning("Could not cache chat context", extra={"error": str(exc)})

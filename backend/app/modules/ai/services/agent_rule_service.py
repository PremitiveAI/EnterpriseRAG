"""Super Admin agent rule management (docs/ai/agent-rules.md, ADR-009).

The rules live in files, so this service has no repository and touches the
database exactly once per save - to write the audit row. That row is the whole
compensating control for the feature: a Super Admin can change how every
tenant's chat behaves without a code review, so the guarantee is not that they
cannot, it is that they cannot do it silently.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.modules.ai import agent_rules
from app.modules.ai.schemas.agent_rule import (
    AgentRuleDetail,
    AgentRuleListResponse,
    AgentRuleSummary,
)
from app.modules.auth.models import ActorType
from app.modules.auth.repositories.user_repository import AuditRepository

logger = get_logger(__name__)

AGENT_RULE_UPDATED = "agent_rule.updated"


class AgentRuleService:
    def __init__(self, db: Session, super_admin_id: UUID) -> None:
        self.db = db
        self.super_admin_id = super_admin_id
        self.audit = AuditRepository(db)

    def list(self) -> AgentRuleListResponse:
        return AgentRuleListResponse(
            items=[AgentRuleSummary(**agent) for agent in agent_rules.describe()]
        )

    def detail(self, agent_key: str) -> AgentRuleDetail:
        spec = self._require(agent_key)
        content = agent_rules.load(agent_key)
        return AgentRuleDetail(
            agent_key=spec.key,
            name=spec.name,
            role=spec.role,
            is_custom=content is not None,
            content=content or "",
            default_content=agent_rules.default_guidance(agent_key),
            locked_text=agent_rules.locked_text(agent_key),
        )

    def save(self, agent_key: str, content: str) -> AgentRuleDetail:
        self._require(agent_key)

        try:
            stored = agent_rules.save(agent_key, content)
        except agent_rules.RuleValidationError as exc:
            raise ValidationError(str(exc),
                                  error_code=ErrorCode.AGENT_RULE_INVALID) from exc
        except OSError as exc:
            # The write is atomic, so the previous rule is still in effect and
            # the caller can safely retry. Say so rather than leaving them to
            # guess whether half a prompt is now live.
            logger.exception("Agent rule write failed",
                             extra={"agent_key": agent_key})
            raise AppError(
                "The rule could not be saved. The previous rule is still in "
                "effect, so it is safe to try again.",
                status_code=500,
                error_code=ErrorCode.AGENT_RULE_WRITE_FAILED,
            ) from exc

        # Length, never content: the rule is concatenated with document text
        # before it reaches the model, and prompts are not logged or audited
        # (§39). "Reset to default" is recorded as its own shape of event.
        self.audit.record(
            action=AGENT_RULE_UPDATED,
            entity_type="agent_rule",
            user_id=self.super_admin_id,
            actor_type=ActorType.SUPER_ADMIN,
            metadata={
                "agent_key": agent_key,
                "chars": len(stored),
                "reset_to_default": stored == "",
            },
        )
        self.db.commit()

        return self.detail(agent_key)

    def _require(self, agent_key: str):
        spec = agent_rules.REGISTRY.get(agent_key)
        if spec is None:
            # Not a filesystem lookup. An unregistered key never becomes a path,
            # which is why traversal is unrepresentable here rather than filtered.
            raise NotFoundError(
                "That agent does not exist, or its rules are not editable.",
                error_code=ErrorCode.AGENT_NOT_FOUND,
            )
        return spec

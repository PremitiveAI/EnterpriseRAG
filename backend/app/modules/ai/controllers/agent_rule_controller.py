"""Translates Super Admin agent rule requests into service calls (spec §12)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.ai.schemas.agent_rule import SaveAgentRuleRequest
from app.modules.ai.services.agent_rule_service import AgentRuleService


class AgentRuleController:
    def __init__(self, db: Session, super_admin_id: UUID) -> None:
        self.service = AgentRuleService(db, super_admin_id)

    def list(self) -> dict:
        return {"success": True, "data": self.service.list().model_dump(mode="json")}

    def detail(self, agent_key: str) -> dict:
        return {"success": True,
                "data": self.service.detail(agent_key).model_dump(mode="json")}

    def save(self, agent_key: str, payload: SaveAgentRuleRequest) -> dict:
        detail = self.service.save(agent_key, payload.content)
        # The message distinguishes the two outcomes, because "Saved." after
        # clearing the box would leave a Super Admin unsure whether the agent
        # now has no instructions or its original ones.
        message = (
            "Rule saved. It applies to the next chat message."
            if detail.is_custom
            else "Rule cleared. The built-in prompt is running again."
        )
        return {"success": True, "message": message,
                "data": detail.model_dump(mode="json")}

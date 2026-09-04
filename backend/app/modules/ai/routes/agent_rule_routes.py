"""Super Admin agent rule routes (ADR-009, docs/ai/agent-rules.md §7).

Mounted under ``/super-admin`` alongside organizations and categories. Rules are
global, so there is no organization in any path here and no tenant scoping: an
Organization Admin editing them would be editing them for every other tenant.

**There is no DELETE handler, and the absence is the design.** Saving empty
content restores the built-in prompt, which is the only thing a delete would
have done. A second route to the same state is a second thing to authorize.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.principal import Principal, require_super_admin
from app.modules.ai.controllers.agent_rule_controller import AgentRuleController
from app.modules.ai.schemas.agent_rule import SaveAgentRuleRequest

router = APIRouter(prefix="/super-admin", tags=["super-admin"])


def controller(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_super_admin),
) -> AgentRuleController:
    return AgentRuleController(db, principal.subject_id)


@router.get("/agent-rules", response_model=None)
def list_agent_rules(ctrl: AgentRuleController = Depends(controller)) -> dict:
    """Every editable agent, and whether a custom rule is in effect."""
    return ctrl.list()


@router.get("/agent-rules/{agent_key}", response_model=None)
def agent_rule_detail(
    agent_key: str, ctrl: AgentRuleController = Depends(controller)
) -> dict:
    """The rule, the built-in default, and the text code always appends."""
    return ctrl.detail(agent_key)


@router.put("/agent-rules/{agent_key}", response_model=None)
def save_agent_rule(
    agent_key: str,
    payload: SaveAgentRuleRequest,
    ctrl: AgentRuleController = Depends(controller),
) -> dict:
    """Create or replace. Empty content restores the built-in prompt."""
    return ctrl.save(agent_key, payload)

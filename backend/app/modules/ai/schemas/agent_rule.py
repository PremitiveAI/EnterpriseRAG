"""Agent rule contracts (docs/ai/agent-rules.md §7).

No path, no filename, no directory appears in any of these. The client works in
agent keys; where a rule lives is the application's business.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentRuleSummary(BaseModel):
    agent_key: str
    name: str
    role: str
    # False means the shipped prompt is running, not that something is missing.
    is_custom: bool = False


class AgentRuleListResponse(BaseModel):
    items: list[AgentRuleSummary]


class AgentRuleDetail(AgentRuleSummary):
    # "" when no rule is set. The editor opens empty and the default runs.
    content: str = ""
    # What runs when content is empty, so the page can show it rather than
    # leaving an empty box that implies the agent has no instructions.
    default_content: str = ""
    # 🔴 Appended by code on every call and unreachable from this API. Returned
    # so the page can display it read-only: a Super Admin who cannot see the
    # part they may not change will try to write their own.
    locked_text: str = ""


class SaveAgentRuleRequest(BaseModel):
    """Empty content is legal, and is how an agent is reset to its default.

    That is also why there is no DELETE: it would be a second route to a state
    this one already reaches.
    """

    content: str = Field(default="", max_length=20_000)

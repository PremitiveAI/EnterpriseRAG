"""Agent 2 — Query Planner (docs/ai/agents.md).

Turns a conversational question into a retrieval query and proposes metadata
filters. It earns its place on the synchronous path because follow-ups are
unretrievable as written: *"What about for contractors?"* embeds to nothing
useful, but resolved against the previous turn it becomes *"leave policy
entitlement contractors"* and retrieves correctly.

It is blocking, so it has a hard timeout and a **non-agent fallback**: search
the raw question with no filters. For a well-formed standalone question that
fallback is equivalent, not degraded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.modules.ai import agent_rules, crew
from config.settings import settings

logger = get_logger(__name__)


AGENT_KEY = "query_planner"

TEMPERATURE = 0.0
MAX_QUESTION_CHARS = 2000
CONTEXT_TURNS_SHOWN = 4


@dataclass
class QueryPlan:
    search_query: str
    filters: dict[str, str] = field(default_factory=dict)
    intent: str | None = None
    # True when the agent did not produce a plan and the raw question is used.
    degraded: bool = False


ROLE = "Retrieval Query Planner"
GOAL = (
    "Rewrite a user's question into a standalone search query that will retrieve "
    "the right passages from an enterprise document corpus."
)
BACKSTORY = (
    "You are a search specialist inside a document management system. You never "
    "answer questions yourself. You only prepare the query that retrieval will "
    "use, and you resolve pronouns and elliptical follow-ups against the "
    "conversation so far."
)


# The editable block. A Super Admin's rule file replaces exactly this text and
# nothing else (ADR-009). Everything around it - the transcript, the question,
# the slug list and the JSON contract below - is assembled by code.
DEFAULT_GUIDANCE = """Rewrite the new question as a standalone search query. If it refers to the
conversation ("that policy", "what about contractors"), resolve the reference so
the query stands alone.

Propose a filter ONLY when the question names it explicitly. A guessed filter
eliminates every result, which is far worse than no filter at all. Use null when
unsure. `category_slug` must be one of the slugs listed above, exactly."""

# 🔴 Appended after the guidance on every call, editable or not. Without it the
# agent stops returning parseable JSON, `plan()` falls back to the raw question,
# and retrieval quietly gets worse with no error anywhere to notice it by.
OUTPUT_CONTRACT = """Return JSON only:
{"search_query": "...", "filters": {"category_slug": null, "language": null,
  "document_type": null}, "intent": "..."}"""


def _prompt(question: str, history: list[dict[str, str]], taxonomy: list[str]) -> str:
    recent = history[-CONTEXT_TURNS_SHOWN:]
    transcript = (
        "\n".join(f"{turn['role']}: {turn['content'][:400]}" for turn in recent)
        or "(this is the first message)"
    )
    slugs = ", ".join(taxonomy) if taxonomy else "(no categories configured)"
    guidance = agent_rules.load(AGENT_KEY) or DEFAULT_GUIDANCE

    return f"""Conversation so far:
{transcript}

New question: {question}

Available category slugs: {slugs}

{guidance}

{OUTPUT_CONTRACT}"""


def plan(
    question: str, *, history: list[dict[str, str]] | None = None,
    taxonomy: list[str] | None = None,
) -> QueryPlan:
    """Never raises. A failure here means retrieval uses the raw question."""
    raw = (question or "").strip()[:MAX_QUESTION_CHARS]
    fallback = QueryPlan(search_query=raw, degraded=True)

    if not raw:
        return fallback

    result = crew.run_json(
        role=ROLE,
        goal=GOAL,
        backstory=BACKSTORY,
        task=_prompt(raw, history or [], taxonomy or []),
        expected_output='JSON: {"search_query": str, "filters": object, "intent": str}',
        timeout=settings.AGENT_PLAN_TIMEOUT_SECONDS,
        temperature=TEMPERATURE,
        retries=settings.AGENT_PLAN_RETRIES,
    )

    if not isinstance(result, dict):
        logger.info("Query planner unavailable; searching the raw question")
        return fallback

    search_query = str(result.get("search_query") or "").strip()
    if not search_query:
        return fallback

    filters = result.get("filters")
    proposed: dict[str, str] = {}
    if isinstance(filters, dict):
        for key in ("category_slug", "language", "document_type"):
            value = filters.get(key)
            if isinstance(value, str) and value.strip() and value.strip().lower() != "null":
                proposed[key] = value.strip()

    intent = result.get("intent")
    return QueryPlan(
        search_query=search_query[:MAX_QUESTION_CHARS],
        filters=proposed,
        intent=intent if isinstance(intent, str) else None,
    )

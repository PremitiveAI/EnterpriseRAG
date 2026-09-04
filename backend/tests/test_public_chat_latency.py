"""The public chatbot's latency budget.

A website visitor is not a signed-in admin who knows a query is running. They
are watching a spinner in a 400px panel, and they leave. The authenticated
budget allowed 25s of planning plus two 60s composing attempts - 145 seconds,
against a browser proxy that gave up at 60 - so the visitor was shown "that took
too long to answer" for an answer that was still being written.

Three things fixed it, and each is pinned here because each is easy to undo by
accident:

1. The query planner does not run when there is no history to resolve against.
2. The public path composes once, briefly, on a short timeout.
3. The proxy's timeout exceeds the server's worst case instead of sitting inside
   it.
"""

from __future__ import annotations

from app.modules.ai import query_planner, response_composer
from config.settings import settings


# --- The budget itself ----------------------------------------------------- #


def test_the_public_budget_fits_inside_the_browsers():
    """The number that matters, stated as an assertion rather than a comment.

    PROXY_TIMEOUT mirrors the 40s in the frontend's public chat route. If the
    server's worst case ever grows past it, a visitor sees a timeout instead of
    an answer - which is exactly the bug this suite exists for.
    """
    PROXY_TIMEOUT_SECONDS = 40

    compose_attempts = settings.PUBLIC_AGENT_COMPOSE_RETRIES + 1
    worst_case = settings.PUBLIC_AGENT_COMPOSE_TIMEOUT_SECONDS * compose_attempts

    assert worst_case < PROXY_TIMEOUT_SECONDS, (
        f"the server can spend {worst_case}s but the proxy gives up at "
        f"{PROXY_TIMEOUT_SECONDS}s - the visitor would be told it took too long"
    )


def test_the_public_path_is_faster_than_the_authenticated_one():
    assert (
        settings.PUBLIC_AGENT_COMPOSE_TIMEOUT_SECONDS
        < settings.AGENT_COMPOSE_TIMEOUT_SECONDS
    )
    assert settings.PUBLIC_AGENT_COMPOSE_RETRIES <= settings.AGENT_COMPOSE_RETRIES
    assert settings.PUBLIC_RETRIEVAL_TOP_K <= settings.RETRIEVAL_TOP_K


# --- Brief mode ------------------------------------------------------------ #


def _capture(monkeypatch) -> dict:
    """Record what compose() hands to the agent, without calling one."""
    seen: dict = {}

    def fake_run_json(**kwargs):
        seen.update(kwargs)
        return {"answer": "Twenty-four days.", "is_grounded": True,
                "cited_chunk_ids": ["c1"]}

    monkeypatch.setattr(response_composer.crew, "run_json", fake_run_json)
    return seen


CHUNKS = [{"chunk_id": "c1", "document_id": "d1", "document_name": "Leave.pdf",
           "text": "Employees receive 24 days of annual leave.", "page_number": 2}]


def test_brief_mode_uses_the_short_timeout_and_no_retry(monkeypatch):
    seen = _capture(monkeypatch)

    response_composer.compose("How much leave?", CHUNKS, brief=True)

    assert seen["timeout"] == settings.PUBLIC_AGENT_COMPOSE_TIMEOUT_SECONDS
    assert seen["retries"] == settings.PUBLIC_AGENT_COMPOSE_RETRIES


def test_the_authenticated_path_keeps_its_full_budget(monkeypatch):
    seen = _capture(monkeypatch)

    response_composer.compose("How much leave?", CHUNKS)

    assert seen["timeout"] == settings.AGENT_COMPOSE_TIMEOUT_SECONDS
    assert seen["retries"] == settings.AGENT_COMPOSE_RETRIES


def test_brief_mode_asks_for_a_short_answer(monkeypatch):
    seen = _capture(monkeypatch)

    response_composer.compose("How much leave?", CHUNKS, brief=True)

    assert "at most three sentences" in seen["task"]


def test_brief_mode_REPLACES_the_format_menu_rather_than_appending_to_it(monkeypatch):
    """Appending would leave the model holding six shapes and one instruction to
    be brief, and the menu wins."""
    seen = _capture(monkeypatch)

    response_composer.compose("How much leave?", CHUNKS, brief=True)

    assert "gets a Markdown table" not in seen["task"]


def test_brief_mode_does_not_relax_a_single_grounding_rule(monkeypatch):
    """Speed is bought from the format and the timeout, never from the rules
    that stop an ungrounded answer."""
    seen = _capture(monkeypatch)

    response_composer.compose("How much leave?", CHUNKS, brief=True)

    task = seen["task"]
    assert "Use ONLY the passages above" in task
    assert "Do not invent facts" in task
    assert "Cite only ids from the list above" in task


def test_brief_mode_still_drops_invented_citations(monkeypatch):
    """The verification step is not a formatting concern and must survive."""
    monkeypatch.setattr(
        response_composer.crew, "run_json",
        lambda **_: {"answer": "Twenty-four days.", "is_grounded": True,
                     "cited_chunk_ids": ["c1", "not-a-real-id"]},
    )

    result = response_composer.compose("How much leave?", CHUNKS, brief=True)

    assert result.cited_chunk_ids == ["c1"]


# --- Skipping the planner -------------------------------------------------- #


def test_the_planner_is_still_used_when_there_is_history_to_resolve(monkeypatch):
    """The skip must not cost follow-ups their rewrite - that is the planner's
    whole reason to be on the synchronous path."""
    calls: list[str] = []

    def fake_run_json(**kwargs):
        calls.append(kwargs["task"])
        return {"search_query": "leave policy entitlement contractors", "filters": {}}

    monkeypatch.setattr(query_planner.crew, "run_json", fake_run_json)

    plan = query_planner.plan(
        "What about for contractors?",
        history=[{"role": "user", "content": "How much annual leave do I get?"}],
        taxonomy=["hr-policies"],
    )

    assert len(calls) == 1
    assert plan.search_query == "leave policy entitlement contractors"
    assert plan.degraded is False

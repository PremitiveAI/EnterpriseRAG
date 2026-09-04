"""CrewAI wiring (ADR-002, docs/ai/agents.md).

One entry point — `run_json` — used by agents 2 and 3. Everything the agents
differ in (role, goal, backstory, task, timeout, temperature) is a parameter,
so the framework is configured in exactly one place.

Three properties this module guarantees to its callers:

* **It never raises.** Every failure returns None and the caller falls back to
  its documented non-agent path. A failed agent must never mean a failed chat.
* **It never blocks past its timeout.** Agents 2 and 3 sit on the synchronous
  request path, so an unbounded call would hang a user's request.
* **It never logs prompts.** They contain document text (§39).

CrewAI is imported lazily. It pulls a large dependency tree, and the API and
the Celery worker must both start even when it is absent or broken.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

from app.core.logging import get_logger
from app.modules.ai import llm_client
from app.modules.ai.llm_client import clean_json

logger = get_logger(__name__)

# One shared pool. A new thread per message would be wasteful on a path that is
# already latency-sensitive.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="crew")


def is_available() -> bool:
    """True when CrewAI is installed AND a provider resolves.

    The provider now comes from the database (ADR-010), so this answers a
    different question than it used to: not "is a key in .env" but "is one
    active, readable and decryptable right now".
    """
    if not llm_client.is_configured():
        return False
    try:
        import crewai  # noqa: F401
    except Exception:
        return False
    return True


def _llm(temperature: float, timeout: int):
    """The client for the currently active provider.

    Not cached here. ``llm_client.build_llm`` caches on the resolved config's
    identity, so this is a lookup while the version holds and a rebuild the
    moment a Super Admin switches provider — with no restart and no
    invalidation call anywhere.
    """
    return llm_client.build_llm(llm_client.resolve(), temperature, timeout)


def _kickoff(
    *, role: str, goal: str, backstory: str, task: str, expected_output: str,
    temperature: float, timeout: int,
) -> str:
    from crewai import Agent, Crew, Task

    agent = Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=_llm(temperature, timeout),
        # No delegation: a single-agent crew that can delegate will sometimes
        # spend a turn deciding not to, which is pure latency on a blocking path.
        allow_delegation=False,
        verbose=False,
        max_iter=2,
        max_execution_time=timeout,
    )
    crew = Crew(
        agents=[agent],
        tasks=[Task(description=task, expected_output=expected_output, agent=agent)],
        verbose=False,
    )
    result = crew.kickoff()
    return getattr(result, "raw", None) or str(result)


def run_json(
    *,
    role: str,
    goal: str,
    backstory: str,
    task: str,
    expected_output: str,
    timeout: int,
    temperature: float = 0.0,
    retries: int = 1,
) -> Any | None:
    """Run one agent and parse its JSON reply. Returns None on any failure."""
    if not is_available():
        logger.info("CrewAI unavailable; the caller will use its fallback",
                    extra={"agent_role": role})
        return None

    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        future = _executor.submit(
            _kickoff,
            role=role, goal=goal, backstory=backstory, task=task,
            expected_output=expected_output, temperature=temperature, timeout=timeout,
        )
        try:
            raw = future.result(timeout=timeout)
        except FutureTimeout:
            # The thread cannot be killed and will finish in the background;
            # its result is simply discarded. Bounded by max_execution_time.
            future.cancel()
            logger.warning("Agent timed out",
                           extra={"agent_role": role, "timeout_s": timeout,
                                  "attempt": attempt + 1})
            continue
        except Exception as exc:
            # Never log the prompt or the exception message: both can contain
            # document text (§39).
            logger.warning("Agent failed",
                           extra={"agent_role": role, "error_type": type(exc).__name__,
                                  "attempt": attempt + 1})
            continue

        parsed = clean_json(raw or "")
        if parsed is not None:
            return parsed

        logger.warning("Agent returned unparseable output",
                       extra={"agent_role": role, "attempt": attempt + 1})

    return None


def describe() -> dict[str, object]:
    """Diagnostics for /health. Never returns the key itself."""
    installed = False
    version = None
    try:
        import crewai

        installed = True
        version = getattr(crewai, "__version__", None)
    except Exception:
        pass

    return {
        "crewai_installed": installed,
        "crewai_version": version,
        # The active provider, its model and the id actually handed to CrewAI.
        # Both ids are reported because they differ and a mismatch between them
        # is invisible otherwise: a doubled prefix once made every agent call
        # fail while this endpoint still reported the agents as enabled.
        "provider": llm_client.describe(),
        # "Configured", not "working". Nothing here proves a call succeeds —
        # only a real request does. See `probe`.
        "agents_enabled": is_available(),
    }


def probe() -> dict[str, object]:
    """Make ONE real agent call and report whether it worked.

    Deliberately not part of `/health`: it costs a real model call, and a liveness
    probe that spends money every few seconds is its own bug. Called explicitly
    when someone needs to know the agents actually work rather than merely
    being configured.
    """
    if not is_available():
        return {"ok": False, "reason": "CrewAI missing, or no LLM provider is active",
                **describe()}

    result = run_json(
        role="Connectivity Probe",
        goal="Confirm the model responds with valid JSON.",
        backstory="You answer with the exact JSON asked for and nothing else.",
        task='Return exactly this JSON and nothing else: {"ok": true}',
        expected_output='{"ok": true}',
        timeout=20,
        retries=0,
    )

    return {
        "ok": isinstance(result, dict) and result.get("ok") is True,
        "raw": result,
        **describe(),
    }


def _unused_json_guard() -> None:  # pragma: no cover - keeps json import meaningful
    json.dumps({})

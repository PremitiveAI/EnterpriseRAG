"""Dynamic agent rules — the registry and the reader
(ADR-009, docs/ai/agent-rules.md).

An agent's instruction block can be overridden by a plain-text file that a
Super Admin edits. This module owns which agents have one, where each file
lives, and what happens when it cannot be read.

Three properties it guarantees to its callers:

* **It never raises.** Every failure path returns the in-code default. A rule
  file is a customisation; losing it must never cost a chat, exactly as a
  failed agent must never cost one (ADR-002).
* **It never takes a path.** Callers pass an agent KEY, looked up in the
  registry below. No path component ever originates outside this file, so
  traversal is not filtered - it cannot be expressed. That is a stronger
  position than ``LocalStorageService._path()``, which must resolve and check
  containment because its key genuinely comes from a client.
* **It never lets an edit reach the grounding rules.** This module returns the
  editable block only. The rules that keep an answer grounded, and the JSON
  contract every caller parses, are appended by the agent modules after this
  content and are unreachable from here.

Phase 3-4 is read-only. Writing arrives with the Super Admin page; a file is
created by hand or by deployment until then.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger
from config.settings import settings

logger = get_logger(__name__)

# A rule is prose. This bound is not about storage - it is about the context
# window: a runaway rule pushes the retrieved passages out of the prompt, and
# the result looks like bad retrieval rather than a bad rule.
MAX_RULE_CHARS = 8_000


@dataclass(frozen=True)
class AgentSpec:
    """One agent that can be customised.

    ``filename`` is fixed here and never composed from a request. Adding an
    agent means adding an entry, which is a code change and a review - the
    point of a registry rather than a directory scan.
    """

    key: str
    name: str
    role: str
    filename: str


# The only agents whose rules are editable. Agent 1 (Identity Document) is
# deliberately absent: it runs in the Celery worker, and its output feeds PAN
# and Aadhaar validation, so its editable envelope would have to be narrower
# than these - a separate decision and a separate ADR (ADR-009 Scope).
REGISTRY: dict[str, AgentSpec] = {
    "query_planner": AgentSpec(
        key="query_planner",
        name="Query Planner",
        role="Rewrites a question into a standalone search query and proposes filters",
        filename="query_planner.txt",
    ),
    "response_composer": AgentSpec(
        key="response_composer",
        name="Response Composer",
        role="Writes a grounded answer from the retrieved passages and cites them",
        filename="response_composer.txt",
    ),
}


def rules_dir() -> Path:
    """Read through settings on every call, not captured at import.

    The test suite points this at a temporary directory. Binding it once at
    import would make that override depend on module import order, which is
    the kind of thing that works until it silently does not.
    """
    return Path(settings.AGENT_RULES_DIR)


def path_for(agent_key: str) -> Path | None:
    """The file for an agent, or None if the key is not in the registry."""
    spec = REGISTRY.get(agent_key)
    return rules_dir() / spec.filename if spec else None


# --- Reading --------------------------------------------------------------- #

# Cached on (mtime, size) rather than on a TTL or for the process lifetime.
# A save must take effect on the NEXT request - "eventually" is indistinguishable
# from "not at all" to someone who has just edited a prompt and is testing it.
_cache: dict[str, tuple[float, int, str]] = {}
_lock = threading.Lock()


def load(agent_key: str) -> str | None:
    """The agent's custom rule, or None to use the in-code default.

    None means "no usable customisation" for every reason there can be: no
    file, an empty one, whitespace, unreadable, oversized, or a key that is not
    registered. The caller does not branch on which - there is exactly one
    fallback and it is the same prompt that shipped.
    """
    path = path_for(agent_key)
    if path is None:
        # Not a warning: asking about an unregistered agent is a programming
        # error at the call site, and the HTTP layer answers 404 long before.
        logger.debug("Unknown agent key", extra={"agent_key": agent_key})
        return None

    try:
        stat = path.stat()
    except OSError:
        # Missing is the normal state before anyone has edited anything, so it
        # is not worth a warning on every chat message.
        return None

    signature = (stat.st_mtime, stat.st_size)

    with _lock:
        cached = _cache.get(agent_key)
        if cached and (cached[0], cached[1]) == signature:
            return cached[2] or None

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # This one IS a warning. The file exists, so somebody meant it to be
        # used, and it silently is not.
        logger.warning(
            "Agent rule file could not be read; using the built-in prompt",
            extra={"agent_key": agent_key, "error_type": type(exc).__name__},
        )
        return None

    if len(content) > MAX_RULE_CHARS:
        logger.warning(
            "Agent rule file is too large; using the built-in prompt",
            extra={"agent_key": agent_key, "chars": len(content),
                   "limit": MAX_RULE_CHARS},
        )
        return None

    cleaned = content.strip()

    with _lock:
        # An empty file is cached as "" so that repeated reads of a deliberately
        # cleared rule stay as cheap as a populated one.
        _cache[agent_key] = (stat.st_mtime, stat.st_size, cleaned)

    return cleaned or None


def clear_cache() -> None:
    """Drop every cached rule. For tests, and for a future save path."""
    with _lock:
        _cache.clear()


# --- The in-code defaults, and the parts no edit can reach ----------------- #
#
# Imported lazily inside the functions, never at module level: the two agent
# modules import THIS one, so a top-level import here would be circular.


def default_guidance(agent_key: str) -> str:
    """The guidance block that runs when no rule file is set."""
    if agent_key == "query_planner":
        from app.modules.ai import query_planner

        return query_planner.DEFAULT_GUIDANCE
    if agent_key == "response_composer":
        from app.modules.ai import response_composer

        return response_composer.FORMAT_RULES
    return ""


def locked_text(agent_key: str) -> str:
    """What the code always appends, whatever the rule says.

    Returned to the editing page so a Super Admin can SEE the part they cannot
    change. Hiding it invites them to write their own output contract and then
    wonder why nothing takes effect.
    """
    if agent_key == "query_planner":
        from app.modules.ai import query_planner

        return query_planner.OUTPUT_CONTRACT
    if agent_key == "response_composer":
        from app.modules.ai import response_composer

        return f"{response_composer.ABSOLUTE_RULES}\n\n{response_composer.OUTPUT_CONTRACT}"
    return ""


# --- Writing --------------------------------------------------------------- #


class RuleValidationError(ValueError):
    """The content is not usable as a prompt. Carries a human explanation."""


def validate(content: str) -> str:
    """Return the content to store, or raise RuleValidationError.

    Deliberately permissive: a rule is prose, so almost anything is legal. The
    two bounds exist for concrete reasons rather than tidiness.
    """
    if len(content) > MAX_RULE_CHARS:
        raise RuleValidationError(
            f"A rule may be at most {MAX_RULE_CHARS:,} characters; this is "
            f"{len(content):,}. A longer rule pushes the retrieved passages out "
            "of the model's context, which looks like bad retrieval rather than "
            "a bad rule."
        )

    # Tab and the two newline characters are ordinary in prose. Anything else in
    # the C0 range arrived by accident - a paste from a binary file, usually -
    # and a NUL would truncate the prompt at the C layer with no error.
    illegal = {ch for ch in content if ord(ch) < 32 and ch not in "\t\n\r"}
    if illegal:
        raise RuleValidationError(
            "The rule contains control characters that cannot appear in a "
            "prompt. Paste it as plain text."
        )

    return content


def save(agent_key: str, content: str) -> str:
    """Write a rule and return what was stored. Raises on a bad key or content.

    The write is atomic - temp file, then replace - because a truncated prompt
    is still a *valid* prompt. It would be used, not rejected, and the agent
    would quietly run on half an instruction.
    """
    spec = REGISTRY.get(agent_key)
    if spec is None:
        raise KeyError(agent_key)

    cleaned = validate(content).strip()

    directory = rules_dir()
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / spec.filename
    # Same directory as the target: os.replace is only atomic within one
    # filesystem, and a temp directory can be on another volume.
    temporary = final.with_suffix(".tmp")

    try:
        temporary.write_text(cleaned, encoding="utf-8")
        temporary.replace(final)
    except OSError:
        # Leave no half-written file behind for the reader to pick up.
        temporary.unlink(missing_ok=True)
        raise
    finally:
        with _lock:
            _cache.pop(agent_key, None)

    logger.info("Agent rule saved", extra={"agent_key": agent_key,
                                           "chars": len(cleaned)})
    return cleaned


def describe() -> list[dict[str, object]]:
    """Every agent and whether a custom rule is currently in effect.

    Shaped for the Super Admin page that will list them. Never returns a path:
    the client has no use for one and could not act on it.
    """
    return [
        {
            "agent_key": spec.key,
            "name": spec.name,
            "role": spec.role,
            "is_custom": load(spec.key) is not None,
        }
        for spec in REGISTRY.values()
    ]

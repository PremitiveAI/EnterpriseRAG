"""The client factory and agent 1's migration (ADR-010 §4, ADR-002).

Phase 4. No network: every test either inspects a composed id, or replaces the
call itself. What is being asserted is the wiring — which provider is resolved,
which model id is handed over, when a client is rebuilt, and that agent 1 now
travels the same path as agents 2 and 3.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import NoActiveLLMProviderError
from app.modules.ai import crew, identity_agent, llm_client
from app.modules.ai.llm_config import LLMConfig


def _config(version: int = 1, provider: str = "gemini",
            model: str = "gemini-2.0-flash") -> LLMConfig:
    return LLMConfig(version=version, provider=provider, model=model,
                     api_key="test-key", fingerprint="0123456789abcdef")


@pytest.fixture(autouse=True)
def _clear_client_cache():
    llm_client.build_llm.cache_clear()
    yield
    llm_client.build_llm.cache_clear()


# --- Composing the model id ------------------------------------------------- #


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("gemini", "gemini-2.0-flash", "gemini/gemini-2.0-flash"),
        ("openai", "gpt-4o", "openai/gpt-4o"),
        ("anthropic", "claude-sonnet-4", "anthropic/claude-sonnet-4"),
        ("azure", "my-deployment", "azure/my-deployment"),
    ],
)
def test_the_prefix_is_composed_once_per_provider(provider, model, expected):
    """One test per provider, because the prefix is per provider and a wrong
    one is only visible as a failed call at run time."""
    assert llm_client.model_id(provider, model) == expected


@pytest.mark.parametrize(
    "stored",
    ["gemini/gemini-2.0-flash", "google/gemini-2.0-flash", "models/gemini-2.0-flash",
     "GEMINI/gemini-2.0-flash"],
)
def test_a_prefix_already_in_the_stored_id_is_not_doubled(stored):
    """The regression this guard exists for.

    ``GEMINI_MODEL=gemini/gemini-2.0-flash`` once produced
    ``gemini/gemini/gemini-2.0-flash``, which made BOTH AI paths fail and every
    answer come silently from the templated fallback. A value can still reach
    the database from a migration, a fixture or hand-written SQL, so the guard
    lives here and not only on the write path.
    """
    assert llm_client.model_id("gemini", stored) == "gemini/gemini-2.0-flash"


def test_a_cross_provider_prefix_is_replaced_not_kept():
    """A row edited from openai to gemini must not keep the old prefix."""
    assert llm_client.model_id("gemini", "openai/gpt-4o") == "gemini/gpt-4o"


def test_an_unknown_provider_is_refused():
    with pytest.raises(ValueError):
        llm_client.model_id("mistral", "mistral-large")


# --- When a client is rebuilt ----------------------------------------------- #


def test_the_same_configuration_reuses_the_same_client():
    """Otherwise every chat message would construct a new CrewAI client."""
    config = _config()

    first = llm_client.build_llm(config, 0.0, 30)
    second = llm_client.build_llm(config, 0.0, 30)

    assert first is second


def test_a_new_configuration_builds_a_new_client():
    """The switch, at the level where it actually takes effect. No invalidation
    call anywhere - the new version is a new object, so it is a cache miss."""
    later = _config(version=2, model="gemini-2.5-pro")

    first = llm_client.build_llm(_config(version=1), 0.0, 30)
    second = llm_client.build_llm(later, 0.0, 30)

    assert first is not second
    # CrewAI keeps the bare id on the instance and routes on the prefix, so the
    # composed id is asserted where it is composed.
    assert llm_client.llm_kwargs(later, 0.0, 30)["model"] == "gemini/gemini-2.5-pro"
    assert second.model == "gemini-2.5-pro"


def test_each_temperature_gets_its_own_client():
    """Agents 1 and 2 need 0.0 and the composer needs 0.2; one shared client
    would silently give one of them the other's setting."""
    config = _config()

    assert llm_client.build_llm(config, 0.0, 30) is not llm_client.build_llm(config, 0.2, 30)


def test_a_base_url_is_passed_through():
    """Azure and self-hosted endpoints are unusable without it.

    Asserted on the arguments rather than on a constructed client: CrewAI loads
    a different SDK per provider and only google-genai is installed.
    """
    config = LLMConfig(version=1, provider="azure", model="my-deployment",
                       api_key="k", base_url="https://example.invalid/openai")

    assert llm_client.llm_kwargs(config, 0.0, 30) == {
        "model": "azure/my-deployment",
        "api_key": "k",
        "temperature": 0.0,
        "timeout": 30,
        "base_url": "https://example.invalid/openai",
    }


def test_an_absent_base_url_is_omitted_rather_than_sent_as_none():
    """Some providers read base_url=None as an override of their own default
    endpoint rather than as an absence."""
    assert "base_url" not in llm_client.llm_kwargs(_config(), 0.0, 30)


def test_a_provider_whose_library_is_missing_fails_as_a_config_problem():
    """Registering a provider is not the same as being able to call it.

    CrewAI 1.15 gives each provider its own extra and only google-genai is in
    requirements.txt. Without this the failure is a bare ImportError from deep
    inside crewai, which reads like a broken install rather than a provider
    that was never provisioned.
    """
    from app.core.exceptions import LLMConfigUnavailableError

    config = _config(provider="anthropic", model="claude-sonnet-4")

    with pytest.raises(LLMConfigUnavailableError):
        llm_client.build_llm(config, 0.0, 30)


# --- The never-raises contract ---------------------------------------------- #


def test_no_active_provider_degrades_rather_than_raising(monkeypatch):
    """A document that would index perfectly well must not fail because nobody
    has chosen a provider (ADR-002)."""
    monkeypatch.setattr(llm_client, "get_config",
                        lambda *_a, **_k: (_ for _ in ()).throw(NoActiveLLMProviderError()))

    assert llm_client.generate_text("anything") is None
    assert llm_client.generate_json("anything") is None
    assert llm_client.is_configured() is False


def test_a_failing_model_call_returns_none(monkeypatch):
    class _Boom:
        def call(self, *_a, **_k):
            raise RuntimeError("upstream refused")

    monkeypatch.setattr(llm_client, "get_config", lambda *_a, **_k: _config())
    monkeypatch.setattr(llm_client, "build_llm", lambda *_a, **_k: _Boom())

    assert llm_client.generate_json("anything") is None


def test_a_json_reply_is_parsed(monkeypatch):
    class _Fixed:
        def call(self, *_a, **_k):
            return '```json\n{"ok": true}\n```'

    monkeypatch.setattr(llm_client, "get_config", lambda *_a, **_k: _config())
    monkeypatch.setattr(llm_client, "build_llm", lambda *_a, **_k: _Fixed())

    assert llm_client.generate_json("anything") == {"ok": True}


# --- Agent 1 now travels the same path -------------------------------------- #


def test_agent_1_produces_a_result_through_the_crew_path(monkeypatch):
    """The test with teeth.

    Agent 1's failure mode is silent and runs in the worker: a document with no
    identity type is indistinguishable from one that legitimately has none. So
    this asserts a RESULT, not merely that nothing raised.
    """
    captured = {}

    def _fake_run_json(**kwargs):
        captured.update(kwargs)
        return {
            "document_type": "pan_card",
            "confidence": 0.94,
            "fields": {"name": "A Person", "pan_number": "ABCDE1234F"},
        }

    monkeypatch.setattr(crew, "is_available", lambda: True)
    monkeypatch.setattr(crew, "run_json", _fake_run_json)

    result = identity_agent.analyse("PERMANENT ACCOUNT NUMBER ABCDE1234F")

    assert result.document_type == "pan_card"
    assert result.fields["pan_number"] == "ABCDE1234F"
    assert result.confidence == pytest.approx(0.94)


def test_agent_1_is_a_crew_agent_with_its_own_persona(monkeypatch):
    """It used to be a bare prompt against a provider-specific SDK, which is
    why a provider switch would have reached two agents out of three."""
    captured = {}

    def _fake_run_json(**kwargs):
        captured.update(kwargs)
        return {"document_type": "not_an_identity_document", "confidence": 0.1,
                "fields": {}}

    monkeypatch.setattr(crew, "is_available", lambda: True)
    monkeypatch.setattr(crew, "run_json", _fake_run_json)

    identity_agent.analyse("some ocr text")

    assert captured["role"] == identity_agent.ROLE
    # Identifier extraction; there is nothing here creativity improves.
    assert captured["temperature"] == 0.0
    assert captured["timeout"] == identity_agent.TIMEOUT_SECONDS


def test_agent_1_degrades_when_no_provider_is_active(monkeypatch):
    """The document is still classified, chunked and indexed."""
    monkeypatch.setattr(crew, "is_available", lambda: False)

    result = identity_agent.analyse("PERMANENT ACCOUNT NUMBER ABCDE1234F")

    assert result.document_type is None
    assert result.fields == {}


def test_agent_1_still_discards_an_invalid_identifier(monkeypatch):
    """The structural validation sits outside the agent and must survive the
    move: a wrong identifier is worse than a missing one."""
    monkeypatch.setattr(crew, "is_available", lambda: True)
    monkeypatch.setattr(
        crew, "run_json",
        lambda **_k: {"document_type": "pan_card", "confidence": 0.9,
                      "fields": {"pan_number": "NOT-A-PAN"}},
    )

    result = identity_agent.analyse("noise")

    assert "pan_number" not in result.fields
    assert "pan_number" in result.discarded


# --- Nothing reads GEMINI_* any more ---------------------------------------- #


def test_the_agent_layer_no_longer_reads_the_env_provider():
    """Phase 7 deletes these settings. Anything still reading them would break
    then, silently, in the worker."""
    import inspect

    from app.modules.ai import enrichment_service

    for module in (crew, llm_client, identity_agent, enrichment_service):
        source = inspect.getsource(module)
        assert "GEMINI_API_KEY" not in source, module.__name__
        assert "settings.GEMINI_MODEL" not in source, module.__name__


def test_the_superseded_module_fails_loudly_rather_than_answering():
    """A missed import must not quietly answer from a provider nobody chose."""
    from app.modules.ai import gemini_client

    with pytest.raises(RuntimeError, match="llm_client"):
        gemini_client.is_configured()

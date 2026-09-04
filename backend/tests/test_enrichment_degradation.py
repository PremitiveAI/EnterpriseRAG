"""Enrichment must report when it did not actually run.

Every AI stage degrades rather than fails, which is right: losing a category
label is a metadata gap, losing the vectors would make the document invisible.
But degrading silently is not the same thing, and that is what happened here.

A real upload hit the Gemini free tier's 20-requests-per-day cap mid-document.
Classification got through; metadata and tags were both refused with 429. The
old code judged each stage by its output - a filename title and an empty tag
list are exactly what a successful call on a contentless document returns - so
``degraded`` came back False and the document was recorded as fully enriched
while missing its description and every one of its tags.

These tests pin the distinction: **empty means the model answered with nothing,
None means the model never answered.**
"""

from __future__ import annotations

import pytest

from app.modules.ai import enrichment_service

TAXONOMY = [
    {"slug": "hr-policies", "name": "HR Policies", "description": "Leave, payroll."},
    {"slug": "uncategorised", "name": "Uncategorised", "description": ""},
]

TEXT = "Annual leave policy for the financial year 2025-2026."


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Pretend a key is present; each test decides what the model returns."""
    monkeypatch.setattr(enrichment_service.llm_client, "is_configured", lambda: True)


def _model_returns(monkeypatch, *responses):
    """Queue one response per generate_json call, in stage order."""
    queue = list(responses)

    def fake(_prompt, **_kwargs):
        return queue.pop(0) if queue else None

    monkeypatch.setattr(enrichment_service.llm_client, "generate_json", fake)


# --- The bug that was shipped --------------------------------------------- #


def test_a_rate_limited_metadata_call_is_degraded(monkeypatch):
    """The exact production failure: classify succeeds, the rest are refused."""
    _model_returns(
        monkeypatch,
        {"category_slug": "hr-policies", "document_type": "leave_policy"},
        None,  # extract_metadata - 429
        None,  # generate_tags - 429
    )

    result = enrichment_service.enrich(TEXT, taxonomy=TAXONOMY, fallback_title="a.pdf")

    assert result.category_slug == "hr-policies"
    assert result.degraded is True, (
        "classification succeeded but metadata and tags were refused - "
        "recording this as fully enriched is how a document loses its "
        "description with nobody noticing"
    )
    assert result.title == "a.pdf"
    assert result.description is None
    assert result.tags == []


def test_a_rate_limited_tag_call_alone_is_degraded(monkeypatch):
    _model_returns(
        monkeypatch,
        {"category_slug": "hr-policies", "document_type": "leave_policy"},
        {"title": "Leave Policy 2025-26", "description": "Annual leave rules."},
        None,  # generate_tags - 429
    )

    result = enrichment_service.enrich(TEXT, taxonomy=TAXONOMY, fallback_title="a.pdf")

    assert result.title == "Leave Policy 2025-26"
    assert result.description == "Annual leave rules."
    assert result.degraded is True


def test_everything_refused_is_degraded(monkeypatch):
    _model_returns(monkeypatch, None, None, None)

    result = enrichment_service.enrich(TEXT, taxonomy=TAXONOMY, fallback_title="a.pdf")

    assert result.degraded is True
    assert result.title == "a.pdf"
    assert result.tags == []


# --- The other side: a genuine answer is NOT degraded --------------------- #


def test_a_full_answer_is_not_degraded(monkeypatch):
    _model_returns(
        monkeypatch,
        {"category_slug": "hr-policies", "document_type": "leave_policy"},
        {"title": "Leave Policy 2025-26", "description": "Annual leave rules."},
        {"tags": ["leave", "hr", "policy"]},
    )

    result = enrichment_service.enrich(TEXT, taxonomy=TAXONOMY, fallback_title="a.pdf")

    assert result.degraded is False
    assert result.tags == ["leave", "hr", "policy"]
    assert result.document_type == "leave_policy"


def test_an_empty_tag_list_from_a_real_answer_is_not_degraded(monkeypatch):
    """The distinction this whole change exists for.

    The model read the document and returned no tags. That is an answer, not an
    outage, and it must not be reported as one.
    """
    _model_returns(
        monkeypatch,
        {"category_slug": "hr-policies", "document_type": "leave_policy"},
        {"title": "Leave Policy 2025-26", "description": "Annual leave rules."},
        {"tags": []},
    )

    result = enrichment_service.enrich(TEXT, taxonomy=TAXONOMY, fallback_title="a.pdf")

    assert result.tags == []
    assert result.degraded is False


# --- Unit level ------------------------------------------------------------ #


def test_extract_metadata_returns_none_when_the_model_is_unavailable(monkeypatch):
    _model_returns(monkeypatch, None)
    assert enrichment_service.extract_metadata(TEXT, "a.pdf") is None


def test_generate_tags_returns_none_when_the_model_is_unavailable(monkeypatch):
    _model_returns(monkeypatch, None)
    assert enrichment_service.generate_tags(TEXT) is None


def test_generate_tags_returns_a_list_when_the_model_answers_with_none(monkeypatch):
    _model_returns(monkeypatch, {"tags": []})
    assert enrichment_service.generate_tags(TEXT) == []


def test_empty_text_is_not_an_outage(monkeypatch):
    """No text to read is a fact about the document, not about the model, and
    it must not spend a request finding that out."""
    _model_returns(monkeypatch)  # any call would pop from an empty queue -> None
    assert enrichment_service.extract_metadata("   ", "a.pdf") == ("a.pdf", None)
    assert enrichment_service.generate_tags("   ") == []


def test_an_unconfigured_key_is_degraded(monkeypatch):
    monkeypatch.setattr(enrichment_service.llm_client, "is_configured", lambda: False)

    result = enrichment_service.enrich(TEXT, taxonomy=TAXONOMY, fallback_title="a.pdf")

    assert result.degraded is True
    assert result.title == "a.pdf"

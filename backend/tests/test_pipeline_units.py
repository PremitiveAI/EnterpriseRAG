"""Pipeline units that need no database, no Qdrant and no model."""

from __future__ import annotations

import pytest

from app.core.exceptions import IllegalTransition
from app.modules.ai.llm_client import clean_json
from app.modules.ai.identity_agent import (
    valid_aadhaar,
    valid_pan,
    verhoeff_valid,
    _validate,
)
from app.modules.documents.models import DocumentStatus as S
from app.modules.documents.services.chunking_service import chunk_pages
from app.modules.documents.services.extraction_service import Page
from app.modules.documents.services.state_machine import (
    can_transition,
    is_terminal,
    progress_percent,
)


class TestStateMachine:
    def test_normal_pipeline_path_is_legal(self):
        path = [S.QUEUED, S.PROCESSING, S.EXTRACTING, S.CLASSIFYING,
                S.CHUNKING, S.EMBEDDING, S.INDEXING, S.COMPLETED]
        for current, target in zip(path, path[1:], strict=False):
            assert can_transition(current, target), f"{current} -> {target}"

    def test_ocr_is_optional(self):
        assert can_transition(S.EXTRACTING, S.OCR)
        assert can_transition(S.EXTRACTING, S.CLASSIFYING)  # skipped

    def test_any_active_state_may_fail(self):
        for state in (S.PROCESSING, S.EXTRACTING, S.OCR, S.CLASSIFYING,
                      S.CHUNKING, S.EMBEDDING, S.INDEXING):
            assert can_transition(state, S.FAILED)

    def test_any_active_state_may_be_deleted(self):
        for state in (S.QUEUED, S.PROCESSING, S.EXTRACTING, S.COMPLETED, S.FAILED):
            assert can_transition(state, S.DELETED)

    def test_deleted_is_absorbing(self):
        """Nothing may resurrect a deleted document."""
        for target in (S.QUEUED, S.PROCESSING, S.COMPLETED, S.FAILED, S.DELETED):
            assert not can_transition(S.DELETED, target)

    def test_cannot_skip_straight_to_completed(self):
        assert not can_transition(S.QUEUED, S.COMPLETED)
        assert not can_transition(S.EXTRACTING, S.COMPLETED)

    def test_completed_requires_indexing(self):
        assert can_transition(S.INDEXING, S.COMPLETED)

    def test_no_backwards_movement_within_a_run(self):
        assert not can_transition(S.EMBEDDING, S.EXTRACTING)

    def test_terminal_states_reenter_only_via_queued(self):
        for state in (S.COMPLETED, S.FAILED, S.DUPLICATE):
            assert can_transition(state, S.QUEUED)
            assert not can_transition(state, S.PROCESSING)

    def test_duplicate_is_reachable_before_ai_stages(self):
        assert can_transition(S.EXTRACTING, S.DUPLICATE)
        assert can_transition(S.OCR, S.DUPLICATE)

    def test_terminal_set(self):
        assert all(is_terminal(s) for s in (S.COMPLETED, S.FAILED, S.DUPLICATE, S.DELETED))
        assert not any(is_terminal(s) for s in (S.QUEUED, S.PROCESSING, S.EMBEDDING))

    def test_progress_is_monotonic_along_the_pipeline(self):
        path = [S.QUEUED, S.PROCESSING, S.EXTRACTING, S.OCR, S.CLASSIFYING,
                S.CHUNKING, S.EMBEDDING, S.INDEXING, S.COMPLETED]
        values = [progress_percent(s) for s in path]
        assert values == sorted(values)
        assert values[-1] == 100


class TestChunking:
    def test_short_page_is_one_chunk(self):
        chunks = chunk_pages([Page(number=1, text="Annual leave is 24 days.")])
        assert len(chunks) == 1
        assert chunks[0].page_number == 1

    def test_chunks_never_span_pages(self):
        """Citations must point at the right page."""
        pages = [Page(number=1, text="Page one content."), Page(number=2, text="Page two content.")]
        chunks = chunk_pages(pages)
        assert {c.page_number for c in chunks} == {1, 2}
        for chunk in chunks:
            assert not ("one" in chunk.text and "two" in chunk.text)

    def test_long_page_splits(self):
        text = " ".join(f"Sentence number {i} about leave policy." for i in range(300))
        chunks = chunk_pages([Page(number=1, text=text)], size=500, overlap=50)
        assert len(chunks) > 1
        assert all(c.page_number == 1 for c in chunks)

    def test_chunks_stay_near_the_encoder_window(self):
        """Oversized chunks are truncated silently by the model (ADR-003)."""
        text = " ".join(f"word{i}" for i in range(4000))
        chunks = chunk_pages([Page(number=1, text=text)], size=1000, overlap=150)
        assert all(c.char_count <= 1200 for c in chunks)

    def test_indexes_are_contiguous_from_zero(self):
        pages = [Page(number=n, text=f"Content for page {n}. " * 40) for n in range(1, 4)]
        chunks = chunk_pages(pages, size=300, overlap=30)
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_overlap_carries_context_forward(self):
        text = " ".join(f"Sentence {i}." for i in range(200))
        chunks = chunk_pages([Page(number=1, text=text)], size=400, overlap=100)
        assert len(chunks) > 1
        # Some tail of chunk n should reappear at the head of chunk n+1.
        assert any(
            chunks[i].text[-40:].split()[-1] in chunks[i + 1].text
            for i in range(len(chunks) - 1)
        )

    def test_empty_pages_produce_nothing(self):
        assert chunk_pages([Page(number=1, text="   "), Page(number=2, text="")]) == []

    def test_section_is_carried_onto_chunks(self):
        chunks = chunk_pages([Page(number=1, text="Body text.", section="Leave")])
        assert chunks[0].section == "Leave"

    def test_no_word_is_split(self):
        text = "supercalifragilistic " * 200
        chunks = chunk_pages([Page(number=1, text=text)], size=200, overlap=20)
        for chunk in chunks:
            for word in chunk.text.split():
                assert word in {"supercalifragilistic"}


class TestIdentityValidation:
    @pytest.mark.parametrize("value", ["ABCDE1234F", "abcde1234f"])
    def test_valid_pan_accepted(self, value):
        assert valid_pan(value)

    @pytest.mark.parametrize("value", ["ABCD1234F", "ABCDE12345", "12345ABCDE", "", "ABCDE1234"])
    def test_invalid_pan_rejected(self, value):
        assert not valid_pan(value)

    def test_verhoeff_matches_the_documented_example(self):
        """2363 is the standard worked example; guards the tables themselves."""
        assert verhoeff_valid("2363")
        assert not verhoeff_valid("2364")

    def test_verhoeff_accepts_a_valid_checksum(self):
        # Verified: check digit for prefix 23456789012 is 4. The library itself is
        # cross-checked against the documented Verhoeff example 2363 below.
        assert verhoeff_valid("234567890124")

    def test_verhoeff_rejects_transposed_digits(self):
        """The failure mode a plain length check would miss."""
        assert not verhoeff_valid("234567890142")

    def test_aadhaar_accepts_spaced_form(self):
        assert valid_aadhaar("2345 6789 0124")

    @pytest.mark.parametrize("value", ["123456789012", "12345678901", "abcdefghijkl", ""])
    def test_invalid_aadhaar_rejected(self, value):
        assert not valid_aadhaar(value)

    def test_invalid_identifiers_are_discarded_not_stored(self):
        """A confidently wrong number is worse than a missing one."""
        kept, discarded = _validate({"pan_number": "NOTAPAN", "aadhaar_number": "111111111111",
                                     "name": "Asha Rao"})
        assert "pan_number" not in kept
        assert "aadhaar_number" not in kept
        assert set(discarded) == {"pan_number", "aadhaar_number"}
        assert kept["name"] == "Asha Rao"

    def test_valid_identifiers_are_kept_and_normalised(self):
        kept, discarded = _validate({"pan_number": "abcde1234f", "aadhaar_number": "2345 6789 0124"})
        assert kept["pan_number"] == "ABCDE1234F"
        assert kept["aadhaar_number"] == "234567890124"
        assert discarded == []


class TestGeminiResponseParsing:
    def test_plain_json(self):
        assert clean_json('{"a": 1}') == {"a": 1}

    def test_markdown_fenced_json(self):
        assert clean_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_trailing_comma_salvaged(self):
        assert clean_json('{"a": 1,}') == {"a": 1}

    def test_prose_around_json_salvaged(self):
        assert clean_json('Here you go:\n{"a": 1}\nHope that helps.') == {"a": 1}

    def test_unparseable_returns_none(self):
        """A bad response is a degraded stage, not a failed document."""
        assert clean_json("I'm sorry, I can't do that.") is None

    def test_empty_returns_none(self):
        assert clean_json("") is None

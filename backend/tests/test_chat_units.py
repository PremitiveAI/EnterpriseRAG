"""Chat logic that needs no database, no Qdrant and no model.

These cover the two rules that carry most of the grounding safety: an invented
citation is dropped, and an answer that cites nothing verifiable is replaced.
"""

from __future__ import annotations

import pytest

from app.modules.ai import query_planner, response_composer
from app.modules.chat.repositories.conversation_repository import derive_title


class TestTitleDerivation:
    def test_short_message_is_the_title(self):
        assert derive_title("What is the leave policy?") == "What is the leave policy?"

    def test_long_message_is_cut_on_a_word_boundary(self):
        question = (
            "What documents are required for onboarding a new permanent employee "
            "in the Bangalore office during the probation period?"
        )
        title = derive_title(question)
        assert len(title) <= 61  # 60 plus the ellipsis
        assert title.endswith("…")
        # Never mid-word.
        assert not title[:-1].endswith(" ")
        assert question.startswith(title[:-1])

    def test_whitespace_is_collapsed(self):
        assert derive_title("  What   is\n\nthe policy?  ") == "What is the policy?"

    def test_empty_message_falls_back(self):
        assert derive_title("   ") == "New conversation"


class TestComposerCitationVerification:
    """The model does not get the final say on what it cited (§35)."""

    CHUNKS = [
        {"chunk_id": "aaa", "document_id": "d1", "document_name": "Leave.pdf",
         "text": "Employees receive 24 days of annual leave.", "page_number": 1},
        {"chunk_id": "bbb", "document_id": "d1", "document_name": "Leave.pdf",
         "text": "Leave must be requested two weeks ahead.", "page_number": 2},
    ]

    def test_invented_citation_is_dropped(self, monkeypatch):
        monkeypatch.setattr(response_composer.crew, "run_json", lambda **_: {
            "answer": "Employees receive 24 days.",
            "is_grounded": True,
            "cited_chunk_ids": ["aaa", "ccc-invented"],
        })

        result = response_composer.compose("How much leave?", self.CHUNKS)

        assert result.cited_chunk_ids == ["aaa"]
        assert result.is_grounded is True

    def test_grounded_answer_citing_nothing_real_falls_back(self, monkeypatch):
        """An answer claiming grounding while citing only invented ids is the
        exact failure §35 exists to prevent."""
        monkeypatch.setattr(response_composer.crew, "run_json", lambda **_: {
            "answer": "Employees receive 24 days.",
            "is_grounded": True,
            "cited_chunk_ids": ["nonsense"],
        })

        result = response_composer.compose("How much leave?", self.CHUNKS)

        assert result.degraded is True
        assert set(result.cited_chunk_ids) == {"aaa", "bbb"}

    def test_agent_failure_uses_the_templated_answer(self, monkeypatch):
        monkeypatch.setattr(response_composer.crew, "run_json", lambda **_: None)

        result = response_composer.compose("How much leave?", self.CHUNKS)

        assert result.degraded is True
        assert result.is_grounded is True
        # Plain prose, but still grounded and still cited.
        assert "Leave.pdf" in result.answer
        assert "24 days" in result.answer
        assert set(result.cited_chunk_ids) == {"aaa", "bbb"}

    def test_empty_answer_uses_the_templated_answer(self, monkeypatch):
        monkeypatch.setattr(response_composer.crew, "run_json", lambda **_: {
            "answer": "   ", "is_grounded": True, "cited_chunk_ids": ["aaa"]})

        assert response_composer.compose("q", self.CHUNKS).degraded is True

    def test_ungrounded_answer_is_passed_through_without_sources(self, monkeypatch):
        monkeypatch.setattr(response_composer.crew, "run_json", lambda **_: {
            "answer": "I could not find this information in the available documents.",
            "is_grounded": False,
            "cited_chunk_ids": ["aaa"],
        })

        result = response_composer.compose("What is the capital of France?", self.CHUNKS)

        assert result.is_grounded is False
        assert result.cited_chunk_ids == []

    def test_no_chunks_never_produces_an_answer(self):
        """Defence in depth: the caller short-circuits, and so does this."""
        result = response_composer.compose("anything", [])
        assert result.is_grounded is False
        assert result.answer == response_composer.NOT_FOUND_ANSWER

    def test_the_corpus_is_never_sent(self):
        """Only the supplied chunks reach the prompt, each capped (§32)."""
        chunk = {"chunk_id": "x", "document_id": "d", "document_name": "Big.pdf",
                 "text": "A" * 50_000, "page_number": 1}
        prompt = response_composer._prompt("q", [chunk])

        assert len(prompt) < 5_000
        assert "A" * response_composer.MAX_CHUNK_CHARS in prompt
        assert "A" * (response_composer.MAX_CHUNK_CHARS + 1) not in prompt


class TestQueryPlanner:
    def test_agent_failure_falls_back_to_the_raw_question(self, monkeypatch):
        monkeypatch.setattr(query_planner.crew, "run_json", lambda **_: None)

        plan = query_planner.plan("What is the leave policy?")

        assert plan.search_query == "What is the leave policy?"
        assert plan.filters == {}
        assert plan.degraded is True

    def test_rewritten_query_is_used(self, monkeypatch):
        monkeypatch.setattr(query_planner.crew, "run_json", lambda **_: {
            "search_query": "leave policy entitlement contractors",
            "filters": {"category_slug": "administrative-internal", "language": "en",
                        "document_type": None},
            "intent": "policy_lookup",
        })

        plan = query_planner.plan("What about for contractors?",
                                  history=[{"role": "user", "content": "leave policy?"}])

        assert plan.search_query == "leave policy entitlement contractors"
        assert plan.filters == {"category_slug": "administrative-internal", "language": "en"}
        assert plan.degraded is False

    def test_null_filters_are_not_proposed(self, monkeypatch):
        monkeypatch.setattr(query_planner.crew, "run_json", lambda **_: {
            "search_query": "leave policy",
            "filters": {"category_slug": "null", "language": "  ", "document_type": None},
        })

        assert query_planner.plan("leave policy?").filters == {}

    def test_empty_search_query_falls_back(self, monkeypatch):
        monkeypatch.setattr(query_planner.crew, "run_json", lambda **_: {"search_query": ""})

        plan = query_planner.plan("leave policy?")
        assert plan.search_query == "leave policy?"
        assert plan.degraded is True

    def test_empty_question_is_not_sent_to_the_agent(self, monkeypatch):
        called = False

        def spy(**_):
            nonlocal called
            called = True
            return None

        monkeypatch.setattr(query_planner.crew, "run_json", spy)
        query_planner.plan("   ")
        assert called is False

    def test_history_is_truncated_in_the_prompt(self):
        history = [{"role": "user", "content": f"question {i} " + "x" * 2000}
                   for i in range(20)]
        prompt = query_planner._prompt("follow-up", history, ["hr-policies"])

        # Only the recent window, each turn capped.
        assert "question 19" in prompt
        assert "question 0 " not in prompt
        assert len(prompt) < 3_000

    @pytest.mark.parametrize("malformed", [None, [], "text", 42])
    def test_malformed_agent_output_falls_back(self, monkeypatch, malformed):
        monkeypatch.setattr(query_planner.crew, "run_json", lambda **_: malformed)
        assert query_planner.plan("leave policy?").degraded is True


class TestResponseFormatting:
    """The composer picks a shape to fit the question (§35 addendum).

    These assert the *instructions* and the deterministic fallback. What shape a
    live model actually returns belongs to the grounding evaluation, not here —
    a unit test cannot hold a model to its prompt.
    """

    CHUNKS = [
        {"chunk_id": "aaa", "document_id": "d1", "document_name": "Leave.pdf",
         "text": "Employees receive 24 days of annual leave.", "page_number": 1},
        {"chunk_id": "bbb", "document_id": "d1", "document_name": "Leave.pdf",
         "text": "Leave must be requested two weeks ahead.", "page_number": 2},
    ]

    def test_the_prompt_names_every_supported_shape(self):
        prompt = response_composer._prompt("How much leave?", self.CHUNKS)

        for shape in ("sentence", "paragraph", "bullet", "numbered", "table", "code block"):
            assert shape in prompt.lower(), f"{shape} is not offered to the composer"

    def test_the_prompt_forbids_defaulting_to_a_list(self):
        """A model handed a menu of formats will use them — which is how every
        answer becomes bullet points, including 'how many days of leave?'."""
        prompt = response_composer._prompt("How much leave?", self.CHUNKS).lower()

        assert "do not use a\nlist by default" in prompt or "not use a list by default" in prompt
        assert "one sentence" in prompt

    def test_the_prompt_ties_numbered_lists_to_order(self):
        prompt = response_composer._prompt("How do I claim expenses?", self.CHUNKS).lower()
        assert "only when order matters" in prompt

    def test_the_prompt_restricts_code_blocks_to_technical_content(self):
        prompt = response_composer._prompt("q", self.CHUNKS).lower()
        assert "never put ordinary prose in a code block" in prompt

    def test_the_prompt_forbids_padding(self):
        prompt = response_composer._prompt("q", self.CHUNKS).lower()
        assert "never repeat the\nquestion back" in prompt or "repeat the question back" in prompt
        assert "closing summary" in prompt

    def test_the_answer_is_declared_as_markdown(self):
        """The client renders it as Markdown; the contract has to say so."""
        assert "markdown" in response_composer._prompt("q", self.CHUNKS).lower()

    def test_a_single_passage_fallback_is_a_paragraph_not_a_bullet(self):
        """The fallback follows the rule it asks the agent to follow."""
        result = response_composer.templated(self.CHUNKS[:1])

        assert not result.answer.lstrip().startswith(("-", "*", "•"))
        assert "Leave.pdf" in result.answer
        assert "24 days" in result.answer

    def test_several_passages_fall_back_to_a_list(self):
        result = response_composer.templated(self.CHUNKS)

        assert "- **Leave.pdf, page 1**" in result.answer
        assert "- **Leave.pdf, page 2**" in result.answer

    def test_the_fallback_stays_grounded_and_cited_whatever_its_shape(self):
        for chunks in (self.CHUNKS[:1], self.CHUNKS):
            result = response_composer.templated(chunks)
            assert result.is_grounded is True
            assert result.degraded is True
            assert len(result.cited_chunk_ids) == len(chunks)

    def test_a_markdown_answer_survives_composition_unchanged(self):
        """Tables and code blocks must not be mangled on the way through."""
        table = (
            "| Class | Cap |\n| --- | --- |\n| Metro | 4,000 |\n| Other | 2,500 |"
        )
        monkey = {"answer": table, "is_grounded": True, "cited_chunk_ids": ["aaa"]}

        import pytest as _pytest

        with _pytest.MonkeyPatch.context() as patch:
            patch.setattr(response_composer.crew, "run_json", lambda **_: monkey)
            result = response_composer.compose("Compare the hotel caps", self.CHUNKS)

        assert result.answer == table
        assert result.is_grounded is True

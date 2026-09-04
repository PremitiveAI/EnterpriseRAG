"""Grounding evaluation (docs/testing/test-plan.md § Grounding evaluation).

The hardest requirement in the spec — *the answer contains no fact absent from
the supplied chunks* — is not a unit test. It needs a fixed corpus, a fixed
question set and expected-answer assertions.

    10 questions with a supporting document, all of which must be answered
    from it, and 5 with no supporting document, all of which must be refused.
    One hallucinated answer fails the suite.

**This suite is opt-in.** It is skipped unless `RUN_GROUNDING_EVAL=1`, because
it costs real Gemini calls and takes minutes. Run it deliberately:

    set RUN_GROUNDING_EVAL=1 && py -m pytest tests/test_grounding_eval.py -v

It is also skipped when no LLM provider is active. That is not a workaround —
without a key every answer comes from the templated fallback, which is grounded
by construction, so the suite would pass while testing nothing.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from tests.conftest import reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _enabled() -> tuple[bool, str]:
    if os.environ.get("RUN_GROUNDING_EVAL") != "1":
        return False, "set RUN_GROUNDING_EVAL=1 to run the grounding evaluation"
    from app.modules.ai import llm_client

    if not llm_client.is_configured():
        return False, (
            "no LLM provider is active — every answer would come from the templated "
            "fallback, which is grounded by construction, so the suite would prove nothing"
        )
    try:
        from app.vector.client import is_available

        if not is_available():
            return False, "Qdrant is not running"
    except Exception as exc:  # pragma: no cover
        return False, f"Qdrant check failed: {type(exc).__name__}"
    return True, ""


_RUN, _SKIP_REASON = _enabled()
pytestmark = pytest.mark.skipif(not _RUN, reason=_SKIP_REASON)


# --- The fixed corpus ---------------------------------------------------- #

CORPUS: dict[str, str] = {
    "leave-policy.txt": (
        "Annual Leave Policy\n\n"
        "All permanent employees are entitled to twenty-four days of paid annual leave "
        "per calendar year. Leave accrues monthly at two days per month. Unused leave "
        "may be carried forward for up to six months, after which it lapses.\n\n"
        "Leave requests must be submitted at least two weeks in advance through the HR "
        "portal. Managers must respond within three working days. Contractors are not "
        "eligible for paid annual leave.\n"
    ),
    "expense-policy.txt": (
        "Expense Reimbursement Policy\n\n"
        "Claims must be submitted within thirty days of the expense being incurred. "
        "Receipts are required for every claim above one hundred rupees. Claims are "
        "reimbursed with the following month's payroll.\n\n"
        "Air travel must be booked in economy class. Hotel stays are capped at four "
        "thousand rupees per night in metro cities and two thousand five hundred rupees "
        "elsewhere.\n"
    ),
    "onboarding.txt": (
        "New Employee Onboarding\n\n"
        "New joiners must provide a PAN card, proof of address, two passport photographs "
        "and their previous employer's relieving letter on or before their first day.\n\n"
        "The probation period is six months. A confirmation review is held in the fifth "
        "month. IT equipment is issued on day one and must be returned on exit.\n"
    ),
    "security-policy.txt": (
        "Information Security Policy\n\n"
        "Passwords must be at least twelve characters and are rotated every ninety days. "
        "Multi-factor authentication is mandatory for all administrative accounts.\n\n"
        "Company data may not be stored on personal devices. Security incidents must be "
        "reported to the security team within twenty-four hours of discovery.\n"
    ),
}

# Each: (question, source document, a fact that must appear in the answer).
GROUNDED_QUESTIONS: list[tuple[str, str, list[str]]] = [
    ("How many days of paid annual leave do permanent employees get?",
     "leave-policy.txt", ["twenty-four", "24"]),
    ("How long can unused leave be carried forward?",
     "leave-policy.txt", ["six month", "6 month"]),
    ("How far in advance must leave be requested?",
     "leave-policy.txt", ["two week", "2 week"]),
    ("Are contractors eligible for paid annual leave?",
     "leave-policy.txt", ["not eligible", "no", "not"]),
    ("What is the deadline for submitting an expense claim?",
     "expense-policy.txt", ["thirty day", "30 day"]),
    ("When is a receipt required for an expense claim?",
     "expense-policy.txt", ["one hundred", "100"]),
    ("What is the hotel cap in metro cities?",
     "expense-policy.txt", ["four thousand", "4,000", "4000"]),
    ("What documents must a new joiner provide?",
     "onboarding.txt", ["pan", "address", "photograph", "relieving"]),
    ("How long is the probation period?",
     "onboarding.txt", ["six month", "6 month"]),
    ("How often must passwords be rotated?",
     "security-policy.txt", ["ninety", "90"]),
]

# Nothing in the corpus supports any of these. All five must be refused.
UNSUPPORTED_QUESTIONS: list[str] = [
    "What is the boiling point of liquid nitrogen?",
    "How many vacation days does the Berlin office give?",
    "What is our company's revenue for the last quarter?",
    "Who is the current chief executive?",
    "What is the notice period for resignation?",
]

REFUSAL_MARKERS = ("could not find", "not find", "no information", "not available",
                   "does not contain", "no relevant")


# --- Fixtures ------------------------------------------------------------ #


@pytest.fixture(scope="module")
def evaluated():
    """Index the corpus once, then run every question through the real path."""
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "head")

    from app.core.database import SessionLocal
    from app.modules.auth.services.auth_service import AuthService
    from app.modules.documents.services.pipeline_service import ProcessingPipeline
    from app.storage.local import get_storage
    from app.utils.hashing import sha256_stream
    from app.vector.client import ensure_collection, get_client
    from config.settings import settings

    db = SessionLocal()
    reset_database(db)

    # Start from an empty collection so a leftover document cannot answer a
    # question the corpus is supposed to refuse.
    try:
        get_client().delete_collection(settings.QDRANT_COLLECTION)
    except Exception:
        pass
    ensure_collection()

    admin = AuthService(db).create_admin(
        email="eval@example.com", password="a-long-test-password", full_name="Eval"
    )

    from app.modules.documents.repositories.document_repository import DocumentRepository

    storage = get_storage()
    pipeline = ProcessingPipeline(db, storage)
    documents: dict[str, str] = {}

    for name, body in CORPUS.items():
        raw = body.encode()
        digest, size = sha256_stream(io.BytesIO(raw))
        key = f"eval/{uuid4()}.txt"
        storage.save(key, io.BytesIO(raw))

        document = DocumentRepository(db).create(
            file_name=name, original_file_name=name, file_type="txt",
            mime_type="text/plain", file_size=size, storage_key=key,
            file_hash=digest, created_by=admin.id,
        )
        db.commit()

        result = pipeline.run(document.id)
        assert result.status == "COMPLETED", f"{name} did not index: {result.error_code}"
        documents[name] = str(document.id)

    # Ask everything once; the tests below only assert over the results.
    from app.modules.chat.repositories.conversation_repository import ConversationRepository
    from app.modules.chat.services.chat_service import ChatService

    chat = ChatService(db)
    answers: dict[str, object] = {}

    for question, *_ in GROUNDED_QUESTIONS:
        conversation = ConversationRepository(db).create(admin.id)
        db.commit()
        answers[question] = chat.ask(conversation.id, admin.id, question)

    for question in UNSUPPORTED_QUESTIONS:
        conversation = ConversationRepository(db).create(admin.id)
        db.commit()
        answers[question] = chat.ask(conversation.id, admin.id, question)

    yield {"answers": answers, "documents": documents}

    db.close()


# --- Assertions ---------------------------------------------------------- #


@pytest.mark.parametrize(("question", "source", "expected"), GROUNDED_QUESTIONS,
                         ids=[q[:48] for q, _, _ in GROUNDED_QUESTIONS])
def test_supported_questions_are_answered_from_the_corpus(
    evaluated, question, source, expected
):
    answer = evaluated["answers"][question]

    assert answer.is_grounded is True, f"refused a question the corpus answers: {question}"
    assert answer.sources, "a grounded answer must cite something"

    body = answer.answer.lower()
    assert any(token.lower() in body for token in expected), (
        f"answer did not contain any of {expected}: {answer.answer}"
    )

    # The citation must point at the document that actually holds the fact.
    cited = {s.document_name for s in answer.sources}
    assert source in cited, f"cited {cited}, expected {source}"


@pytest.mark.parametrize("question", UNSUPPORTED_QUESTIONS,
                         ids=[q[:48] for q in UNSUPPORTED_QUESTIONS])
def test_unsupported_questions_are_refused(evaluated, question):
    """A single hallucinated answer here fails the suite (§34)."""
    answer = evaluated["answers"][question]

    assert answer.is_grounded is False, (
        f"answered a question nothing supports: {question}\n{answer.answer}"
    )
    assert answer.sources == []
    assert any(marker in answer.answer.lower() for marker in REFUSAL_MARKERS), answer.answer


def test_no_answer_cites_a_document_it_did_not_retrieve(evaluated):
    live = set(evaluated["documents"].values())
    for question, answer in evaluated["answers"].items():
        for source in answer.sources:
            assert str(source.document_id) in live, (
                f"cited an unknown document for: {question}"
            )


def test_the_refusal_rate_is_exactly_as_designed(evaluated):
    """Reported as one number, so a regression toward over-refusal or toward
    over-answering is visible at a glance."""
    grounded = sum(
        1 for q, *_ in GROUNDED_QUESTIONS if evaluated["answers"][q].is_grounded
    )
    refused = sum(
        1 for q in UNSUPPORTED_QUESTIONS if not evaluated["answers"][q].is_grounded
    )

    assert (grounded, refused) == (len(GROUNDED_QUESTIONS), len(UNSUPPORTED_QUESTIONS)), (
        f"{grounded}/{len(GROUNDED_QUESTIONS)} supported answered, "
        f"{refused}/{len(UNSUPPORTED_QUESTIONS)} unsupported refused"
    )

"""Chat and conversations against a real database.

Retrieval is stubbed in most tests so the grounding rules can be asserted
without a running Qdrant; the tests that exercise real retrieval are marked.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import make_tenant, otp_login, reset_database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

BASE = "/api/v1/chat"
LEAVE = (
    "Annual Leave Policy. All permanent employees are entitled to twenty-four days "
    "of paid annual leave per calendar year. Leave accrues monthly.\n"
)


def qdrant_running() -> bool:
    from app.vector.client import is_available

    return is_available()


needs_qdrant = pytest.mark.skipif(
    not qdrant_running(), reason="Qdrant is not running on QDRANT_URL"
)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture()
def db():
    from app.core.database import SessionLocal

    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _clean(db):
    reset_database(db)

    # Chat context is a cache; a leftover key from a previous test would make
    # a follow-up test pass for the wrong reason.
    try:
        from app.cache.redis_client import get_redis

        client = get_redis()
        for key in client.scan_iter("chat:*"):
            client.delete(key)
    except Exception:
        pass
    yield


@pytest.fixture()
def client():
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def tenant(db):
    """The organization these tests operate inside.

    Release 2: documents and conversations belong to an Organization Admin, so
    the Release 1 Super Admin is no longer a valid owner.
    """
    return make_tenant(db, email="admin@example.com")


@pytest.fixture()
def auth(client, tenant):
    """A signed-in Organization Admin, returning the Authorization header."""
    return otp_login(client, "admin@example.com")


@pytest.fixture()
def admin(tenant):
    """The Organization Admin row - the owner of anything these tests create."""
    return tenant[1]


@pytest.fixture()
def conversation(client, auth):
    r = client.post(f"{BASE}/conversations", headers=auth, json={"title": None})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def make_indexed_document(db, admin, *, name="leave.txt", chunks=("chunk text",)):
    """A COMPLETED document with chunk rows, so citations have something to resolve."""
    from app.modules.documents.models import (
        Document,
        DocumentChunk,
        DocumentProcessing,
        DocumentStatus,
    )

    document = Document(
        file_name=name, original_file_name=name, file_type="txt", mime_type="text/plain",
        file_size=100, storage_key=f"test/{uuid4()}.txt", file_hash="0" * 64,
        status=DocumentStatus.COMPLETED, created_by=admin.id, language="en",
        organization_id=admin.organization_id,
    )
    db.add(document)
    db.flush()
    db.add(DocumentProcessing(document_id=document.id))

    rows = []
    for index, body in enumerate(chunks):
        row = DocumentChunk(
            organization_id=document.organization_id,
            document_id=document.id, chunk_index=index, text=body, char_count=len(body),
            page_number=index + 1, vector_point_id=uuid4(), metadata_={},
        )
        db.add(row)
        rows.append(row)

    db.commit()
    db.refresh(document)
    return document, rows


def stub_retrieval(monkeypatch, chunks):
    """Replace Qdrant with a fixed result set."""
    from app.modules.chat.services import retrieval_service

    def fake_search(self, query, *, proposed_filters=None):
        return retrieval_service.RetrievalResult(
            chunks=list(chunks), query_used=query,
            applied_filters=proposed_filters or {},
        )

    monkeypatch.setattr(retrieval_service.RetrievalService, "search", fake_search)


def chunk_from(document, row, *, score=0.9, rank=1):
    from app.modules.chat.services.retrieval_service import RetrievedChunk

    return RetrievedChunk(
        chunk_id=str(row.id), document_id=str(document.id),
        document_name=document.original_file_name, text=row.text,
        score=score, rank=rank, page_number=row.page_number,
    )


def stub_agents(monkeypatch, *, answer="Employees receive 24 days.", grounded=True, cited=None):
    from app.modules.ai import query_planner, response_composer

    monkeypatch.setattr(query_planner.crew, "run_json", lambda **_: None)
    monkeypatch.setattr(
        response_composer.crew, "run_json",
        lambda **_: {"answer": answer, "is_grounded": grounded,
                     "cited_chunk_ids": cited if cited is not None else []},
    )


# --- Auth ---------------------------------------------------------------- #


def test_chat_requires_authentication(client):
    cid = uuid4()
    assert client.get(f"{BASE}/conversations").status_code == 401
    assert client.post(f"{BASE}/conversations", json={}).status_code == 401
    assert client.get(f"{BASE}/conversations/{cid}").status_code == 401
    assert client.delete(f"{BASE}/conversations/{cid}").status_code == 401
    assert client.post(f"{BASE}/conversations/{cid}/messages",
                       json={"content": "hi"}).status_code == 401


# --- Conversation lifecycle ---------------------------------------------- #


def test_new_conversation_is_not_in_the_sidebar_until_it_has_a_message(client, auth):
    """Clicking New Chat five times must not litter the list."""
    for _ in range(5):
        client.post(f"{BASE}/conversations", headers=auth, json={"title": None})

    assert client.get(f"{BASE}/conversations", headers=auth).json()["data"]["total"] == 0


def test_title_is_derived_from_the_first_message(
    client, auth, db, admin, conversation, monkeypatch
):
    document, rows = make_indexed_document(db, admin)
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch, cited=[str(rows[0].id)])

    client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                json={"content": "What is the annual leave entitlement?"})

    items = client.get(f"{BASE}/conversations", headers=auth).json()["data"]["items"]
    assert items[0]["title"] == "What is the annual leave entitlement?"
    assert items[0]["message_count"] == 2


def test_conversations_are_ordered_most_recent_first(
    client, auth, db, admin, monkeypatch
):
    document, rows = make_indexed_document(db, admin)
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch, cited=[str(rows[0].id)])

    ids = []
    for label in ("first", "second", "third"):
        cid = client.post(f"{BASE}/conversations", headers=auth,
                          json={"title": None}).json()["data"]["id"]
        client.post(f"{BASE}/conversations/{cid}/messages", headers=auth,
                    json={"content": f"{label} question about leave"})
        ids.append(cid)

    items = client.get(f"{BASE}/conversations", headers=auth).json()["data"]["items"]
    assert [item["id"] for item in items] == list(reversed(ids))


def test_rename_persists(client, auth, conversation):
    r = client.patch(f"{BASE}/conversations/{conversation}", headers=auth,
                     json={"title": "Onboarding questions"})
    assert r.status_code == 200
    assert r.json()["data"]["title"] == "Onboarding questions"


def test_delete_is_soft_and_keeps_the_messages(
    client, auth, db, admin, conversation, monkeypatch
):
    document, rows = make_indexed_document(db, admin)
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch, cited=[str(rows[0].id)])
    client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                json={"content": "leave policy?"})

    assert client.delete(f"{BASE}/conversations/{conversation}", headers=auth).status_code == 200

    assert client.get(f"{BASE}/conversations", headers=auth).json()["data"]["total"] == 0
    assert client.get(f"{BASE}/conversations/{conversation}", headers=auth).status_code == 404

    # The record of what was cited outlives the conversation.
    assert db.execute(
        text("SELECT count(*) FROM chat_messages WHERE conversation_id = :c"),
        {"c": conversation},
    ).scalar_one() == 2
    assert db.execute(text("SELECT count(*) FROM message_sources")).scalar_one() >= 1


def test_delete_drops_the_redis_context_key(
    client, auth, db, admin, conversation, monkeypatch
):
    from app.cache.redis_client import get_redis, ping

    if not ping():
        pytest.skip("Redis is not running")

    document, rows = make_indexed_document(db, admin)
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch, cited=[str(rows[0].id)])
    client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                json={"content": "leave policy?"})

    keys = [k for k in get_redis().scan_iter(f"chat:*:{conversation}")]
    assert keys, "context should have been cached"

    client.delete(f"{BASE}/conversations/{conversation}", headers=auth)
    assert [k for k in get_redis().scan_iter(f"chat:*:{conversation}")] == []


def test_unknown_conversation_is_404(client, auth):
    r = client.get(f"{BASE}/conversations/{uuid4()}", headers=auth)
    assert r.status_code == 404
    assert r.json()["error_code"] == "CONVERSATION_NOT_FOUND"


# --- Message validation -------------------------------------------------- #


def test_empty_message_is_rejected(client, auth, conversation):
    r = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                    json={"content": "   "})
    assert r.status_code == 400
    assert r.json()["error_code"] == "MESSAGE_EMPTY"


def test_over_length_message_is_rejected(client, auth, conversation):
    from config.settings import settings

    r = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                    json={"content": "x" * (settings.MAX_MESSAGE_CHARS + 1)})
    assert r.status_code == 400
    assert r.json()["error_code"] == "MESSAGE_TOO_LONG"


# --- Grounding ----------------------------------------------------------- #


def test_grounded_answer_carries_resolvable_citations(
    client, auth, db, admin, conversation, monkeypatch
):
    document, rows = make_indexed_document(
        db, admin, chunks=("Employees receive twenty-four days of annual leave.",)
    )
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch, answer="Employees receive 24 days.", cited=[str(rows[0].id)])

    r = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                    json={"content": "How much annual leave?"})

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["is_grounded"] is True
    assert len(data["sources"]) == 1

    source = data["sources"][0]
    assert source["document_id"] == str(document.id)
    assert source["chunk_id"] == str(rows[0].id)
    assert source["page"] == 1
    assert source["rank"] == 1

    # The citation is persisted, not just returned.
    assert db.execute(text("SELECT count(*) FROM message_sources")).scalar_one() == 1


def test_nothing_retrieved_returns_a_refusal_with_http_200(
    client, auth, conversation, monkeypatch
):
    """§34: a refusal is a correct outcome, not an error."""
    stub_retrieval(monkeypatch, [])
    stub_agents(monkeypatch)

    r = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                    json={"content": "What is the boiling point of nitrogen?"})

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["is_grounded"] is False
    assert data["sources"] == []
    assert data["error_code"] == "NO_RELEVANT_CONTEXT"
    assert "could not find" in data["answer"].lower()


def test_composer_is_not_called_when_nothing_is_retrieved(
    client, auth, conversation, monkeypatch
):
    """A model handed zero chunks and asked to answer will often oblige.

    Patched at `compose`, not at `crew.run_json`: both agents share the one
    crew module, so patching there cannot tell which agent ran.
    """
    from app.modules.chat.services import chat_service

    called = False

    def spy(question, chunks):
        nonlocal called
        called = True
        raise AssertionError("agent 3 must not run without retrieved context")

    stub_retrieval(monkeypatch, [])
    monkeypatch.setattr(chat_service.response_composer, "compose", spy)

    r = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                    json={"content": "anything"})

    assert called is False
    assert r.status_code == 200
    assert r.json()["data"]["is_grounded"] is False


def test_invented_citations_never_reach_the_response(
    client, auth, db, admin, conversation, monkeypatch
):
    document, rows = make_indexed_document(db, admin)
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch, cited=[str(rows[0].id), str(uuid4()), "not-even-a-uuid"])

    data = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                       json={"content": "leave?"}).json()["data"]

    assert [s["chunk_id"] for s in data["sources"]] == [str(rows[0].id)]


def test_a_citation_on_a_vanished_document_is_dropped(
    client, auth, db, admin, conversation, monkeypatch
):
    """A Qdrant payload can outlive its row; message_sources.document_id is a FK."""
    from app.modules.chat.services.retrieval_service import RetrievedChunk

    ghost = RetrievedChunk(
        chunk_id=str(uuid4()), document_id=str(uuid4()), document_name="Ghost.pdf",
        text="Something", score=0.9, rank=1, page_number=1,
    )
    stub_retrieval(monkeypatch, [ghost])
    stub_agents(monkeypatch, cited=[ghost.chunk_id])

    r = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                    json={"content": "leave?"})

    assert r.status_code == 200
    assert r.json()["data"]["sources"] == []


def test_composer_failure_still_produces_a_cited_answer(
    client, auth, db, admin, conversation, monkeypatch
):
    from app.modules.ai import query_planner, response_composer

    document, rows = make_indexed_document(
        db, admin, chunks=("Employees receive twenty-four days of annual leave.",)
    )
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    monkeypatch.setattr(query_planner.crew, "run_json", lambda **_: None)
    monkeypatch.setattr(response_composer.crew, "run_json", lambda **_: None)

    data = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                       json={"content": "How much leave?"}).json()["data"]

    assert data["is_grounded"] is True
    assert data["degraded"] is True
    assert len(data["sources"]) == 1
    assert "twenty-four" in data["answer"]


def test_search_outage_returns_503_but_keeps_the_question(
    client, auth, conversation, monkeypatch
):
    from app.modules.chat.services import retrieval_service

    def boom(self, query, *, proposed_filters=None):
        raise retrieval_service.SearchUnavailable("Qdrant is down")

    monkeypatch.setattr(retrieval_service.RetrievalService, "search", boom)
    stub_agents(monkeypatch)

    r = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                    json={"content": "leave policy?"})

    assert r.status_code == 503
    assert r.json()["error_code"] == "SEARCH_FAILED"

    # The transcript must not lie about what was asked.
    detail = client.get(f"{BASE}/conversations/{conversation}", headers=auth).json()["data"]
    assert detail["messages"][0]["content"] == "leave policy?"


# --- Filters proposed by agent 2 ----------------------------------------- #


def test_an_invented_category_filter_is_dropped(db, admin):
    """An unknown slug would silently eliminate every result."""
    from app.modules.chat.services.retrieval_service import RetrievalService

    applied, dropped = RetrievalService(db, admin.organization_id)._validate_filters(
        {"category_slug": "does-not-exist"}
    )
    assert applied == {}
    assert dropped == ["category_slug"]


def test_a_real_category_filter_is_applied(db, admin):
    from app.modules.documents.models import DocumentCategory
    from app.modules.chat.services.retrieval_service import RetrievalService

    db.add(DocumentCategory(slug="hr-policies", name="HR Policies", sort_order=1))
    db.commit()

    applied, dropped = RetrievalService(db, admin.organization_id)._validate_filters({"category_slug": "hr-policies"})
    assert applied == {"category_slug": "hr-policies"}
    assert dropped == []


def test_an_inactive_category_filter_is_dropped(db, admin):
    from app.modules.documents.models import DocumentCategory
    from app.modules.chat.services.retrieval_service import RetrievalService

    db.add(DocumentCategory(slug="retired", name="Retired", sort_order=1, is_active=False))
    db.commit()

    applied, dropped = RetrievalService(db, admin.organization_id)._validate_filters({"category_slug": "retired"})
    assert applied == {}
    assert dropped == ["category_slug"]


def test_a_language_name_instead_of_a_code_is_dropped(db, admin):
    from app.modules.chat.services.retrieval_service import RetrievalService

    applied, dropped = RetrievalService(db, admin.organization_id)._validate_filters({"language": "English"})
    assert applied == {}
    assert dropped == ["language"]


# --- Context ------------------------------------------------------------- #


def test_context_survives_a_redis_flush(client, auth, db, admin, conversation, monkeypatch):
    """§9: PostgreSQL is the source of truth; Redis is only a cache."""
    from app.cache.redis_client import get_redis, ping

    if not ping():
        pytest.skip("Redis is not running")

    document, rows = make_indexed_document(db, admin)
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch, cited=[str(rows[0].id)])

    client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                json={"content": "What is the leave policy?"})

    client_redis = get_redis()
    for key in client_redis.scan_iter(f"chat:*:{conversation}"):
        client_redis.delete(key)

    # The follow-up must still see the earlier turn, rebuilt from PostgreSQL.
    # Both agents share the one crew module, so the capture is keyed by role.
    from app.modules.ai import query_planner

    seen: dict[str, str] = {}

    def capture(**kwargs):
        seen[kwargs["role"]] = kwargs.get("task", "")
        return None

    monkeypatch.setattr(query_planner.crew, "run_json", capture)

    client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                json={"content": "What about for contractors?"})

    planner_prompt = seen[query_planner.ROLE]
    assert "leave policy" in planner_prompt, "context was not rebuilt from PostgreSQL"


def test_detail_returns_messages_with_their_sources(
    client, auth, db, admin, conversation, monkeypatch
):
    document, rows = make_indexed_document(db, admin)
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch, cited=[str(rows[0].id)])
    client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                json={"content": "leave?"})

    data = client.get(f"{BASE}/conversations/{conversation}", headers=auth).json()["data"]

    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    assert data["messages"][1]["sources"][0]["document_name"] == "leave.txt"


def test_a_citation_to_a_deleted_document_still_renders(
    client, auth, db, admin, conversation, monkeypatch
):
    document, rows = make_indexed_document(db, admin)
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch, cited=[str(rows[0].id)])
    client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                json={"content": "leave?"})

    db.execute(text("UPDATE documents SET deleted_at = now(), status = 'DELETED' WHERE id = :d"),
               {"d": str(document.id)})
    db.commit()

    data = client.get(f"{BASE}/conversations/{conversation}", headers=auth).json()["data"]
    source = data["messages"][1]["sources"][0]

    assert source["document_deleted"] is True
    assert source["document_name"] == "leave.txt"


# --- Real retrieval ------------------------------------------------------ #


@needs_qdrant
@pytest.mark.parametrize(
    ("indexed_status", "expect_grounded"),
    [("DELETED", False), ("FAILED", False), ("DUPLICATE", False), ("COMPLETED", True)],
)
def test_only_completed_documents_are_retrievable(
    client, auth, db, admin, conversation, monkeypatch, indexed_status, expect_grounded
):
    """Retrieval filters on status INSIDE the Qdrant query (§33).

    The COMPLETED case is the control: it proves the corpus and the question
    match, so a refusal in the other three is the filter working rather than an
    empty collection.
    """
    from app.modules.ai import query_planner
    from app.vector import repository as vector_repo
    from app.vector.client import collection_for, ensure_collection, get_client
    from app.vector.embeddings import embed_text
    from config.settings import settings

    # Start from an empty collection: leftovers from another suite would let a
    # refusal pass for the wrong reason. Only ever acceptable in a test, and
    # only against this tenant's own collection (ADR-006, superseded for the
    # multi-tenant case by one collection per organization).
    document, rows = make_indexed_document(db, admin, chunks=(LEAVE,))
    try:
        get_client().delete_collection(collection_for(document.organization_id))
    except Exception:
        pass
    ensure_collection(document.organization_id)
    vector_repo.upsert_chunks(
        document.organization_id,
        document.id, [embed_text(LEAVE)],
        [vector_repo.ChunkPayload(
            organization_id=str(document.organization_id),
            is_public=False,
            document_id=str(document.id), chunk_id=str(rows[0].id), chunk_index=0,
            text=LEAVE, document_name=document.original_file_name, document_type=None,
            category_slug=None, language="en", tags=[], page_number=1, section=None,
            status=indexed_status, created_at=document.created_at.isoformat(),
        )],
    )

    # Both agents fall back, so this asserts retrieval, not model behaviour.
    monkeypatch.setattr(query_planner.crew, "run_json", lambda **_: None)

    try:
        data = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                           json={"content": "How many days of annual leave?"}).json()["data"]

        assert data["is_grounded"] is expect_grounded
        assert bool(data["sources"]) is expect_grounded
    finally:
        vector_repo.delete_document_vectors(document.organization_id, document.id)


# --- Identity masking in answers ----------------------------------------- #


def test_an_answer_quoting_a_pan_card_is_masked(
    client, auth, db, admin, conversation, monkeypatch
):
    """The screenshot case: a question about a PAN card returned the number in
    plain text (docs/security/pii-handling.md § Display)."""
    document, rows = make_indexed_document(
        db, admin, name="pan-card.jpg",
        chunks=("INCOME TAX DEPARTMENT Name SURYA SINGH PAN APZPN1234A",),
    )
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch,
                answer="The PAN number on the card is APZPN1234A.",
                cited=[str(rows[0].id)])

    data = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                       json={"content": "what are the pan card details?"}).json()["data"]

    assert "APZPN1234A" not in data["answer"]
    assert "234A" in data["answer"], "the tail should survive so the card is identifiable"


def test_the_masked_answer_is_what_gets_persisted(
    client, auth, db, admin, conversation, monkeypatch
):
    """Masking at display time only would leave the raw number in the database
    and in the next follow-up's Redis context."""
    document, rows = make_indexed_document(db, admin, chunks=("PAN APZPN1234A",))
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    stub_agents(monkeypatch, answer="The number is APZPN1234A.", cited=[str(rows[0].id)])

    client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                json={"content": "pan number?"})

    stored = db.execute(
        text("""SELECT content FROM chat_messages
                WHERE conversation_id = :c AND role = 'ASSISTANT'"""),
        {"c": conversation},
    ).scalars().all()

    assert stored
    assert all("APZPN1234A" not in row for row in stored)


def test_the_templated_fallback_is_masked_too(
    client, auth, db, admin, conversation, monkeypatch
):
    """The fallback quotes raw chunk text, so it is the MORE likely leak."""
    from app.modules.ai import query_planner, response_composer

    document, rows = make_indexed_document(
        db, admin, chunks=("Permanent Account Number APZPN1234A issued 1988",))
    stub_retrieval(monkeypatch, [chunk_from(document, rows[0])])
    monkeypatch.setattr(query_planner.crew, "run_json", lambda **_: None)
    monkeypatch.setattr(response_composer.crew, "run_json", lambda **_: None)

    data = client.post(f"{BASE}/conversations/{conversation}/messages", headers=auth,
                       json={"content": "pan details"}).json()["data"]

    assert data["degraded"] is True
    assert "APZPN1234A" not in data["answer"]

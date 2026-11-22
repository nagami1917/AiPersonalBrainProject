"""End-to-end chat contract against a real, migrated Postgres.

These tests need DATABASE_URL pointing at a database where migrations and
seed.sql have been applied (make db-reset). They are skipped otherwise, so the
unit suite stays fast and offline.

They assert the two things that matter most:
  1. an unanswerable question NEVER calls the LLM and returns grounded=false;
  2. a fabricated citation marker produces no citation row.
"""

from __future__ import annotations

import asyncpg
import pytest
from app.ai.grounding import REFUSAL_TEMPLATE
from app.core.config import get_settings
from app.core.security import AuthUser
from app.schemas.chat import ChatRequest
from app.services import chat_service

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def _alice() -> AuthUser:
    return AuthUser(claims={"sub": ALICE, "role": "authenticated"}, token="test")


async def _pool_or_skip():
    try:
        dsn = get_settings().database_url
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        await pool.fetchval("select 1")
        return pool
    except Exception as exc:  # no database in this environment
        pytest.skip(f"database unavailable: {exc}")


@pytest.fixture
async def conn():
    pool = await _pool_or_skip()
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("select set_config('role','authenticated',true)")
        await connection.execute("select set_config('request.jwt.claim.sub',$1,true)", ALICE)
        await connection.execute("delete from public.conversations where user_id=$1", ALICE)
        yield connection
    await pool.close()


class TestGroundedAnswers:
    async def test_answerable_question_cites_a_real_chunk(self, conn, embedder, llm):
        request = ChatRequest(message="Where is the service role key stored?")
        _message_id, _conversation_id, answer = await chat_service.answer(
            conn,
            user_id=ALICE,
            request=request,
            embedder=embedder,
            llm=llm,
            settings=get_settings(),
        )
        assert answer.grounded is True
        assert answer.citations
        for citation in answer.citations:
            exists = await conn.fetchval(
                "select 1 from public.chunks where id=$1 and user_id=$2",
                citation.chunk_id,
                ALICE,
            )
            assert exists, "citation must point at a chunk the user actually owns"

    async def test_unanswerable_question_never_calls_the_llm(self, conn, embedder, llm):
        request = ChatRequest(message="zzzz qqqq unrelated gibberish xxyy")
        _message_id, _conversation_id, answer = await chat_service.answer(
            conn,
            user_id=ALICE,
            request=request,
            embedder=embedder,
            llm=llm,
            settings=get_settings(),
        )
        assert answer.grounded is False
        assert answer.content == REFUSAL_TEMPLATE
        assert answer.citations == []
        assert llm.calls == [], "the model must not be asked when retrieval is empty"

    async def test_fabricated_marker_persists_no_citation(self, conn, embedder):
        class FabricatingLLM:
            model = "test-llm"

            async def complete(self, system: str, user: str) -> str:
                return "A claim with a fake source [99]."

            async def stream(self, system, user):  # type: ignore[no-untyped-def]
                yield ""

        request = ChatRequest(message="Where is the service role key stored?")
        message_id, _conversation_id, answer = await chat_service.answer(
            conn,
            user_id=ALICE,
            request=request,
            embedder=embedder,
            llm=FabricatingLLM(),  # type: ignore[arg-type]
            settings=get_settings(),
        )
        assert answer.grounded is False
        rows = await conn.fetchval(
            "select count(*) from public.message_citations where message_id=$1", message_id
        )
        assert rows == 0
        assert "[99]" not in answer.content


class TestTenantIsolation:
    async def test_bob_cannot_retrieve_alices_chunks(self, conn, embedder):
        """Retrieval is filtered twice: explicitly by user_id and by RLS."""
        from app.services.retrieval_service import retrieve

        result = await retrieve(
            conn,
            user_id=BOB,
            query="service role key Fly secrets deployment",
            embedder=embedder,
            settings=get_settings(),
        )
        alice_document_ids = {
            "dddddddd-0000-0000-0000-000000000001",
            "dddddddd-0000-0000-0000-000000000002",
            "dddddddd-0000-0000-0000-000000000003",
        }
        assert all(hit.document_id not in alice_document_ids for hit in result.hits)

    async def test_cannot_open_another_users_conversation(self, conn, embedder, llm):
        from app.core.errors import NotFound

        conversation_id = await conn.fetchval(
            "insert into public.conversations (user_id, title) values ($1,$2) returning id",
            ALICE,
            "alice only",
        )
        bob = AuthUser(claims={"sub": BOB, "role": "authenticated"}, token="test")
        with pytest.raises(NotFound):
            await chat_service.answer(
                conn,
                user_id=bob.id,
                request=ChatRequest(conversation_id=str(conversation_id), message="hi"),
                embedder=embedder,
                llm=llm,
                settings=get_settings(),
            )

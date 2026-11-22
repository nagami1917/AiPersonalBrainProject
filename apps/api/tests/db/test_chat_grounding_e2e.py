"""End-to-end chat contract against a real, migrated Postgres.

These tests need DATABASE_URL pointing at a database where migrations and
seed.sql have been applied (make db-reset). They are skipped otherwise, so the
unit suite stays fast and offline.

They assert the four things that matter most:
  1. an unanswerable question NEVER calls the LLM and returns grounded=false;
  2. a fabricated citation marker produces no citation row;
  3. the message_sources row names the correct parent table (note vs document);
  4. a member of another workspace retrieves nothing of Alice's.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest
from app.ai.rag import REFUSAL_TEMPLATE
from app.core.config import get_settings
from app.core.security import AuthUser
from app.schemas.chat import ChatRequest
from app.services import chat_service

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def _user(user_id: str) -> AuthUser:
    return AuthUser(claims={"sub": user_id, "role": "authenticated"}, token="test")


async def _pool_or_skip() -> asyncpg.Pool:
    try:
        dsn = get_settings().database_url
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        await pool.fetchval("select 1")
        return pool
    except Exception as exc:  # no database in this environment
        pytest.skip(f"database unavailable: {exc}")


async def _personal_workspace(conn: asyncpg.Connection, user_id: str) -> str:
    workspace_id = await conn.fetchval(
        "select id from public.workspaces where owner_id=$1 and personal limit 1", user_id
    )
    if workspace_id is None:
        pytest.skip("seed data missing: run `make db-reset` first")
    return str(workspace_id)


@asynccontextmanager
async def _session(pool: asyncpg.Pool, user_id: str) -> AsyncIterator[asyncpg.Connection]:
    """A connection that RLS treats as `user_id`, inside a rolled-back transaction.

    One connection per user is the point: switching identity mid-connection would
    make the isolation assertions meaningless, because the policies would already
    have been evaluated under a different role.
    """
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("select set_config('role','authenticated',true)")
        await connection.execute("select set_config('request.jwt.claim.sub',$1,true)", user_id)
        yield connection


@pytest.fixture
async def pool():
    pool = await _pool_or_skip()
    async with _session(pool, ALICE) as connection:
        await connection.execute(
            "delete from public.conversations where workspace_id in "
            "(select id from public.workspaces where owner_id=$1)",
            ALICE,
        )
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def conn(pool):
    """A connection acting as Alice."""
    async with _session(pool, ALICE) as connection:
        yield connection


@pytest.fixture
async def bob_conn(pool):
    """A separate connection acting as Bob — a different tenant."""
    async with _session(pool, BOB) as connection:
        yield connection


class TestGroundedAnswers:
    async def test_answerable_question_cites_a_real_chunk(self, conn, embedder, llm):
        workspace_id = await _personal_workspace(conn, ALICE)
        request = ChatRequest(message="Where is the service role key stored?")

        _message_id, _conversation_id, answer = await chat_service.answer(
            conn,
            workspace_id=workspace_id,
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
                "select 1 from public.document_chunks where id=$1 and workspace_id=$2",
                citation.chunk_id,
                workspace_id,
            )
            assert exists, "citation must point at a chunk in the caller's workspace"

    async def test_citation_records_the_parent_kind(self, conn, embedder, llm):
        """A note chunk and a document chunk must both resolve to a real parent."""
        workspace_id = await _personal_workspace(conn, ALICE)
        request = ChatRequest(message="Where is the service role key stored?")

        message_id, _conversation_id, answer = await chat_service.answer(
            conn,
            workspace_id=workspace_id,
            user_id=ALICE,
            request=request,
            embedder=embedder,
            llm=llm,
            settings=get_settings(),
        )
        assert answer.citations

        rows = await conn.fetch(
            "select source_kind, note_id, document_id from public.message_sources "
            "where message_id=$1",
            message_id,
        )
        assert rows
        for row in rows:
            # The XOR CHECK in migration 0005 guarantees this, but assert it here
            # too so a future schema edit cannot silently break citations.
            assert (row["note_id"] is None) != (row["document_id"] is None)
            assert row["source_kind"] == ("note" if row["note_id"] else "document")

    async def test_unanswerable_question_never_calls_the_llm(self, conn, embedder, llm):
        workspace_id = await _personal_workspace(conn, ALICE)
        request = ChatRequest(message="zzzz qqqq unrelated gibberish xxyy")

        _message_id, _conversation_id, answer = await chat_service.answer(
            conn,
            workspace_id=workspace_id,
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

        workspace_id = await _personal_workspace(conn, ALICE)
        request = ChatRequest(message="Where is the service role key stored?")

        message_id, _conversation_id, answer = await chat_service.answer(
            conn,
            workspace_id=workspace_id,
            user_id=ALICE,
            request=request,
            embedder=embedder,
            llm=FabricatingLLM(),  # type: ignore[arg-type]
            settings=get_settings(),
        )

        assert answer.grounded is False
        rows = await conn.fetchval(
            "select count(*) from public.message_sources where message_id=$1", message_id
        )
        assert rows == 0
        assert "[99]" not in answer.content


class TestWorkspaceIsolation:
    async def test_other_workspace_retrieves_nothing(self, conn, bob_conn, embedder):
        """Retrieval is filtered twice: explicitly by workspace_id and by RLS.

        Bob's vocabulary is deliberately near-identical to Alice's in seed.sql, so
        a lexical/keyword match alone would surface her rows. Anything of hers
        that appears here is a real tenancy bug.
        """
        from app.services.retrieval_service import retrieve

        alice_ws = await _personal_workspace(conn, ALICE)
        bob_ws = await _personal_workspace(bob_conn, BOB)
        assert alice_ws != bob_ws

        result = await retrieve(
            bob_conn,
            workspace_id=bob_ws,
            query="service role key Fly secrets deployment",
            embedder=embedder,
            settings=get_settings(),
        )

        # Bob's own near-identical note may surface; Alice's must not.
        alice_ids = {
            str(r)
            for r in await conn.fetch("select id from public.notes where workspace_id=$1", alice_ws)
        } | {
            str(r)
            for r in await conn.fetch(
                "select id from public.documents where workspace_id=$1", alice_ws
            )
        }
        assert all(hit.source_id not in alice_ids for hit in result.hits)

        # And the SQL gate alone agrees: Alice's workspace through Bob's
        # connection returns nothing at all.
        from app.repositories import search as search_repo

        rows = await search_repo.hybrid(
            bob_conn,
            workspace_id=alice_ws,
            query="service role key Fly secrets deployment",
            embedding=None,
            match_count=10,
            candidate_count=20,
            vector_weight=0.6,
            keyword_weight=0.4,
            rrf_k=60,
            min_similarity=0.0,
        )
        assert rows == []

    async def test_member_of_another_workspace_cannot_write(self, conn, bob_conn, embedder, llm):
        """Alice's workspace id in Bob's URL is a 404, not a 403."""
        from app.core import tenancy
        from app.core.errors import Forbidden, NotFound

        alice_ws = await _personal_workspace(conn, ALICE)

        with pytest.raises(NotFound):
            await tenancy.resolve_workspace(bob_conn, user_id=BOB, workspace_id=alice_ws)

        # Even if a caller bypasses resolution, RLS refuses the insert. The
        # attempted write runs in a savepoint so the rejected statement does not
        # poison the test transaction.
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            async with bob_conn.transaction():
                await bob_conn.execute(
                    """insert into public.notes (workspace_id, created_by, title, content)
                       values ($1,$2,'smuggled','should never persist')""",
                    alice_ws,
                    BOB,
                )

        # And a read returns nothing rather than an error.
        assert (
            await bob_conn.fetchval(
                "select count(*) from public.notes where workspace_id=$1", alice_ws
            )
            == 0
        )

        assert not issubclass(Forbidden, NotFound)

    async def test_cannot_open_another_workspaces_conversation(self, conn, bob_conn, embedder, llm):
        from app.core.errors import NotFound

        alice_ws = await _personal_workspace(conn, ALICE)
        bob_ws = await _personal_workspace(bob_conn, BOB)

        conversation_id = await conn.fetchval(
            """insert into public.conversations (workspace_id, created_by, title)
               values ($1,$2,$3) returning id""",
            alice_ws,
            ALICE,
            "alice only",
        )

        # Bob, in Bob's workspace, naming Alice's conversation id → 404, not 403.
        with pytest.raises(NotFound):
            await chat_service.answer(
                bob_conn,
                workspace_id=bob_ws,
                user_id=BOB,
                request=ChatRequest(conversation_id=str(conversation_id), message="hi"),
                embedder=embedder,
                llm=llm,
                settings=get_settings(),
            )

    async def test_random_workspace_id_is_not_resolvable(self, conn):
        from app.core import tenancy
        from app.core.errors import NotFound

        with pytest.raises(NotFound):
            await tenancy.resolve_workspace(conn, user_id=ALICE, workspace_id=str(uuid.uuid4()))

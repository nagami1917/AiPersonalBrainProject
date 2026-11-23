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

from tests.db.support import rolled_back

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
    # `rolled_back`, not `async with connection.transaction()`: the latter
    # commits on a clean exit, so this fixture was persisting every test's
    # conversation and messages despite its docstring claiming otherwise.
    #
    # The transaction MUST start before the set_config calls: they pass
    # is_local=true, which scopes them to the current transaction, and outside
    # one they apply to their own statement only and evaporate. Getting this
    # order wrong silently leaves the connection as its default superuser,
    # RLS never applies, and the isolation tests pass for the wrong reason.
    async with pool.acquire() as connection, rolled_back(connection) as tx:
        await tx.execute("select set_config('role','authenticated',true)")
        await tx.execute("select set_config('request.jwt.claim.sub',$1,true)", user_id)
        yield tx


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


class TestStreamingGrounding:
    """The streaming path must obey the same grounding contract as `/chat`.

    ## The bug these tests exist for

    `stream_answer` yielded every model delta directly to the client and only
    resolved citations afterwards:

        async for delta in llm.stream(...):
            yield ("delta", {"text": delta})        # already on the user's screen
        answer = resolve_citations("".join(buffer), sources)   # too late

    Observed against the seeded corpus, with a model that invents a marker:

        USER SAW: "The service role key is in Fly secrets [9]. I couldn't find
                   anything in your knowledge base about that. …"

    Two contradictory statements in one bubble. The persisted message was the
    refusal (correct), so the database and the screen disagreed — and the screen
    is what the user reads. An answer with no marker at all was never retracted,
    because there was no complete marker to notice.

    `/chat/stream` is the path the UI uses, so this was the common case, not an
    edge one.

    ## What is asserted

    The user-visible text is what matters, so these tests collect the `delta`
    events and assert on the concatenation — not on the citations payload, which
    was already correct while the stream was not.
    """

    class _FabricatingLLM:
        """A model that invents a citation id it was never given."""

        model = "test-llm"

        def __init__(self, reply: str) -> None:
            self.reply = reply
            self.calls: list[tuple[str, str]] = []

        async def complete(self, system: str, user: str) -> str:
            self.calls.append((system, user))
            return self.reply

        async def stream(self, system: str, user: str):  # type: ignore[no-untyped-def]
            self.calls.append((system, user))
            for token in self.reply.split(" "):
                yield token + " "

    @staticmethod
    async def _run(conn, workspace_id: str, question: str, reply: str, embedder):
        llm = TestStreamingGrounding._FabricatingLLM(reply)
        shown: list[str] = []
        citations: dict | None = None
        async for event, data in chat_service.stream_answer(
            conn,
            workspace_id=workspace_id,
            user_id=ALICE,
            request=ChatRequest(message=question),
            embedder=embedder,
            llm=llm,
            settings=get_settings(),
        ):
            if event == "delta":
                shown.append(data["text"])
            elif event == "citations":
                citations = data
        return "".join(shown), citations, llm

    async def test_a_fabricated_citation_never_reaches_the_user(self, conn, embedder):
        workspace_id = await _personal_workspace(conn, ALICE)
        shown, citations, _ = await self._run(
            conn,
            workspace_id,
            "Where is the Supabase service role key stored?",
            "The service role key is in Fly secrets [9].",
            embedder,
        )
        assert citations is not None and citations["grounded"] is False
        assert citations["citations"] == []
        assert "[9]" not in shown, "a fabricated marker must never be displayed"
        assert "Fly secrets" not in shown, (
            "unverified model text must not be shown at all; before the fix the user "
            "read the fabrication and the refusal in the same bubble"
        )

    async def test_an_answer_without_any_citation_never_reaches_the_user(self, conn, embedder):
        """The case a post-hoc marker check cannot catch: there is nothing to retract."""
        workspace_id = await _personal_workspace(conn, ALICE)
        shown, citations, _ = await self._run(
            conn,
            workspace_id,
            "Where is the Supabase service role key stored?",
            "The service role key is in Fly secrets.",
            embedder,
        )
        assert citations is not None and citations["grounded"] is False
        assert "Fly secrets" not in shown

    async def test_a_grounded_answer_still_streams(self, conn, embedder):
        """The gate must not defeat streaming for the normal case."""
        workspace_id = await _personal_workspace(conn, ALICE)
        shown, citations, _ = await self._run(
            conn,
            workspace_id,
            "Where is the Supabase service role key stored?",
            "The service role key is stored in Fly secrets [1].",
            embedder,
        )
        assert citations is not None
        assert citations["grounded"] is True, "the fake model cited a real source"
        assert "Fly secrets" in shown, "a grounded answer must reach the user"
        assert len(citations["citations"]) == 1

    async def test_the_persisted_message_matches_what_was_shown(self, conn, embedder):
        """Screen and database must agree — the original bug made them disagree."""
        workspace_id = await _personal_workspace(conn, ALICE)
        shown, citations, _ = await self._run(
            conn,
            workspace_id,
            "Where is the Supabase service role key stored?",
            "The service role key is in Fly secrets [9].",
            embedder,
        )
        # The assistant row, not the user's question — both live in chat_messages
        # and "most recent" picks whichever was written last.
        stored = await conn.fetchval(
            """
            select content from public.chat_messages
             where role = 'assistant'
             order by created_at desc limit 1
            """
        )
        assert (
            stored.strip() == shown.strip()
        ), f"the user saw {shown!r} but the conversation stored {stored!r}"


class TestConversationTitles:
    """Conversations must arrive with a title, not a NULL.

    Nothing used to write `conversations.title`, so the list endpoint returned a
    column of `null` and the history was unnavigable. Set at creation from the
    opening question so an abandoned first turn is still findable.
    """

    async def test_a_new_conversation_is_titled_from_its_first_question(self, conn, embedder, llm):
        workspace_id = await _personal_workspace(conn, ALICE)
        llm.reply = "Retrieval uses RRF [1]."
        message_id, conversation_id, _ = await chat_service.answer(
            conn,
            workspace_id=workspace_id,
            user_id=ALICE,
            request=ChatRequest(message="What did I decide about retrieval scoring?"),
            embedder=embedder,
            llm=llm,
            settings=get_settings(),
        )
        title = await conn.fetchval(
            "select title from public.conversations where id=$1", conversation_id
        )
        assert title == "What did I decide about retrieval scoring?"

    async def test_a_greeting_still_produces_a_listable_conversation(self, conn, embedder, llm):
        workspace_id = await _personal_workspace(conn, ALICE)
        _, conversation_id, _ = await chat_service.answer(
            conn,
            workspace_id=workspace_id,
            user_id=ALICE,
            request=ChatRequest(message="hi"),
            embedder=embedder,
            llm=llm,
            settings=get_settings(),
        )
        title = await conn.fetchval(
            "select title from public.conversations where id=$1", conversation_id
        )
        assert title == "New conversation", "a greeting must not leave the row untitled"

    async def test_a_follow_up_turn_does_not_retitle_the_conversation(self, conn, embedder, llm):
        """The title is the opening question, not the most recent one."""
        workspace_id = await _personal_workspace(conn, ALICE)
        _, conversation_id, _ = await chat_service.answer(
            conn,
            workspace_id=workspace_id,
            user_id=ALICE,
            request=ChatRequest(message="What did I decide about retrieval scoring?"),
            embedder=embedder,
            llm=llm,
            settings=get_settings(),
        )
        await chat_service.answer(
            conn,
            workspace_id=workspace_id,
            user_id=ALICE,
            request=ChatRequest(
                message="And what about the job queue?", conversation_id=conversation_id
            ),
            embedder=embedder,
            llm=llm,
            settings=get_settings(),
        )
        title = await conn.fetchval(
            "select title from public.conversations where id=$1", conversation_id
        )
        assert title == "What did I decide about retrieval scoring?"


class TestCitationRelevance:
    """Citations must carry a usable relevance value, not a raw RRF score.

    RRF values are ~0.008–0.03, so the UI's `score * 100` rendered every source as
    "1% match" — including the best one. The number was real and the label was a
    fabrication, which is worse than no number.
    """

    async def test_the_best_citation_has_full_relative_relevance(self, conn, embedder):
        workspace_id = await _personal_workspace(conn, ALICE)
        from app.services.retrieval_service import retrieve

        result = await retrieve(
            conn,
            workspace_id=workspace_id,
            query="Supabase service role key Fly secrets",
            embedder=embedder,
            settings=get_settings(),
            match_count=5,
        )
        assert result.sources, "the seeded corpus should answer this"

        from app.ai.rag.answer_generator import resolve_citations

        # Cite every source so the normalisation sees the whole set.
        answer = resolve_citations(
            " ".join(f"[{s.marker}]" for s in result.sources), result.sources
        )
        assert answer.citations
        import math

        assert math.isclose(
            max(c.relevance for c in answer.citations), 1.0
        ), "the best source must normalise to 1.0, not to its raw 0.01 RRF value"
        assert all(0.0 <= c.relevance <= 1.0 for c in answer.citations)
        assert all(c.relevance_kind == "relative" for c in answer.citations)

    async def test_relevance_is_not_the_raw_fused_score(self, conn, embedder):
        """Guards the regression explicitly: the old value was ~0.01 for everything."""
        workspace_id = await _personal_workspace(conn, ALICE)
        from app.services.retrieval_service import retrieve

        result = await retrieve(
            conn,
            workspace_id=workspace_id,
            query="Supabase service role key Fly secrets",
            embedder=embedder,
            settings=get_settings(),
            match_count=5,
        )
        from app.ai.rag.answer_generator import resolve_citations

        answer = resolve_citations(
            " ".join(f"[{s.marker}]" for s in result.sources), result.sources
        )
        assert (
            max(c.relevance for c in answer.citations) > 0.5
        ), "raw RRF scores are ~0.01; a citation reporting 0.01 is the bug this guards"

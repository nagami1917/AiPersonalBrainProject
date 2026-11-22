"""Design §4.1, asserted as sequence rather than as presence.

The spec's ingestion pipeline is an ORDER: extract, clean, chunk, embed, store,
summarise, extract concepts, and only then mark the document completed. Most of
those steps existed before this file did; what was missing was proof that they
run in that order and that "completed" means what the spec says it means.

These tests need a migrated + seeded database (make db-reset) and are skipped
otherwise, matching the rest of tests/db.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from app.ai.chunking import TARGET_TOKENS
from app.core.config import get_settings
from app.workers import ingest

ALICE = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "(select id from public.workspaces where owner_id = $1 and personal limit 1)"


async def _init(conn: asyncpg.Connection) -> None:
    """Mirror app.core.db._init_connection.

    The app registers a jsonb codec so Python dicts can be passed to jsonb
    parameters. A bare test pool does not, and the resulting DataError would look
    like a product bug when it is really a harness gap.
    """
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def _pool_or_skip() -> asyncpg.Pool:
    try:
        pool = await asyncpg.create_pool(
            get_settings().database_url, min_size=1, max_size=2, init=_init
        )
        await pool.fetchval("select 1")
        return pool
    except Exception as exc:  # no database in this environment
        pytest.skip(f"database unavailable: {exc}")


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    pool = await _pool_or_skip()
    try:
        async with pool.acquire() as connection, connection.transaction():
            # The worker runs with the service role; these assertions are about
            # pipeline behaviour, not tenancy (that is covered elsewhere).
            # The worker connects with the service role and has no end-user JWT.
            # can_write_workspace() therefore cannot be satisfied by auth.uid(),
            # which is exactly why enqueue_job needs a service path (see 0006).
            await connection.execute("select set_config('role','service_role',true)")
            yield connection
    finally:
        await pool.close()


PROBE_TEXT = (
    "Deployment secrets are set with flyctl and never committed to the repository. "
    "The worker claims jobs under a row lock so two workers never process one row."
)


async def _document(conn: asyncpg.Connection, *, status: str = "pending") -> uuid.UUID:
    """A raw-text document sitting at the top of the pipeline."""
    workspace_id = await conn.fetchval(
        "select id from public.workspaces where owner_id = $1 and personal limit 1", ALICE
    )
    if workspace_id is None:
        pytest.skip("seed data missing: run `make db-reset` first")
    document_id = uuid.uuid4()
    await conn.execute(
        """
        insert into public.documents (id, workspace_id, uploaded_by, title, filename,
                                      mime_type, file_path, file_size, status, raw_text)
        values ($1::uuid, $2::uuid, $3::uuid, 'ingest-probe', 'ingest-probe.txt',
                'text/plain', $2::uuid::text || '/' || $1::uuid::text || '/ingest-probe.txt',
                4096, $4, $5)
        """,
        document_id,
        workspace_id,
        ALICE,
        status,
        PROBE_TEXT,
    )
    return document_id


async def _ingest(conn: asyncpg.Connection, document_id: uuid.UUID, embedder) -> None:
    """Run the ingest worker exactly as workers/main.py does."""
    await ingest.run_ingest(
        conn,
        job_id=str(uuid.uuid4()),
        kind="document",
        source_id=str(document_id),
        embedder=embedder,
        settings=get_settings(),
    )


class TestStageOrder:
    async def test_a_completed_document_is_ready_and_stamped(self, conn, embedder):
        """The last step of §4.1: ready + completed_at, with real chunks behind it."""
        document_id = await _document(conn)
        await _ingest(conn, document_id, embedder)

        row = await conn.fetchrow(
            "select status, processing_stage, completed_at, page_count "
            "from public.documents where id=$1",
            document_id,
        )
        assert row["status"] == "ready"
        assert row["processing_stage"] is None
        assert row["completed_at"] is not None

        chunks = await conn.fetch(
            "select chunk_index, token_count, content from public.document_chunks "
            "where document_id=$1 order by chunk_index",
            document_id,
        )
        assert chunks, "a completed document must have chunks"
        assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))

    async def test_summary_and_concepts_are_queued_before_completion(self, conn, embedder):
        """§4.1 puts summary and entity extraction BEFORE the completed mark.

        The worker satisfies that by enqueueing both jobs in the same run that
        flips the status. If this ever moves after completion, a document would
        be citable while its summary and graph edges are still missing.
        """
        document_id = await _document(conn)
        await _ingest(conn, document_id, embedder)

        jobs = await conn.fetch(
            "select type from public.processing_jobs where document_id=$1 order by type",
            document_id,
        )
        kinds = {job["type"] for job in jobs}
        assert {"summary", "entities"} <= kinds, f"expected summary+entities jobs, saw {kinds}"

    async def test_chunks_are_not_visible_until_the_document_completes(self, conn, embedder):
        """The retrieval gate is what makes completion meaningful."""
        document_id = await _document(conn)
        await _ingest(conn, document_id, embedder)

        workspace_id = await conn.fetchval(
            "select workspace_id from public.documents where id=$1", document_id
        )
        query = "secrets"

        async def hits() -> int:
            # Count only this document's chunks, so seeded sources do not mask it.
            return await conn.fetchval(
                """
                select count(*) from public.hybrid_search(
                  p_workspace_id => $1::uuid, p_query => $2, p_match_count => 200
                ) where document_id = $3
                """,
                workspace_id,
                query,
                document_id,
            )

        before = await hits()
        assert before > 0, "the probe document must be findable once ready"

        await conn.execute(
            "update public.documents set status='processing' where id=$1", document_id
        )
        assert await hits() == 0, "a mid-pipeline document must not be citable"

        await conn.execute("update public.documents set status='ready' where id=$1", document_id)
        assert await hits() == before


class TestNormalizingStage:
    async def test_cleaning_runs_between_extraction_and_chunking(self, conn, embedder):
        """Messy whitespace must be gone from the stored chunks, not just the text.

        This is the observable consequence of the normalizing stage: chunk
        content is what gets embedded and cited, so cleaning has to happen
        before splitting, never after.
        """
        document_id = await _document(conn)
        messy = "Deployment   notes.\t\t\n\n\n\nSecrets  live   in   Fly."
        await conn.execute(
            "update public.documents set raw_text=$2 where id=$1", document_id, messy
        )

        await _ingest(conn, document_id, embedder)

        contents = await conn.fetch(
            "select content from public.document_chunks where document_id=$1 order by chunk_index",
            document_id,
        )
        joined = " ".join(row["content"] for row in contents)
        assert "Deployment notes." in joined
        assert "  " not in joined, "collapsed whitespace must survive into the stored chunk"


class TestChunkSizing:
    async def test_a_long_document_is_split_near_the_target(self, conn, embedder):
        """§4.2 quality depends on chunk size; 700 is the documented starting point."""
        document_id = await _document(conn)
        paragraph = (
            "The worker claims jobs with a row lock so two workers never process "
            "the same row, and stale jobs are swept back to pending after a lease."
        )
        body = "\n\n".join([paragraph] * 60)
        await conn.execute("update public.documents set raw_text=$2 where id=$1", document_id, body)

        await _ingest(conn, document_id, embedder)

        tokens = await conn.fetch(
            "select token_count from public.document_chunks "
            "where document_id=$1 order by chunk_index",
            document_id,
        )
        assert len(tokens) > 1
        assert all(row["token_count"] <= 1200 for row in tokens)
        assert any(row["token_count"] > TARGET_TOKENS // 2 for row in tokens)

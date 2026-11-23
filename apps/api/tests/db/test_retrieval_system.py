"""Retrieval behaviour against a real, migrated Postgres.

## Why these are database tests and not unit tests

Retrieval is SQL. The arms, the fusion, the filters and the tenancy gate all live
inside `public.hybrid_search`, so a unit test with a fake connection would assert
that the Python passes arguments — not that the right rows come back. The
properties this file cares about (a filter actually filtering, a tag actually
scoping, two tenants not seeing each other) are only observable end to end.

## What each class pins down

  * `TestTagFilter`            — the filter that used to be silently dropped.
  * `TestMetadataFilters`      — kind / source_ids / collection / date scoping.
  * `TestWorkspaceIsolation`   — the tenancy boundary, from the retrieval side.
  * `TestRankingAndDedup`      — ordering, per-source diversity, duplicate removal.
  * `TestKeywordArm`           — literal matching the vector arm cannot do.
  * `TestEmbeddingMetadata`    — model/dimension bookkeeping and drift detection.

Everything here is created inside the fixture's rolled-back transaction, except
the JWT-scoped tests, which use the same real-pool path the API uses.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from app.ai.retrieval import Hit, SearchFilters, dedupe, limit_per_source, order, search
from app.ai.retrieval.reranker import normalize_fused_scores, normalize_scores
from app.core.config import get_settings
from app.services import reindex_service

from tests.conftest import FakeEmbedder
from tests.db.support import rolled_back

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


async def _pool_or_skip() -> asyncpg.Pool:
    """A per-test pool, not the app's cached one.

    `get_pool` memoises a pool bound to the event loop that first created it.
    pytest-asyncio gives each test its own loop, so reusing the cached pool makes
    every test after the first fail with "Event loop is closed" — and because the
    skip is inside a try, it failed *silently* as a skip. A fresh pool per test is
    slower and honest.
    """
    try:
        pool = await asyncpg.create_pool(
            get_settings().database_url, min_size=1, max_size=2, init=_init_jsonb
        )
        await pool.fetchval("select 1")
        return pool
    except Exception as exc:  # no database in this environment
        pytest.skip(f"database unavailable: {exc}")


async def _init_jsonb(conn: asyncpg.Connection) -> None:
    """The app's jsonb codec, which a bare pool does not install."""
    import json

    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    """A connection inside an always-rolled-back transaction.

    Does NOT close the pool: `get_pool` is process-wide and cached, so closing it
    here left every subsequent test in the run with "pool is closed" and silently
    skipping — a fixture that makes the suite lie about what it ran. The pool is
    torn down by the module that created it (or by the app's lifespan).
    """
    pool = await _pool_or_skip()
    try:
        async with pool.acquire() as connection, rolled_back(connection):
            yield connection
    finally:
        await pool.close()


@pytest.fixture
async def embedder() -> FakeEmbedder:
    return FakeEmbedder()


async def _workspace(conn: asyncpg.Connection, user_id: str = ALICE) -> str:
    workspace_id = await conn.fetchval(
        "select id from public.workspaces where owner_id=$1 and personal limit 1", user_id
    )
    if workspace_id is None:
        pytest.skip("seed data missing: run `make db-reset` first")
    return str(workspace_id)


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in embedding) + "]"


async def _tag(conn: asyncpg.Connection, workspace_id: str, name: str) -> str:
    tag_id = await conn.fetchval(
        """
        insert into public.tags (workspace_id, name) values ($1,$2)
        on conflict (workspace_id, lower(btrim(name))) do update set name=excluded.name
        returning id
        """,
        workspace_id,
        name,
    )
    return str(tag_id)


async def _note(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    title: str,
    text: str,
    embedder: FakeEmbedder,
    tag_ids: list[str] | None = None,
    collection_id: str | None = None,
) -> tuple[str, str]:
    """A ready note with one embedded chunk. Returns (note_id, chunk_id).

    Written directly rather than through the ingest worker: these tests are about
    retrieval, and routing every fixture through the pipeline would make a
    retrieval failure indistinguishable from an ingest failure.
    """
    note_id = await conn.fetchval(
        """
        insert into public.notes
          (workspace_id, created_by, title, content, content_format, status,
           collection_id, embedding_model, embedding_dim)
        values ($1,$2,$3,$4,'markdown','ready',$5,'text-embedding-3-small',1536)
        returning id
        """,
        workspace_id,
        ALICE,
        title,
        text,
        collection_id,
    )
    vector = (await embedder.embed([text]))[0]
    chunk_id = await conn.fetchval(
        """
        insert into public.document_chunks
          (workspace_id, note_id, chunk_index, content, token_count,
           embedding, embedding_model, embedding_dim, version)
        values ($1,$2,0,$3,$4,$5::vector,'text-embedding-3-small',1536,1)
        returning id
        """,
        workspace_id,
        note_id,
        text,
        max(1, len(text.split())),
        _vector_literal(vector),
    )
    for tag_id in tag_ids or []:
        await conn.execute(
            "insert into public.note_tags (workspace_id, note_id, tag_id) values ($1,$2,$3)",
            workspace_id,
            note_id,
            tag_id,
        )
    return str(note_id), str(chunk_id)


class TestTagFilter:
    """The `tag` filter used to be accepted by the API and dropped by retrieval.

    `SearchFilters.tag` existed in the request schema, but `_filters()` in
    `retrieval_service` never copied it, the retrieval `SearchFilters` had no such
    field, and `hybrid_search` had no such parameter. A request with
    `{"filters": {"tag": "finance"}}` therefore returned everything — no error, no
    empty result, just unfiltered data. These tests would all have failed before
    the fix, and the first one fails in the most dangerous way: it returns the
    documents the caller asked to exclude.
    """

    async def test_tag_narrows_results_to_the_tagged_source(self, conn, embedder):
        workspace_id = await _workspace(conn)
        tagged = await _tag(conn, workspace_id, f"finance-{uuid.uuid4().hex[:8]}")
        await _note(
            conn,
            workspace_id=workspace_id,
            title="tagged-finance",
            text="quarterly budget forecast revenue",
            embedder=embedder,
            tag_ids=[tagged],
        )
        await _note(
            conn,
            workspace_id=workspace_id,
            title="untagged-finance",
            text="quarterly budget forecast revenue",
            embedder=embedder,
        )

        unfiltered = await search(
            conn,
            workspace_id=workspace_id,
            query="quarterly budget forecast",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=20,
        )
        tag_name = await conn.fetchval("select name from public.tags where id=$1", tagged)
        filtered = await search(
            conn,
            workspace_id=workspace_id,
            query="quarterly budget forecast",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=20,
            filters=SearchFilters(tag=tag_name),
        )

        titles = {h.source_title for h in unfiltered}
        assert {"tagged-finance", "untagged-finance"} <= titles, "both must match the query"
        assert [h.source_title for h in filtered] == [
            "tagged-finance"
        ], "the tag filter must exclude the untagged note; it used to be ignored entirely"

    async def test_tag_matching_is_case_insensitive(self, conn, embedder):
        workspace_id = await _workspace(conn)
        name = f"Research-{uuid.uuid4().hex[:6]}"
        tag_id = await _tag(conn, workspace_id, name)
        await _note(
            conn,
            workspace_id=workspace_id,
            title="case-insensitive-probe",
            text="photosynthesis chlorophyll chloroplast",
            embedder=embedder,
            tag_ids=[tag_id],
        )

        for candidate in (name, name.lower(), name.upper(), f"  {name}  "):
            hits = await search(
                conn,
                workspace_id=workspace_id,
                query="photosynthesis chlorophyll",
                mode="keyword",
                embedder=embedder,
                settings=get_settings(),
                match_count=10,
                filters=SearchFilters(tag=candidate),
            )
            assert hits, f"tag {candidate!r} should have matched"
            assert all(h.source_title == "case-insensitive-probe" for h in hits)

    async def test_an_unknown_tag_matches_nothing(self, conn, embedder):
        """A typo'd tag must return zero, not everything.

        This is the difference between a filter and a suggestion. If an unknown
        tag fell back to "no filter", a caller would silently get the whole
        workspace back and believe it had been scoped.
        """
        workspace_id = await _workspace(conn)
        await _note(
            conn,
            workspace_id=workspace_id,
            title="unrelated-tag-probe",
            text="kubernetes deployment manifest yaml",
            embedder=embedder,
        )
        hits = await search(
            conn,
            workspace_id=workspace_id,
            query="kubernetes deployment manifest",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=10,
            filters=SearchFilters(tag=f"does-not-exist-{uuid.uuid4().hex[:8]}"),
        )
        assert hits == []

    async def test_a_tag_from_another_workspace_matches_nothing(self, conn, embedder):
        """Tag names are workspace-scoped, so a foreign name cannot scope into it."""
        alice_ws = await _workspace(conn, ALICE)
        foreign_tag = await _tag(conn, alice_ws, f"foreign-{uuid.uuid4().hex[:8]}")
        foreign_name = await conn.fetchval("select name from public.tags where id=$1", foreign_tag)

        bob_ws = await _workspace(conn, BOB)
        await _note(
            conn,
            workspace_id=bob_ws,
            title="bob-tagged-probe",
            text="bob private deployment secrets",
            embedder=embedder,
        )
        hits = await search(
            conn,
            workspace_id=bob_ws,
            query="deployment secrets",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=10,
            filters=SearchFilters(tag=foreign_name),
        )
        assert hits == [], "another workspace's tag must not scope a search"


class TestTagFilterThroughTheService:
    """The same tag filter, through the layer where it was actually dropped.

    The tests above call `ai.retrieval.search` directly, so they exercise the SQL
    and the dataclass. They PASS with the bug present, because the bug was never in
    either of those — `retrieval_service._filters()` translated the HTTP schema into
    the retrieval dataclass and simply omitted `tag`, so the value vanished before
    the SQL ever saw it. Keeping the SQL-level tests is worthwhile (they pin the
    migration) but they are not evidence for the wiring.

    These go through `retrieve()`, the function `chat_service` actually calls.
    """

    async def test_tag_survives_the_request_to_retrieval_translation(self, conn, embedder):
        from app.schemas.chat import SearchFilters as RequestFilters
        from app.services.retrieval_service import _filters

        translated = _filters(RequestFilters(tag="Finance"))
        assert translated.tag == "Finance", (
            "the tag must survive the schema→retrieval translation; when it did not, "
            "a tag-filtered request silently returned the unfiltered workspace"
        )

    async def test_retrieve_honours_a_tag_filter(self, conn, embedder):
        from app.schemas.chat import SearchFilters as RequestFilters
        from app.services.retrieval_service import retrieve

        workspace_id = await _workspace(conn)
        name = f"service-{uuid.uuid4().hex[:8]}"
        tag_id = await _tag(conn, workspace_id, name)
        await _note(
            conn,
            workspace_id=workspace_id,
            title="service-tagged",
            text="annual procurement audit findings",
            embedder=embedder,
            tag_ids=[tag_id],
        )
        await _note(
            conn,
            workspace_id=workspace_id,
            title="service-untagged",
            text="annual procurement audit findings",
            embedder=embedder,
        )

        unfiltered = await retrieve(
            conn,
            workspace_id=workspace_id,
            query="annual procurement audit",
            embedder=embedder,
            settings=get_settings(),
            match_count=20,
            mode="keyword",
        )
        assert {"service-tagged", "service-untagged"} <= {
            s.document_title for s in unfiltered.sources
        }

        filtered = await retrieve(
            conn,
            workspace_id=workspace_id,
            query="annual procurement audit",
            embedder=embedder,
            settings=get_settings(),
            match_count=20,
            mode="keyword",
            filters=RequestFilters(tag=name),
        )
        assert [s.document_title for s in filtered.sources] == [
            "service-tagged"
        ], "retrieve() must apply the tag; it used to drop it on the floor"


class TestMetadataFilters:
    async def test_kind_filter_separates_notes_from_documents(self, conn, embedder):
        workspace_id = await _workspace(conn)
        await _note(
            conn,
            workspace_id=workspace_id,
            title="kind-note",
            text="retention policy discussion",
            embedder=embedder,
        )
        await conn.execute(
            """
            insert into public.documents
              (id, workspace_id, uploaded_by, title, filename, mime_type,
               file_path, file_size, status, raw_text, embedding_model, embedding_dim)
            values ($1,$2,$3,'kind-document','kind.txt','text/plain',
                    'p/kind.txt', 64, 'ready', 'retention policy discussion',
                    'text-embedding-3-small', 1536)
            """,
            uuid.uuid4(),
            workspace_id,
            ALICE,
        )
        doc_id = await conn.fetchval(
            "select id from public.documents where title='kind-document' and workspace_id=$1",
            workspace_id,
        )
        vector = (await embedder.embed(["retention policy discussion"]))[0]
        await conn.execute(
            """
            insert into public.document_chunks
              (workspace_id, document_id, chunk_index, content, token_count,
               embedding, embedding_model, embedding_dim, version)
            values ($1,$2,0,'retention policy discussion',3,$3::vector,
                    'text-embedding-3-small',1536,1)
            """,
            workspace_id,
            doc_id,
            _vector_literal(vector),
        )

        notes = await search(
            conn,
            workspace_id=workspace_id,
            query="retention policy",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=20,
            filters=SearchFilters(kind="note"),
        )
        docs = await search(
            conn,
            workspace_id=workspace_id,
            query="retention policy",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=20,
            filters=SearchFilters(kind="document"),
        )
        assert notes and all(h.source_kind == "note" for h in notes)
        assert docs and all(h.source_kind == "document" for h in docs)

    async def test_source_ids_scope_to_named_sources(self, conn, embedder):
        workspace_id = await _workspace(conn)
        wanted, _ = await _note(
            conn,
            workspace_id=workspace_id,
            title="wanted-source",
            text="incident postmortem timeline",
            embedder=embedder,
        )
        await _note(
            conn,
            workspace_id=workspace_id,
            title="unwanted-source",
            text="incident postmortem timeline",
            embedder=embedder,
        )
        hits = await search(
            conn,
            workspace_id=workspace_id,
            query="incident postmortem",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=20,
            filters=SearchFilters(source_ids=[wanted]),
        )
        assert [h.source_id for h in hits] == [wanted]

    async def test_source_ids_accept_a_uuid_string_list(self, conn, embedder):
        """The filter crosses an asyncpg uuid[] parameter; strings must work.

        `search_repo.hybrid` passes a Python list straight into `uuid[]`. asyncpg
        will not coerce `str` to `uuid` inside an array, so a caller holding JSON
        ids (which is every HTTP client) would hit a DataError. Pinned here because
        the failure only appears at the database boundary.
        """
        workspace_id = await _workspace(conn)
        note_id, _ = await _note(
            conn,
            workspace_id=workspace_id,
            title="uuidparam-probe",
            text="budget reconciliation ledger",
            embedder=embedder,
        )
        hits = await search(
            conn,
            workspace_id=workspace_id,
            query="budget reconciliation",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=10,
            filters=SearchFilters(source_ids=[note_id]),  # str, not uuid.UUID
        )
        assert hits

    async def test_a_deleted_source_is_not_retrievable(self, conn, embedder):
        """`deleted_at` is a retrieval gate, not only a list filter."""
        workspace_id = await _workspace(conn)
        note_id, _ = await _note(
            conn,
            workspace_id=workspace_id,
            title="trashed-source",
            text="archived sprint retrospective notes",
            embedder=embedder,
        )
        before = await search(
            conn,
            workspace_id=workspace_id,
            query="sprint retrospective",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=10,
        )
        assert before

        await conn.execute("update public.notes set deleted_at=now() where id=$1", note_id)

        after = await search(
            conn,
            workspace_id=workspace_id,
            query="sprint retrospective",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=10,
        )
        assert after == [], "a trashed note must stop being citable immediately"

    async def test_a_pending_source_is_not_retrievable(self, conn, embedder):
        """Only `ready` sources are citable: a half-indexed document is not a source.

        Chunks are written before the status flips to ready, so this ordering is
        what keeps a partially processed document out of an answer.
        """
        workspace_id = await _workspace(conn)
        note_id, _ = await _note(
            conn,
            workspace_id=workspace_id,
            title="still-processing",
            text="onboarding checklist for new engineers",
            embedder=embedder,
        )
        await conn.execute("update public.notes set status='processing' where id=$1", note_id)

        hits = await search(
            conn,
            workspace_id=workspace_id,
            query="onboarding checklist",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=10,
        )
        assert hits == []


class TestWorkspaceIsolation:
    async def test_one_workspace_cannot_retrieve_anothers_chunks(self, conn, embedder):
        bob_ws = await _workspace(conn, BOB)
        await _note(
            conn,
            workspace_id=bob_ws,
            title="bob-only-secret",
            text="the production database password rotation schedule",
            embedder=embedder,
        )
        alice_ws = await _workspace(conn, ALICE)

        hits = await search(
            conn,
            workspace_id=alice_ws,
            query="production database password rotation",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=20,
        )
        assert all(h.source_title != "bob-only-secret" for h in hits)

    async def test_retrieval_is_scoped_by_argument_even_without_rls(self, conn, embedder):
        """Defence in depth: the function's own predicate, not only RLS.

        The fixture connection runs as a superuser, which bypasses RLS entirely.
        Retrieval must still scope by `p_workspace_id` — the parameter is the
        filter, and RLS is the backstop, not the mechanism.
        """
        bob_ws = await _workspace(conn, BOB)
        await _note(
            conn,
            workspace_id=bob_ws,
            title="superuser-visible-secret",
            text="alice should never see this payroll spreadsheet",
            embedder=embedder,
        )
        alice_ws = await _workspace(conn, ALICE)

        as_superuser = await search(
            conn,
            workspace_id=alice_ws,
            query="payroll spreadsheet",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=20,
        )
        assert all(
            h.source_title != "superuser-visible-secret" for h in as_superuser
        ), "p_workspace_id must scope retrieval on its own; RLS cannot be the only guard"


class TestKeywordArm:
    async def test_an_exact_identifier_is_found_by_the_keyword_arm(self, conn, embedder):
        """The reason the keyword arm exists: a rare literal token.

        A hashed bag-of-words embedder scores this low on cosine (the token appears
        in one chunk) while the keyword arm finds it exactly. That is the same
        asymmetry the real system has with error codes and function names.
        """
        workspace_id = await _workspace(conn)
        await _note(
            conn,
            workspace_id=workspace_id,
            title="error-code-note",
            text="the deployment failed with NX-4021 during the migration step",
            embedder=embedder,
        )
        hits = await search(
            conn,
            workspace_id=workspace_id,
            query="NX-4021",
            mode="keyword",
            embedder=embedder,
            settings=get_settings(),
            match_count=10,
        )
        assert hits, "an exact identifier must be retrievable"
        assert hits[0].source_title == "error-code-note"
        assert hits[0].keyword_rank == 1

    async def test_keyword_mode_does_not_embed_the_query(self, conn, embedder):
        """Keyword-only retrieval costs one round trip and no model call."""

        class CountingEmbedder(FakeEmbedder):
            calls = 0

            async def embed(self, texts):  # type: ignore[no-untyped-def]
                CountingEmbedder.calls += 1
                return await super().embed(texts)

        workspace_id = await _workspace(conn)
        await _note(
            conn,
            workspace_id=workspace_id,
            title="no-embedding-probe",
            text="zebra crossing protocol",
            embedder=embedder,
        )
        counting = CountingEmbedder()
        await search(
            conn,
            workspace_id=workspace_id,
            query="zebra crossing",
            mode="keyword",
            embedder=counting,
            settings=get_settings(),
            match_count=5,
        )
        assert CountingEmbedder.calls == 0, "keyword mode must not call the embedding provider"


class TestRankingAndDedup:
    """The ordering stage, exercised as pure functions plus one live run.

    These are unit-level assertions deliberately placed beside the database ones:
    they describe the same pipeline, and separating them into another file would
    make it easy to change the ranking contract without noticing.
    """

    @staticmethod
    def _hit(title: str, content: str, score: float, *, source_id: str = "s1") -> Hit:
        return Hit(
            chunk_id=str(uuid.uuid4()),
            source_kind="note",
            source_id=source_id,
            source_title=title,
            content=content,
            heading_path=None,
            page_number=None,
            char_start=None,
            char_end=None,
            vector_rank=1,
            keyword_rank=None,
            fused_score=score,
        )

    def test_near_duplicate_passages_are_collapsed(self):
        a = self._hit("a", "the quick brown fox jumps over the lazy dog", 0.03)
        b = self._hit("a", "the quick brown fox jumps over the lazy dog", 0.028)
        assert len(dedupe([a, b])) == 1

    def test_different_content_is_not_collapsed(self):
        a = self._hit("a", "the quick brown fox jumps over the lazy dog", 0.03)
        b = self._hit("b", "a completely unrelated sentence about databases", 0.02)
        assert len(dedupe([a, b])) == 2

    def test_one_source_cannot_crowd_out_the_rest(self):
        hits = [
            self._hit("dominant", f"passage {i} alpha beta gamma", 0.03 - i / 100) for i in range(8)
        ]
        hits.append(self._hit("other", "a distinct passage delta epsilon", 0.001, source_id="s2"))
        kept = limit_per_source(hits)
        assert (
            sum(1 for h in kept if h.source_title == "dominant") < 8
        ), "a single long source must not take every slot"
        assert any(h.source_title == "other" for h in kept)

    def test_a_single_source_is_returned_whole(self):
        """The cap must not truncate the only source — that would return less than asked."""
        hits = [self._hit("only", f"passage {i} unique text {i}", 0.03) for i in range(5)]
        assert len(limit_per_source(hits)) == 5

    def test_ordering_dedupes_before_truncating(self):
        """Truncating first would return fewer results than requested for no reason."""
        dupes = [self._hit("a", "identical passage text here", 0.03) for _ in range(3)]
        # Distinct wording, not distinct digits: `_tokens` drops words of three
        # characters or fewer, so "number 1" and "number 2" share their token set
        # and would be deduped as near-identical — which is correct behaviour and
        # would make this test assert the opposite of what it means.
        uniques = [
            self._hit("u1", "distinct passage about databases", 0.02),
            self._hit("u2", "another passage about weather patterns", 0.019),
            self._hit("u3", "a third passage regarding music theory", 0.018),
        ]
        ordered = order(dupes + uniques, limit=4)
        assert len(ordered) == 4, "dedupe must happen before the limit is applied"

    def test_fused_scores_are_rescaled_for_display(self):
        hits = [self._hit("a", "x", 0.012), self._hit("b", "y", 0.006)]
        assert normalize_fused_scores(hits) == [1.0, 0.5]

    def test_normalize_scores_handles_empty_and_zero(self):
        assert normalize_scores([]) == []
        assert normalize_scores([0.0, 0.0]) == [0.0, 0.0]

    async def test_a_live_search_returns_distinct_chunks(self, conn, embedder):
        workspace_id = await _workspace(conn)
        await _note(
            conn,
            workspace_id=workspace_id,
            title="dedupe-live",
            text="continuous integration pipeline configuration",
            embedder=embedder,
        )
        hits = await search(
            conn,
            workspace_id=workspace_id,
            query="continuous integration pipeline",
            mode="hybrid",
            embedder=embedder,
            settings=get_settings(),
            match_count=20,
        )
        chunk_ids = [h.chunk_id for h in hits]
        assert len(chunk_ids) == len(set(chunk_ids)), "one row per chunk"


class TestEmbeddingMetadata:
    """Model/dimension bookkeeping — the 'reprocess when the model changes' path."""

    async def test_chunks_record_their_model_and_dimension(self, conn, embedder):
        workspace_id = await _workspace(conn)
        _, chunk_id = await _note(
            conn,
            workspace_id=workspace_id,
            title="metadata-probe",
            text="vector metadata bookkeeping",
            embedder=embedder,
        )
        row = await conn.fetchrow(
            "select embedding_model, embedding_dim from public.document_chunks where id=$1",
            chunk_id,
        )
        assert row["embedding_model"] == "text-embedding-3-small"
        assert row["embedding_dim"] == 1536

    async def test_a_wrong_dimension_is_rejected_by_the_database(self, conn, embedder):
        """The CHECK constraint, not application code, is the last line of defence.

        A migration or a hand-written insert that claims the wrong width would
        otherwise put a vector in the index that no query vector can be compared
        against.
        """
        workspace_id = await _workspace(conn)
        note_id, _ = await _note(
            conn,
            workspace_id=workspace_id,
            title="bad-dim-probe",
            text="dimension mismatch probe",
            embedder=embedder,
        )
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                insert into public.document_chunks
                  (workspace_id, note_id, chunk_index, content, token_count,
                   embedding, embedding_model, embedding_dim, version)
                values ($1,$2,9,'bad dim',2,$3::vector,'text-embedding-3-small',3072,1)
                """,
                workspace_id,
                note_id,
                _vector_literal([0.1] * 1536),
            )

    async def test_drift_is_detected_and_reported(self, conn, embedder):
        """A corpus indexed with another model must be visibly stale.

        This is the state that used to be invisible: old vectors stay in the index
        and keep being ranked, so retrieval returns worse results rather than
        failing.
        """
        workspace_id = await _workspace(conn)
        _, chunk_id = await _note(
            conn,
            workspace_id=workspace_id,
            title="drift-probe",
            text="an older model embedded this passage",
            embedder=embedder,
        )
        # Simulate a model swap: this chunk now claims to come from a model the
        # configuration no longer uses.
        await conn.execute(
            "update public.document_chunks"
            " set embedding_model='text-embedding-ada-002' where id=$1",
            chunk_id,
        )

        plan = await reindex_service.report(
            conn, workspace_id=workspace_id, settings=get_settings()
        )
        assert plan.needs_reindex, "stale vectors must be detectable"
        assert any(m.model == "text-embedding-ada-002" and not m.is_current for m in plan.models)
        assert plan.stale_chunks >= 1

    async def test_reindex_queues_one_job_per_source(self, conn, embedder):
        workspace_id = await _workspace(conn)
        _, chunk_id = await _note(
            conn,
            workspace_id=workspace_id,
            title="reindex-probe",
            text="this will need reprocessing",
            embedder=embedder,
        )
        await conn.execute(
            "update public.document_chunks"
            " set embedding_model='text-embedding-ada-002' where id=$1",
            chunk_id,
        )

        plan = await reindex_service.enqueue(
            conn, workspace_id=workspace_id, settings=get_settings()
        )
        assert plan.queued_jobs >= 1
        queued = await conn.fetchval(
            """
            select count(*) from public.processing_jobs
             where workspace_id=$1 and type='reindex' and status in ('pending','queued')
            """,
            workspace_id,
        )
        assert queued >= 1

    async def test_reindex_is_idempotent_per_source(self, conn, embedder):
        """Queueing twice must not double the work."""
        workspace_id = await _workspace(conn)
        _, chunk_id = await _note(
            conn,
            workspace_id=workspace_id,
            title="idempotent-reindex-probe",
            text="queue me twice",
            embedder=embedder,
        )
        await conn.execute(
            "update public.document_chunks"
            " set embedding_model='text-embedding-ada-002' where id=$1",
            chunk_id,
        )
        first = await reindex_service.enqueue(
            conn, workspace_id=workspace_id, settings=get_settings()
        )
        second = await reindex_service.enqueue(
            conn, workspace_id=workspace_id, settings=get_settings()
        )
        assert first.queued_jobs >= 1
        assert second.queued_jobs == 0, "an already-queued source must not be queued again"

    async def test_a_current_corpus_needs_no_reindex(self, conn, embedder):
        workspace_id = await _workspace(conn)
        await _note(
            conn,
            workspace_id=workspace_id,
            title="current-model-probe",
            text="embedded with the configured model",
            embedder=embedder,
        )
        plan = await reindex_service.report(
            conn, workspace_id=workspace_id, settings=get_settings()
        )
        current = [m for m in plan.models if m.model == "text-embedding-3-small"]
        assert current, "the note above must appear in the corpus report"
        assert all(m.is_current for m in current)

"""The retrieval arms, tested as policy rather than as SQL.

The scans live in Postgres, so a unit test cannot execute them. What it *can* pin
is the part the arms actually decide: which weights they pass, whether an
embedding is computed at all, and that a keyword-only query never touches the
embedding provider. Those are the choices that get silently copied wrong when
someone adds a fourth mode.

`test_retrieval_ordering.py` covers the reordering stage in Python; the SQL
function itself is exercised by `tests/db`.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.ai.retrieval import Hit, SearchFilters, keyword, search, vector
from app.core.config import Settings
from app.core.errors import ProviderUnavailable

WORKSPACE = "11111111-1111-1111-1111-111111111111"
QUERY = "where is the service role key stored"


class _Row(dict):
    """Stand-in for an asyncpg Record.

    Subclasses dict because `row_to_hit` calls `dict(row)` and then `.get()` —
    exactly what a real Record supports. A dataclass would look tidier and would
    be the wrong shape, hiding the fact that the mapping is dict-based.
    """

    def __init__(
        self,
        chunk_id: str = "chunk-1",
        source_kind: str = "document",
        note_id: str | None = None,
        document_id: str | None = "doc-1",
        source_title: str = "Thesis",
        content: str = "The service role key lives in Fly secrets.",
        heading_path: str | None = "Deployment",
        page_number: int | None = 3,
        char_start: int | None = 0,
        char_end: int | None = 46,
        vector_rank: int | None = 1,
        keyword_rank: int | None = None,
        fused_score: float | None = 0.032,
    ) -> None:
        super().__init__(
            chunk_id=chunk_id,
            source_kind=source_kind,
            note_id=note_id,
            document_id=document_id,
            source_title=source_title,
            content=content,
            heading_path=heading_path,
            page_number=page_number,
            char_start=char_start,
            char_end=char_end,
            vector_rank=vector_rank,
            keyword_rank=keyword_rank,
            fused_score=fused_score,
        )


class _Recorder:
    """Captures the kwargs each repository call receives."""

    def __init__(self, rows: list[_Row] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.rows = rows if rows is not None else [_Row()]

    async def __call__(self, conn, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self.rows


class _Embedder:
    model = "text-embedding-3-small"
    dim = 1536

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        self.calls.append(list(texts))
        if self.fail:
            raise ProviderUnavailable("down")
        return [[0.1] * 1536 for _ in texts]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql://x",
        supabase_url="http://x",
        supabase_anon_key="a",
        supabase_service_role_key="s",
        supabase_jwt_secret="test-secret-with-at-least-32-characters",
        openai_api_key="k",
        candidate_k=40,
        retrieval_k=8,
        min_similarity=0.25,
    )


@pytest.fixture
def repo(monkeypatch) -> _Recorder:  # type: ignore[no-untyped-def]
    import app.repositories.search as search_repo

    recorder = _Recorder()
    monkeypatch.setattr(search_repo, "hybrid", recorder)
    # the arms import the module, not the function, so patching the module works
    import app.ai.retrieval.vector_search as vs

    monkeypatch.setattr(vs, "search_repo", search_repo)
    import app.ai.retrieval.keyword_search as ks

    monkeypatch.setattr(ks, "search_repo", search_repo)
    import app.ai.retrieval as pkg

    monkeypatch.setattr(pkg, "search_repo", search_repo)
    return recorder


class TestVectorArm:
    async def test_it_sends_no_keyword_weight(self, repo, settings):
        await vector(
            None,
            workspace_id=WORKSPACE,
            query=QUERY,
            embedding=[0.1] * 1536,
            match_count=8,
            candidate_count=40,
            min_similarity=0.25,
        )
        call = repo.calls[0]
        assert call["keyword_weight"] == 0.0
        assert call["vector_weight"] == 1.0
        assert call["embedding"] is not None

    async def test_the_similarity_floor_is_forwarded(self, repo, settings):
        """Dropping this would let unrelated rows through on any query."""
        await vector(
            None,
            workspace_id=WORKSPACE,
            query=QUERY,
            embedding=[0.1] * 1536,
            match_count=8,
            candidate_count=40,
            min_similarity=0.42,
        )
        assert repo.calls[0]["min_similarity"] == 0.42

    async def test_filters_reach_the_candidate_set(self, repo, settings):
        await vector(
            None,
            workspace_id=WORKSPACE,
            query=QUERY,
            embedding=[0.1] * 1536,
            match_count=8,
            candidate_count=40,
            min_similarity=0.25,
            filters=SearchFilters(kind="note", collection_id="c-1"),
        )
        call = repo.calls[0]
        assert call["kind"] == "note"
        assert call["collection_id"] == "c-1"


class TestKeywordArm:
    async def test_it_computes_no_embedding(self, repo, settings):
        """An exact-term search must cost one round trip, not a model call."""
        embedder = _Embedder()
        await keyword(
            None,
            workspace_id=WORKSPACE,
            query="SKIP LOCKED",
            match_count=8,
            candidate_count=40,
        )
        assert embedder.calls == []
        assert repo.calls[0]["embedding"] is None

    async def test_it_sends_no_vector_weight(self, repo, settings):
        await keyword(None, workspace_id=WORKSPACE, query="x", match_count=8, candidate_count=40)
        call = repo.calls[0]
        assert call["vector_weight"] == 0.0
        assert call["keyword_weight"] == 1.0


class TestModeDispatch:
    async def test_keyword_mode_never_embeds(self, repo, settings):
        embedder = _Embedder()
        await search(
            None,
            workspace_id=WORKSPACE,
            query=QUERY,
            mode="keyword",
            embedder=embedder,
            settings=settings,
            match_count=8,
        )
        assert embedder.calls == []

    async def test_semantic_mode_embeds_once(self, repo, settings):
        embedder = _Embedder()
        await search(
            None,
            workspace_id=WORKSPACE,
            query=QUERY,
            mode="semantic",
            embedder=embedder,
            settings=settings,
            match_count=8,
        )
        assert len(embedder.calls) == 1
        assert repo.calls[0]["keyword_weight"] == 0.0

    async def test_semantic_mode_fails_loudly_when_the_provider_is_down(self, repo, settings):
        """No silent degradation: the caller asked for semantic, not 'best effort'."""
        with pytest.raises(ProviderUnavailable):
            await search(
                None,
                workspace_id=WORKSPACE,
                query=QUERY,
                mode="semantic",
                embedder=_Embedder(fail=True),
                settings=settings,
                match_count=8,
            )
        assert repo.calls == []

    async def test_hybrid_degrades_to_keyword_when_the_provider_is_down(self, repo, settings):
        await search(
            None,
            workspace_id=WORKSPACE,
            query=QUERY,
            mode="hybrid",
            embedder=_Embedder(fail=True),
            settings=settings,
            match_count=8,
        )
        # one call, made with no embedding and therefore full keyword weight
        assert len(repo.calls) == 1
        assert repo.calls[0]["embedding"] is None

    async def test_hybrid_uses_both_arms(self, repo, settings):
        await search(
            None,
            workspace_id=WORKSPACE,
            query=QUERY,
            mode="hybrid",
            embedder=_Embedder(),
            settings=settings,
            match_count=8,
        )
        call = repo.calls[0]
        assert call["embedding"] is not None
        assert call["keyword_weight"] == settings.keyword_weight
        assert call["vector_weight"] == settings.vector_weight
        assert call["candidate_count"] == settings.candidate_k
        assert call["min_similarity"] == settings.min_similarity

    async def test_workspace_is_always_forwarded(self, repo, settings):
        """The tenant filter is passed to SQL on every mode; it is not optional."""
        for mode in ("hybrid", "semantic", "keyword"):
            await search(
                None,
                workspace_id=WORKSPACE,
                query=QUERY,
                mode=mode,
                embedder=_Embedder(),
                settings=settings,
                match_count=8,
            )
        assert all(call["workspace_id"] == WORKSPACE for call in repo.calls)


class TestHitProvenance:
    def test_row_maps_the_source_id_from_the_right_column(self, repo):
        from app.ai.retrieval import row_to_hit

        note_row = _Row(source_kind="note", note_id="note-9", document_id=None)
        assert row_to_hit(note_row).source_id == "note-9"

    def test_matched_arm_reports_both_when_both_ranked(self, repo):
        from app.ai.retrieval import row_to_hit

        assert row_to_hit(_Row(vector_rank=2, keyword_rank=3)).matched_arm == "both"

    def test_score_defaults_when_null(self, repo):
        from app.ai.retrieval import row_to_hit

        assert row_to_hit(_Row(fused_score=None)).fused_score == 0.0  # type: ignore[arg-type]

    def test_hit_is_hashable_by_value_for_dedupe(self, repo):
        a = Hit("c", "document", "d", "T", "x", None, None, None, None, 1, None, 0.1)
        b = Hit("c", "document", "d", "T", "x", None, None, None, None, 1, None, 0.1)
        assert a == b

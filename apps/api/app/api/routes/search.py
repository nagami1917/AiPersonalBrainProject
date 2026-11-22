from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.ai.providers import Embedder
from app.ai.retrieval import normalize_fused_scores
from app.core import tenancy
from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser, UserConn, WorkspaceCtx, get_embedder
from app.schemas.chat import SearchFilters, SearchRequest, SearchResponse, SearchResult
from app.services.retrieval_service import retrieve

router = APIRouter(prefix="/workspaces/{workspace_id}/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    ctx: WorkspaceCtx,
    conn: UserConn,
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
) -> SearchResponse:
    result = await retrieve(
        conn,
        workspace_id=ctx.id,
        query=payload.query,
        embedder=embedder,
        settings=settings,
        filters=payload.filters,
        match_count=payload.limit,
        mode=payload.mode,
    )

    # Display score is normalized against the best hit; `matched_arm` exposes
    # WHY a result surfaced, which is the difference between a search that can
    # be debugged and one that cannot.
    display_scores = normalize_fused_scores(result.hits)
    results = [
        SearchResult(
            source_kind=hit.source_kind,
            source_id=hit.source_id,
            source_title=hit.source_title,
            chunk_id=hit.chunk_id,
            snippet=hit.content[:600],
            heading_path=hit.heading_path,
            page=hit.page_number,
            score=display_scores[index],
            matched_arm=hit.matched_arm,
        )
        for index, hit in enumerate(result.hits)
    ]
    return SearchResponse(
        results=results,
        total_candidates={
            "vector": result.vector_candidates,
            "keyword": result.keyword_candidates,
        },
    )


# ---------------------------------------------------------------------------
# Flat, GET-shaped search for clients that treat the workspace as a query
# parameter rather than a path segment (the API spec lists `GET /search`).
#
# The POST form stays canonical: filters such as `source_ids` are lists, and a
# query string is a poor place for them. This one covers the common case — a
# search box — and defaults the workspace to the caller's own.
# ---------------------------------------------------------------------------

flat_router = APIRouter(prefix="/search", tags=["search"])


@flat_router.get("", response_model=SearchResponse)
async def flat_search(
    user: CurrentUser,
    conn: UserConn,
    q: str = Query(min_length=1, max_length=2000, description="Query text"),
    workspace_id: str | None = Query(default=None),
    mode: str = Query(default="hybrid", pattern="^(hybrid|semantic|keyword)$"),
    limit: int = Query(default=20, ge=1, le=50),
    collection_id: str | None = None,
    kind: str | None = Query(default=None, pattern="^(note|document)$"),
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
) -> SearchResponse:
    ctx = (
        await tenancy.resolve_workspace(conn, user_id=user.id, workspace_id=workspace_id)
        if workspace_id
        else await tenancy.default_workspace(conn, user_id=user.id)
    )

    result = await retrieve(
        conn,
        workspace_id=ctx.id,
        query=q,
        embedder=embedder,
        settings=settings,
        filters=SearchFilters(collection_id=collection_id, kind=kind),  # type: ignore[arg-type]
        match_count=limit,
        mode=mode,
    )
    display_scores = normalize_fused_scores(result.hits)
    return SearchResponse(
        results=[
            SearchResult(
                source_kind=hit.source_kind,
                source_id=hit.source_id,
                source_title=hit.source_title,
                chunk_id=hit.chunk_id,
                snippet=hit.content[:600],
                heading_path=hit.heading_path,
                page=hit.page_number,
                score=display_scores[index],
                matched_arm=hit.matched_arm,
            )
            for index, hit in enumerate(result.hits)
        ],
        total_candidates={
            "vector": result.vector_candidates,
            "keyword": result.keyword_candidates,
        },
    )

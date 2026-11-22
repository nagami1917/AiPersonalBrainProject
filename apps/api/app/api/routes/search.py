from __future__ import annotations

from fastapi import APIRouter, Depends

from app.ai.providers import Embedder
from app.ai.retrieval import normalize_fused_scores
from app.core.config import Settings, get_settings
from app.core.deps import UserConn, WorkspaceCtx, get_embedder
from app.schemas.chat import SearchRequest, SearchResponse, SearchResult
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

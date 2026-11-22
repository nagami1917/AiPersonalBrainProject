from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends

from app.ai.providers import Embedder
from app.ai.retrieval import normalize_fused_scores
from app.core.config import Settings, get_settings
from app.core.deps import current_user, get_embedder, user_db
from app.core.security import AuthUser
from app.schemas.chat import SearchRequest, SearchResponse, SearchResult
from app.services.retrieval_service import retrieve

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
) -> SearchResponse:
    result = await retrieve(
        conn,
        user_id=user.id,
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
            document_id=hit.document_id,
            document_title=hit.document_title,
            chunk_id=hit.chunk_id,
            snippet=hit.content[:600],
            heading_path=hit.heading_path,
            page=hit.page_start,
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

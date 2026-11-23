"""`/workspaces/{id}/embeddings` — corpus inspection and model-drift reprocessing.

The service layer (`services/reindex_service.py`) owns the queries and the
reasoning; this module is the authorisation and the shape.

## Why the reindex is two-step

`GET` reports and `POST` acts, and the `POST` refuses without `confirm=true`.
A model swap that leaves the corpus mixed degrades every answer silently, so an
operator *should* be told how many sources are affected before spending the
money — and a client that fires the mutating call on page load would otherwise
trigger a full-corpus reembed by accident. This mirrors how the rest of the API
treats expensive, hard-to-undo operations (deletion goes to trash first).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core import guard
from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser, UserConn, WorkspaceCtx
from app.core.errors import NexusError
from app.services import reindex_service

router = APIRouter(prefix="/workspaces/{workspace_id}/embeddings", tags=["embeddings"])


class ReindexRequest(BaseModel):
    """`confirm` is required rather than defaulted.

    Defaulting it to False would be fine for the dry run, but a client that
    forgets to send it would get a 200 and assume a reindex had started. Making
    the field required forces the caller to state an intention.
    """

    confirm: bool
    # Cap the run so a large corpus can be migrated in batches a worker can
    # actually finish inside its staleness window.
    limit: int | None = Query(default=None, ge=1, le=5000)


class ModelStatOut(BaseModel):
    model: str
    dim: int | None
    chunks: int
    is_current: bool


class ReindexOut(BaseModel):
    configured_model: str
    configured_dim: int
    models: list[ModelStatOut]
    stale_chunks: int
    stale_sources: int
    needs_reindex: bool
    queued_jobs: int
    job_ids: list[str]


def _out(plan: reindex_service.ReindexPlan) -> ReindexOut:
    return ReindexOut(
        configured_model=plan.configured_model,
        configured_dim=plan.configured_dim,
        models=[
            ModelStatOut(model=m.model, dim=m.dim, chunks=m.chunks, is_current=m.is_current)
            for m in plan.models
        ],
        stale_chunks=plan.stale_chunks,
        stale_sources=plan.stale_sources,
        needs_reindex=plan.needs_reindex,
        queued_jobs=plan.queued_jobs,
        job_ids=plan.job_ids,
    )


@router.get("", response_model=ReindexOut)
async def embedding_status(
    ctx: WorkspaceCtx,
    conn: UserConn,
    settings: Settings = Depends(get_settings),
) -> ReindexOut:
    """What the corpus is indexed with, and whether that is still current.

    Read-only and cheap, so the settings page can show it on load. A workspace
    with no chunks returns an empty `models` list and `needs_reindex: false` —
    nothing to reprocess is not drift.
    """
    plan = await reindex_service.report(conn, workspace_id=ctx.id, settings=settings)
    return _out(plan)


@router.post("/reindex", response_model=ReindexOut)
async def reindex(
    payload: ReindexRequest,
    ctx: WorkspaceCtx,
    user: CurrentUser,
    conn: UserConn,
    settings: Settings = Depends(get_settings),
) -> ReindexOut:
    """Queue a reindex of every source whose vectors came from another model.

    Reuses the worker's existing `reindex` job type. Idempotent per source: a
    source already queued is skipped, so this can be retried safely.
    """
    if not payload.confirm:
        raise NexusError(
            "Reindex changes stored vectors for this workspace. Send "
            '{"confirm": true} to proceed, or GET this endpoint to see the scope first.',
            code="confirmation_required",
            status_code=400,
        )

    # Reindexing N sources is N embedding passes. Counted as ingest cost, and
    # counted before the work so a rate-limited caller cannot queue thousands of
    # jobs and discover the limit afterwards.
    await guard.ai_call(user.id, ctx.id, cost="ingest")

    plan = await reindex_service.enqueue(
        conn, workspace_id=ctx.id, settings=settings, limit=payload.limit
    )
    return _out(plan)


@router.get("/health", response_model=dict[str, Any])
async def embedding_health(
    ctx: WorkspaceCtx,
    conn: UserConn,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """A one-line answer for the settings page badge: is retrieval trustworthy?

    `mixed` is the state that matters and the one nothing reported before: some
    chunks from the current model, some from another. Retrieval still returns
    results in that state, which is exactly why it is dangerous — the results are
    silently worse rather than visibly broken.
    """
    plan = await reindex_service.report(conn, workspace_id=ctx.id, settings=settings)
    current = sum(m.chunks for m in plan.models if m.is_current)
    stale = sum(m.chunks for m in plan.models if not m.is_current)

    if not plan.models:
        state = "empty"
    elif stale == 0:
        state = "current"
    elif current == 0:
        state = "stale"
    else:
        state = "mixed"

    return {
        "state": state,
        "configured_model": plan.configured_model,
        "current_chunks": current,
        "stale_chunks": stale,
        "needs_reindex": plan.needs_reindex,
    }

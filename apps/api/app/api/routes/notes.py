from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core import audit, tenancy
from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser, UserConn, WorkspaceCtx
from app.core.errors import Forbidden, NotFound
from app.repositories import knowledge as repo
from app.schemas.documents import (
    NoteBacklink,
    NoteCreate,
    NoteDetail,
    NoteOutgoingLink,
    NoteSummary,
    NoteUpdate,
    StatusResponse,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/notes", tags=["notes"])

_STAGE_PROGRESS = {
    None: 0,
    "extracting": 20,
    "normalizing": 30,
    "chunking": 45,
    "embedding": 70,
    "entities": 90,
}


async def _new_link_titles(conn: asyncpg.Connection, note_id: str, titles: list[str]) -> list[str]:
    """Which of `titles` are newly linked from this note, case-insensitively.

    `replace_note_links` deletes every outgoing link and re-inserts the set on
    each save, because the body is the source of truth. So "a link was created"
    can only mean the *delta* — recording the full set every time would emit an
    identical event on every autosave and drown the trail in events that
    describe no change at all.

    Returns an empty list when nothing is new, which is the common case.
    """
    existing = {
        row["target_title"].lower()
        for row in await conn.fetch(
            "select target_title from public.note_links where source_note_id = $1", note_id
        )
    }
    return [t for t in titles if t.lower() not in existing]


def _summary(record: asyncpg.Record, tags: list[str] | None = None) -> NoteSummary:
    data = dict(record)
    return NoteSummary(
        id=str(data["id"]),
        title=data["title"],
        status=data["status"],
        processing_stage=data.get("processing_stage"),
        collection_id=str(data["collection_id"]) if data.get("collection_id") else None,
        summary=data.get("summary"),
        tags=tags or [],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


@router.get("", response_model=dict)
async def list_notes(
    ctx: WorkspaceCtx,
    conn: UserConn,
    status: str | None = None,
    collection_id: str | None = None,
    tag: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = None,
    q: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    """List notes. `q` is a substring filter over title and body.

    This is the search box on the notes list, not the semantic search endpoint:
    it filters the caller's own notes without embedding a query, so typing stays
    offline and instant. `POST /search` remains the way to search by meaning.
    """
    records = await repo.list_notes(
        conn,
        ctx.id,
        status=status,
        collection_id=collection_id,
        query=q,
        tag=tag,
        limit=limit,
        cursor=cursor,
    )
    items = [_summary(r).model_dump(mode="json") for r in records]
    next_cursor = records[-1]["updated_at"].isoformat() if len(records) == limit else None
    return {"items": items, "next_cursor": next_cursor}


@router.post("", response_model=dict, status_code=202)
async def create_note(
    payload: NoteCreate,
    ctx: WorkspaceCtx,
    conn: UserConn,
    user: CurrentUser,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    tenancy.require_write(ctx)

    note_id = str(uuid.uuid4())
    record = await repo.insert_note(
        conn,
        {
            "id": note_id,
            "workspace_id": ctx.id,
            "collection_id": payload.collection_id,
            "created_by": user.id,
            "title": payload.title,
            "content": payload.content,
            "content_format": payload.content_format,
            "status": "pending",
        },
    )
    if payload.tags:
        await repo.replace_tags(conn, ctx.id, payload.tags, note_id=note_id)

    # Links are recomputed on every write, from the body, in one place. A note
    # created now may satisfy a [[link]] someone wrote yesterday, so resolve
    # outstanding references to the new title before storing this note's own.
    await repo.reindex_backlinks_to_title(conn, ctx.id, payload.title, note_id)
    links = repo.extract_wiki_links(payload.content)
    await repo.replace_note_links(conn, ctx.id, note_id, links)

    await audit.record(
        conn,
        workspace_id=ctx.id,
        action="note.create",
        target_type="note",
        target_id=note_id,
        target_label=payload.title,
        detail={
            "title": payload.title,
            "link_count": len(links),
            "collection_id": payload.collection_id,
        },
    )

    job_id = await conn.fetchval(
        """select public.enqueue_job(
             p_workspace_id => $1, p_job_type => 'ingest_note', p_note_id => $2)""",
        ctx.id,
        note_id,
    )
    return {
        "note": _summary(record, payload.tags).model_dump(mode="json"),
        "job_id": str(job_id),
    }


# Declared BEFORE `/{note_id}` on purpose: FastAPI matches routes in
# declaration order, so a `GET /notes/trash` that came later would be captured
# by the detail route and fail with a 422 on the note-id path parameter.
@router.get("/trash", response_model=dict)
async def list_trash(
    ctx: WorkspaceCtx,
    conn: UserConn,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Everything in the trash, with how long it has left.

    Registered before `/{note_id}` would otherwise match "trash" as a note id —
    FastAPI matches in declaration order, and this route is declared above the
    detail route for that reason.

    `expires_at` is computed from `deleted_at` rather than stored, so changing
    `trash_retention_days` applies to items already in the trash instead of only
    to new deletions.
    """
    notes = await repo.list_deleted_notes(conn, ctx.id)
    counts = await repo.purge_due_count(conn, ctx.id, settings.trash_retention_days)
    items = []
    for row in notes:
        data = dict(row)
        deleted_at = data["deleted_at"]
        items.append(
            {
                "id": str(data["id"]),
                "kind": "note",
                "title": data["title"],
                "snippet": (data["content"] or "")[:280],
                "deleted_at": deleted_at,
                "expires_at": deleted_at + timedelta(days=settings.trash_retention_days),
            }
        )
    return {
        "items": items,
        "retention_days": settings.trash_retention_days,
        "counts": counts,
    }


@router.get("/{note_id}", response_model=NoteDetail)
async def get_note(note_id: str, ctx: WorkspaceCtx, conn: UserConn) -> NoteDetail:
    record = await repo.get_note(conn, note_id, ctx.id)
    if record is None:
        raise NotFound("Note not found")

    data = dict(record)
    tags = await repo.tag_names(conn, workspace_id=ctx.id, note_id=note_id)
    backlinks, outgoing = await _links(conn, ctx.id, note_id)
    return NoteDetail(
        **_summary(record, tags).model_dump(),
        content=data.get("content"),
        content_format=data.get("content_format", "markdown"),
        error_message=data.get("error_message"),
        version=data.get("version", 1),
        embedding_model=data.get("embedding_model"),
        chunk_count=await repo.chunk_count(conn, ctx.id, note_id=note_id),
        backlinks=backlinks,
        outgoing_links=outgoing,
    )


async def _links(
    conn: Any, workspace_id: str, note_id: str
) -> tuple[list[NoteBacklink], list[NoteOutgoingLink]]:
    """Both directions of a note's graph edges, shaped for the panel."""
    backlinks = [
        NoteBacklink(
            id=str(r["id"]),
            title=r["title"],
            status=r["status"],
            updated_at=r["updated_at"],
        )
        for r in await repo.backlinks_for(conn, workspace_id, note_id)
    ]
    outgoing = [
        NoteOutgoingLink(
            # The stored spelling is the author's own; a target that has since
            # been created shows its current title instead.
            target_title=r["resolved_title"] or r["target_title"],
            # A resolved link is navigable; a dangling one is a promise, and the
            # UI renders it as plain text rather than a dead end.
            target_id=str(r["target_note_id"]) if r["target_note_id"] else None,
            resolved=r["target_note_id"] is not None,
            status=r["resolved_status"],
        )
        for r in await repo.outgoing_links_for(conn, workspace_id, note_id)
    ]
    return backlinks, outgoing


@router.patch("/{note_id}", response_model=NoteSummary)
async def update_note(
    note_id: str, payload: NoteUpdate, ctx: WorkspaceCtx, conn: UserConn
) -> NoteSummary:
    tenancy.require_write(ctx)
    existing = await repo.get_note(conn, note_id, ctx.id)
    if existing is None:
        raise NotFound("Note not found")

    patch: dict[str, Any] = {}
    if payload.title is not None:
        patch["title"] = payload.title
    if "collection_id" in payload.model_fields_set:
        patch["collection_id"] = payload.collection_id
    if payload.content is not None:
        patch["content"] = payload.content
        await repo.bump_note_version(conn, note_id, ctx.id)
    if payload.content_format is not None:
        patch["content_format"] = payload.content_format

    record = await repo.update_note(conn, note_id, ctx.id, patch) or existing

    if payload.tags is not None:
        await repo.replace_tags(conn, ctx.id, payload.tags, note_id=note_id)

    # A title change can satisfy links written elsewhere; a content change can
    # add or remove this note's own. Only re-resolve when one of them moved.
    if payload.title is not None:
        await repo.reindex_backlinks_to_title(conn, ctx.id, payload.title, note_id)
    new_links: list[str] = []
    if payload.content is not None:
        titles = repo.extract_wiki_links(payload.content)
        new_links = await _new_link_titles(conn, note_id, titles)
        await repo.replace_note_links(conn, ctx.id, note_id, titles)

    # A content edit changes the searchable representation, so re-index.
    if payload.content is not None:
        await conn.fetchval(
            """select public.enqueue_job(
                 p_workspace_id => $1, p_job_type => 'ingest_note', p_note_id => $2)""",
            ctx.id,
            note_id,
        )

    # `patch` rather than the payload: it holds only the fields the caller
    # actually sent, so the trail records what changed instead of a wall of
    # nulls. Titles go in as before/after because that pair is what answers
    # "who renamed this?".
    await audit.record(
        conn,
        workspace_id=ctx.id,
        action="note.update",
        target_type="note",
        target_id=note_id,
        target_label=patch.get("title") or existing["title"],
        detail={
            "fields_changed": ",".join(sorted(patch)),
            "title_before": existing["title"],
            "title_after": patch.get("title", existing["title"]),
        },
    )

    if new_links:
        await audit.record(
            conn,
            workspace_id=ctx.id,
            action="note.link.create",
            target_type="note",
            target_id=note_id,
            target_label=patch.get("title") or existing["title"],
            detail={"link_count": len(new_links), "title": ",".join(new_links)},
        )

    return _summary(record, await repo.tag_names(conn, workspace_id=ctx.id, note_id=note_id))


@router.delete("/{note_id}", status_code=204, response_class=Response, response_model=None)
async def delete_note(
    note_id: str,
    ctx: WorkspaceCtx,
    conn: UserConn,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Move a note to the trash, and schedule its purge.

    Deleting is a two-phase operation. The row is marked `deleted_at` and
    disappears from every user-facing view immediately, but it is only
    hard-deleted once `trash_retention_days` has passed. That window is what
    makes `POST /{note_id}/restore` meaningful.

    The purge is scheduled with an explicit `run_after` rather than the
    helper's default of `now()`. The original code let the worker delete the
    row seconds later, which made recovery impossible in practice even though
    the schema had a `deleted_at` column.
    """
    tenancy.require_write(ctx)
    # Read the title first: after the soft delete the row is still readable, but
    # the audit label has to survive the eventual hard purge, which it does
    # because `target_label` is a copy rather than a reference.
    existing = await repo.get_note(conn, note_id, ctx.id)
    if existing is None or not await repo.soft_delete_note(conn, note_id, ctx.id):
        raise NotFound("Note not found")

    await audit.record(
        conn,
        workspace_id=ctx.id,
        action="note.delete",
        target_type="note",
        target_id=note_id,
        target_label=existing["title"],
        detail={"title": existing["title"], "reason": "moved to trash"},
    )

    retention_days = settings.trash_retention_days
    if retention_days > 0:
        await conn.fetchval(
            """select public.enqueue_job(
                 p_workspace_id => $1, p_job_type => 'purge', p_note_id => $2,
                 p_run_after   => now() + make_interval(days => $3))""",
            ctx.id,
            note_id,
            retention_days,
        )
    else:
        # Retention disabled: purge now, and say so rather than leaving the row
        # in an in-between state forever.
        await conn.fetchval(
            """select public.enqueue_job(
                 p_workspace_id => $1, p_job_type => 'purge', p_note_id => $2)""",
            ctx.id,
            note_id,
        )
    return Response(status_code=204)


@router.post("/{note_id}/restore", response_model=NoteSummary)
async def restore_note(note_id: str, ctx: WorkspaceCtx, conn: UserConn) -> NoteSummary:
    """Undo a deletion.

    Restoring re-indexes: the chunks were removed when the note was trashed, so
    a restored note that is not re-ingested would be visible but unsearchable —
    the most confusing possible outcome. It comes back as 'pending' and the
    normal pipeline makes it citable again.
    """
    tenancy.require_write(ctx)
    if not await repo.restore_note(conn, note_id, ctx.id):
        raise NotFound("No deleted note with that id")

    await conn.fetchval(
        """select public.enqueue_job(
             p_workspace_id => $1, p_job_type => 'ingest_note', p_note_id => $2)""",
        ctx.id,
        note_id,
    )
    record = await repo.get_note(conn, note_id, ctx.id)
    assert record is not None
    return _summary(record, await repo.tag_names(conn, workspace_id=ctx.id, note_id=note_id))


@router.get("/{note_id}/status", response_model=StatusResponse)
async def note_status(note_id: str, ctx: WorkspaceCtx, conn: UserConn) -> StatusResponse:
    record = await repo.get_note(conn, note_id, ctx.id)
    if record is None:
        raise NotFound("Note not found")
    data = dict(record)
    status = data["status"]
    stage = data.get("processing_stage")
    return StatusResponse(
        id=str(data["id"]),
        kind="note",
        status=status,
        processing_stage=stage,
        progress_pct=100
        if status == "ready"
        else _STAGE_PROGRESS.get(stage, 5 if status == "pending" else 10),
        error_message=data.get("error_message"),
    )


@router.post("/{note_id}/reprocess", status_code=202)
async def reprocess_note(note_id: str, ctx: WorkspaceCtx, conn: UserConn) -> dict[str, Any]:
    tenancy.require_write(ctx)
    if await repo.get_note(conn, note_id, ctx.id) is None:
        raise NotFound("Note not found")
    job_id = await conn.fetchval(
        """select public.enqueue_job(
             p_workspace_id => $1, p_job_type => 'reindex', p_note_id => $2, p_priority => 50)""",
        ctx.id,
        note_id,
    )
    return {"job_id": str(job_id), "status": "queued"}


def _forbidden() -> None:
    raise Forbidden("read-only", code="insufficient_role")

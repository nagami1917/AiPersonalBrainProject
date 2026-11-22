"""Workspace-level counts for the Welcome page.

One endpoint, one query, because the stat cards are read together and a
per-card request would let the numbers disagree with themselves on screen.

Every count is scoped to the workspace and, for notes and documents, to
non-deleted rows: an archived note that still counts in the header but cannot
be opened reads as a bug.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.deps import UserConn, WorkspaceCtx

router = APIRouter(prefix="/workspaces/{workspace_id}/stats", tags=["stats"])


@router.get("", response_model=dict)
async def workspace_stats(ctx: WorkspaceCtx, conn: UserConn) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        select
          (select count(*) from public.notes
            where workspace_id=$1 and deleted_at is null)                       as notes,
          (select count(*) from public.notes
            where workspace_id=$1 and deleted_at is null and status='ready')    as notes_ready,
          (select count(*) from public.documents
            where workspace_id=$1 and deleted_at is null)                       as documents,
          (select count(*) from public.documents
            where workspace_id=$1 and deleted_at is null
              and status='ready')                                              as documents_ready,
          (select count(*) from public.documents
            where workspace_id=$1 and deleted_at is null
              and status in ('pending','processing'))                    as documents_processing,
          (select count(*) from public.documents
            where workspace_id=$1 and deleted_at is null
              and status='failed')                                             as documents_failed,
          (select count(*) from public.collections
            where workspace_id=$1)                                             as collections,
          (select count(*) from public.tags t
            where t.workspace_id=$1)                                            as tags,
          (select count(*) from public.knowledge_entities
            where workspace_id=$1)                                             as entities,
          (select count(*) from public.knowledge_links
            where workspace_id=$1)                                             as links,
          (select count(*) from public.document_chunks
            where workspace_id=$1)                                             as chunks,
          (select count(*) from public.conversations
            where workspace_id=$1 and archived_at is null)                      as conversations,
          (select count(*) from public.note_links
            where workspace_id=$1)                                             as note_links
        """,
        ctx.id,
    )
    assert row is not None  # a scalar-row select always returns exactly one row
    data = dict(row)
    return {
        "workspace_id": ctx.id,
        "notes": data["notes"],
        "notes_ready": data["notes_ready"],
        "documents": data["documents"],
        "documents_ready": data["documents_ready"],
        "documents_processing": data["documents_processing"],
        "documents_failed": data["documents_failed"],
        "collections": data["collections"],
        "tags": data["tags"],
        "entities": data["entities"],
        "links": data["links"],
        "chunks": data["chunks"],
        "conversations": data["conversations"],
        "note_links": data["note_links"],
    }

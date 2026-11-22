"""Flat aliases for the spec's `/notes`, `/documents`, … paths.

The API spec lists resources without a workspace prefix (`GET /notes`), while the
implementation nests them (`/workspaces/{id}/notes`) because a workspace in the
path is how authorisation, RLS identity, and every other resource route work.

Rather than fork the implementation, each alias resolves a workspace — from an
optional `workspace_id` query parameter, defaulting to the caller's own — and then
calls the *same* scoped view function. Consequences worth stating:

  * there is one implementation of each resource, so a tenancy fix cannot be made
    in one copy and forgotten in the other;
  * the defaulted workspace is the user's `profiles.default_workspace_id`, not
    "the first workspace we find";
  * an explicit `workspace_id` is authorised identically to the path form, so a
    guessed id from another tenant is a 404, not a silent fallback.

Aliases exist only where the meaning is unambiguous. Document upload, for example,
has no alias: it is a two-step signed-URL flow whose response depends on the
workspace path, and guessing a default for a write that creates storage objects is
not a convenience worth having.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.api.routes.collections import CollectionCreate
from app.core import tenancy
from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser, UserConn
from app.schemas.documents import NoteCreate

flat = APIRouter(tags=["aliases"])


async def _ctx(
    conn: asyncpg.Connection, user_id: str, workspace_id: str | None
) -> tenancy.WorkspaceContext:
    if workspace_id:
        return await tenancy.resolve_workspace(conn, user_id=user_id, workspace_id=workspace_id)
    return await tenancy.default_workspace(conn, user_id=user_id)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
@flat.get("/notes", response_model=dict, tags=["notes"])
async def list_notes(
    user: CurrentUser,
    conn: UserConn,
    workspace_id: str | None = Query(default=None),
    status: str | None = None,
    collection_id: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = None,
) -> dict[str, Any]:
    from app.api.routes.notes import list_notes as scoped

    ctx = await _ctx(conn, user.id, workspace_id)
    return await scoped(
        ctx=ctx,
        conn=conn,
        status=status,
        collection_id=collection_id,
        limit=limit,
        cursor=cursor,
    )


@flat.post("/notes", status_code=201, response_model=dict, tags=["notes"])
async def create_note(
    payload: NoteCreate,
    user: CurrentUser,
    conn: UserConn,
    workspace_id: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> Any:
    from app.api.routes.notes import create_note as scoped

    ctx = await _ctx(conn, user.id, workspace_id)
    return await scoped(payload=payload, ctx=ctx, conn=conn, user=user, settings=settings)


# ---------------------------------------------------------------------------
# Documents (read-only aliases; upload stays scoped)
# ---------------------------------------------------------------------------
@flat.get("/documents", response_model=dict, tags=["documents"])
async def list_documents(
    user: CurrentUser,
    conn: UserConn,
    workspace_id: str | None = Query(default=None),
    status: str | None = None,
    collection_id: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = None,
) -> dict[str, Any]:
    from app.api.routes.documents import list_documents as scoped

    ctx = await _ctx(conn, user.id, workspace_id)
    return await scoped(
        ctx=ctx,
        conn=conn,
        status=status,
        collection_id=collection_id,
        limit=limit,
        cursor=cursor,
    )


@flat.get("/documents/{document_id}", response_model=dict, tags=["documents"])
async def get_document(
    document_id: str,
    user: CurrentUser,
    conn: UserConn,
    workspace_id: str | None = Query(default=None),
) -> Any:
    from app.api.routes.documents import get_document as scoped

    ctx = await _ctx(conn, user.id, workspace_id)
    return await scoped(document_id=document_id, ctx=ctx, conn=conn)


@flat.delete("/documents/{document_id}", status_code=204, response_model=None, tags=["documents"])
async def delete_document(
    document_id: str,
    user: CurrentUser,
    conn: UserConn,
    workspace_id: str | None = Query(default=None),
) -> None:
    from app.api.routes.documents import delete_document as scoped

    ctx = await _ctx(conn, user.id, workspace_id)
    await scoped(document_id=document_id, ctx=ctx, conn=conn)


@flat.post("/documents/{document_id}/reprocess", response_model=dict, tags=["documents"])
async def reprocess_document(
    document_id: str,
    user: CurrentUser,
    conn: UserConn,
    workspace_id: str | None = Query(default=None),
) -> dict[str, Any]:
    from app.api.routes.documents import reprocess as scoped

    ctx = await _ctx(conn, user.id, workspace_id)
    return await scoped(document_id=document_id, ctx=ctx, conn=conn)


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------
@flat.get("/collections", response_model=list, tags=["collections"])
async def list_collections(
    user: CurrentUser,
    conn: UserConn,
    workspace_id: str | None = Query(default=None),
) -> Any:
    from app.api.routes.collections import list_collections as scoped

    ctx = await _ctx(conn, user.id, workspace_id)
    return await scoped(ctx=ctx, conn=conn)


@flat.post("/collections", status_code=201, response_model=dict, tags=["collections"])
async def create_collection(
    payload: CollectionCreate,
    user: CurrentUser,
    conn: UserConn,
    workspace_id: str | None = Query(default=None),
) -> Any:
    from app.api.routes.collections import create_collection as scoped

    ctx = await _ctx(conn, user.id, workspace_id)
    return await scoped(payload=payload, ctx=ctx, conn=conn)


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
@flat.get("/conversations", response_model=list, tags=["conversations"])
async def list_conversations(
    user: CurrentUser,
    conn: UserConn,
    workspace_id: str | None = Query(default=None),
) -> Any:
    from app.api.routes.chat import list_conversations as scoped

    ctx = await _ctx(conn, user.id, workspace_id)
    return await scoped(ctx=ctx, conn=conn)


@flat.get("/conversations/{conversation_id}", response_model=dict, tags=["conversations"])
async def get_conversation(
    conversation_id: str,
    user: CurrentUser,
    conn: UserConn,
    workspace_id: str | None = Query(default=None),
) -> dict[str, Any]:
    from app.api.routes.chat import get_conversation as scoped

    ctx = await _ctx(conn, user.id, workspace_id)
    return await scoped(conversation_id=conversation_id, ctx=ctx, conn=conn)

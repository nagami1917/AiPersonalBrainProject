"""Request dependencies: auth, database, workspace context, and providers.

Workspace context is resolved here, once, so no route has to decide which
workspace it is operating on. Every tenant-scoped query then takes an explicit
`workspace_id` — the Python-side half of the two-mechanism isolation (the other
half is RLS).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends, Path, Request
from starlette.concurrency import run_in_threadpool

from app.ai.providers import LLM, Embedder
from app.core import tenancy
from app.core.config import Settings, get_settings
from app.core.db import get_pool
from app.core.errors import Unauthorized
from app.core.security import AuthUser, decode_token, user_connection


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("Missing bearer token", code="unauthorized")
    return token.strip()


async def current_user(request: Request, settings: Settings = Depends(get_settings)) -> AuthUser:
    user = await run_in_threadpool(decode_token, _bearer(request), settings)
    request.state.user_id = user.id
    return user


async def db(request: Request, settings: Settings = Depends(get_settings)) -> asyncpg.Pool:
    return await get_pool(settings)


async def user_db(
    user: AuthUser = Depends(current_user),
    pool: asyncpg.Pool = Depends(db),
) -> AsyncIterator[asyncpg.Connection]:
    """A connection bound to the caller, so RLS applies to all statements.

    Yields inside an open transaction because `set_config(..., is_local=true)`
    is transaction-scoped.
    """
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await user_connection(conn, user)
            yield conn
            await tx.commit()
        except Exception:
            await tx.rollback()
            raise


def admin_pool_dep(pool: asyncpg.Pool = Depends(db)) -> asyncpg.Pool:
    """Service-role access. Restricted by convention to enqueueing and workers."""
    return pool


# ---------------------------------------------------------------------------
# Workspace context
#
# Two forms, because they answer different questions:
#
#   workspace_path   /api/v1/workspaces/{workspace_id}/... → explicit, used by all
#                    resource routes. Must be a member or it is a 404.
#   active_workspace the caller's default workspace, used by routes that are
#                    inherently personal (e.g. "which workspace am I in?").
# ---------------------------------------------------------------------------


async def workspace_path(
    workspace_id: Annotated[str, Path(description="Workspace id")],
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> tenancy.WorkspaceContext:
    """Resolve and authorise the workspace named in the URL.

    Non-membership returns 404, never 403: the API does not confirm that someone
    else's workspace exists.
    """
    return await tenancy.resolve_workspace(conn, user_id=user.id, workspace_id=workspace_id)


CurrentUser = Annotated[AuthUser, Depends(current_user)]
WorkspaceCtx = Annotated[tenancy.WorkspaceContext, Depends(workspace_path)]
UserConn = Annotated[asyncpg.Connection, Depends(user_db)]


def get_embedder(settings: Settings = Depends(get_settings)) -> Embedder:
    """Provider accessors are dependencies so tests can override them without
    patching httpx (and without any network access)."""
    from app.ai.providers import build_embedder

    return build_embedder(settings)


def get_llm(settings: Settings = Depends(get_settings)) -> LLM:
    from app.ai.providers import build_llm

    return build_llm(settings)

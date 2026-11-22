"""Request dependencies: auth, database, and the two-client split."""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
from fastapi import Depends, Request

from app.ai.providers import LLM, Embedder
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
    user = decode_token(_bearer(request), settings)
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


def get_embedder(settings: Settings = Depends(get_settings)) -> Embedder:
    """Provider accessors are dependencies so tests can override them without
    patching httpx (and without any network access)."""
    from app.ai.providers import build_embedder

    return build_embedder(settings)


def get_llm(settings: Settings = Depends(get_settings)) -> LLM:
    from app.ai.providers import build_llm

    return build_llm(settings)

"""asyncpg pool management.

Connection state is reset between checkouts because RLS identity is set as a
session-local setting on the connection (`set_config(..., true)` scoped to a
transaction); a leaked identity between requests would be a tenancy bug.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from app.core.config import Settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def get_pool(settings: Settings) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=10, init=_init_connection, command_timeout=30
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def rows_to_dicts(records: list[Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in records]

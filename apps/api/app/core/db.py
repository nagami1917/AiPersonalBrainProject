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


# Per-statement ceiling, as a backstop against a query that wedges forever.
# Deliberately much larger than any statement in the ingest pipeline should
# take, and set with `server_settings` rather than asyncpg's `command_timeout`:
#
#   * `command_timeout` is asyncpg's own client-side cancel. Verified by
#     experiment: with `command_timeout=30` and a 31-second statement, asyncpg
#     raises `TimeoutError` and the surrounding transaction is aborted.
#   * The alternative here is Postgres's `statement_timeout`, which produces a
#     normal `QueryCanceledError` the pipeline already handles as a job failure.
#
# The value matters because the ingest worker writes a document's whole chunk
# + vector set in one transaction; a large PDF can legitimately exceed 30s on
# that single statement, and being cut off mid-write is worse than waiting.
# 120s is the ceiling for one statement — not for one job, which is bounded by
# `job_stale_after_seconds` and the sweeper instead.
STATEMENT_TIMEOUT_MS = 120_000


async def get_pool(settings: Settings) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=10,
            init=_init_connection,
            server_settings={"statement_timeout": str(STATEMENT_TIMEOUT_MS)},
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def rows_to_dicts(records: list[Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in records]

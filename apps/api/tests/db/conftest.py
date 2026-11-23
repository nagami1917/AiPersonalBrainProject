"""Fixtures shared by the database suite.

`conn` and `workspace_id` live here rather than being redefined per module. Each
module that rolled its own got the isolation subtly wrong at least once (see
`tests/db/support.py` for the two that committed instead of rolling back), and
the per-module pools meant a single stale pool could silently skip a whole file.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import asyncpg
import pytest
from app.core.config import get_settings

from tests.db.support import rolled_back

ALICE = "11111111-1111-1111-1111-111111111111"


async def _init(conn: asyncpg.Connection) -> None:
    """The app's jsonb codec; a bare pool does not install it."""
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@pytest.fixture
async def db_pool() -> AsyncIterator[asyncpg.Pool]:
    """A per-test pool.

    Not `get_pool()`: that memoises a pool bound to the event loop that created it,
    and pytest-asyncio gives every test a new loop — so a cached pool dies with the
    first test and every later one fails (or, worse, silently skips).
    """
    try:
        pool = await asyncpg.create_pool(
            get_settings().database_url, min_size=1, max_size=2, init=_init
        )
        await pool.fetchval("select 1")
    except Exception as exc:  # no database in this environment
        pytest.skip(f"database unavailable: {exc}")
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def conn(db_pool) -> AsyncIterator[asyncpg.Connection]:
    """A connection inside an always-rolled-back transaction."""
    async with db_pool.acquire() as connection, rolled_back(connection):
        yield connection


@pytest.fixture
async def workspace_id(conn) -> str:
    """Alice's personal workspace — the seeded corpus every eval case refers to."""
    found = await conn.fetchval(
        "select id from public.workspaces where owner_id=$1 and personal limit 1", ALICE
    )
    if found is None:
        pytest.skip("seed data missing: run `make db-reset` first")
    return str(found)

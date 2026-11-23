"""Shared fixtures for the database suite, including the isolation guarantee.

## The bug this module fixes

The `conn` fixtures in `test_ingest_pipeline.py` and `test_chat_grounding_e2e.py`
both read:

    async with pool.acquire() as connection, connection.transaction():
        ...
        yield connection

and the docstring claimed the transaction was "rolled back". It is not. An
asyncpg `Transaction` commits on a clean exit from its `async with` block — that
is what "exit without exception" means. So every test in those modules *committed*
its rows, and the suite left documents, chunks and conversations behind on every
run. Observed: 10 `ingest-probe` documents accumulated over two runs of one
module, each with chunks written into the real corpus.

That is not cosmetic. Those leftover rows are readable through `hybrid_search`,
so they changed the behaviour of later tests: `test_each_mode_returns_only_its_own_arm`
eventually failed because an unordered `limit 1` picked up a chunk from abandoned
test data — and had been passing only while the heap order happened to favour the
seeded row. A suite that mutates the database it asserts against cannot tell you
which of its own results to trust.

## The fix

`rolled_back(conn)` wraps the pattern that actually does what the docstrings
claimed: an explicit `start()` and a `rollback()` in `finally`. Using it is the
only way to open a transaction in this suite, so the claim and the behaviour
cannot drift apart again.

`purge_sources` is for the rows that must be written by a *separate* connection
(the worker tests, which exercise the real ingest path) and therefore cannot be
rolled back by the fixture's transaction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg


@asynccontextmanager
async def rolled_back(conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    """Run the body inside a transaction that is ALWAYS rolled back.

    Explicit `start()`/`rollback()` rather than `async with conn.transaction()`:
    the context manager commits on a clean exit, which is the opposite of what a
    test fixture wants and exactly the trap this module exists to close.
    """
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()


async def purge_sources(conn: asyncpg.Connection, *, title: str) -> int:
    """Hard-delete documents (and their chunks) by title path. Returns rows removed.

    `delete from public.documents` is a hard delete — soft deletion for this table
    is `update … set deleted_at`, which the API performs. So this is the correct
    cleanup for test data, in the same spirit as `_purge_now` in the trash tests:
    the API's DELETE is the product's behaviour, not the harness's.
    """
    result = await conn.execute("delete from public.documents where title = $1", title)
    # asyncpg returns the command tag, e.g. "DELETE 7".
    return int(result.split()[-1]) if result else 0


async def cleanup_conversations(conn: asyncpg.Connection) -> int:
    """Remove conversations (and their messages/citations by cascade) created by tests.

    The HTTP-level suites drive the real application pool, so their writes commit
    on purpose — there is no test transaction to roll back, and overriding it
    would mean no longer testing the code path the browser uses. That makes an
    explicit sweep necessary: 8 conversations per run of
    `test_api_spec_endpoints.py` were accumulating, and they are visible through
    `GET /conversations` and through retrieval (a saved answer is searchable
    content), so the leak changes later test behaviour as well as leaving debris.

    Scoped to the seeded demo users, so a run against a real database cannot
    delete real users' conversations.
    """
    result = await conn.execute(
        """
        delete from public.conversations
         where created_by in (
           '11111111-1111-1111-1111-111111111111',
           '22222222-2222-2222-2222-222222222222',
           '33333333-3333-3333-3333-333333333333'
         )
        """
    )
    return int(result.split()[-1]) if result else 0

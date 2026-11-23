"""Operational endpoints and pool configuration.

Two production-readiness defects, both of the "reports success while broken"
kind, which are the ones that survive a green test suite:

1. `/health` returned `{"status": "ok"}` unconditionally. With the DSN pointed
   at a closed port it still answered 200, so a deploy with wrong database
   credentials would pass its health check and fail every real request.

2. The pool used asyncpg's `command_timeout=30`. That is a client-side cancel:
   it aborts whatever transaction is open, which for the ingest worker is the
   single transaction that writes a document's entire chunk and vector set. A
   large PDF crossing 30s on that statement lost the whole write and retried
   into the same wall.

Both are checked by behaviour rather than by reading a config value, because the
bug in each case was a value that looked right.
"""

from __future__ import annotations

import pytest
from app.core.config import get_settings
from app.core.db import get_pool
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
async def fresh_pool():
    """Drop the cached module-level pool around every test here.

    These tests deliberately repoint the DSN. Without this, whichever test runs
    first decides which pool the next one inherits, and a passing suite becomes
    order-dependent — the same class of bug as the reader-order issue these
    tests are about.
    """
    from app.core import db as db_module

    await db_module.close_pool()
    yield
    await db_module.close_pool()


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api.test") as http:
        yield http


async def test_readiness_is_200_when_the_database_answers(client):
    response = await client.get("/ready")
    assert response.status_code == 200, response.text
    assert response.json()["database"] == "ok"


async def test_readiness_is_503_when_the_database_is_unreachable(client, monkeypatch):
    """The regression: `/ready` must fail when the database cannot be reached.

    The pool is closed first so the new DSN is actually used — otherwise the
    check would be answered from already-open connections and pass for the wrong
    reason. Restored in a finally so a failure here cannot corrupt the next test.
    """
    from app.core import db as db_module

    settings = get_settings()
    real = settings.database_url
    await db_module.close_pool()
    monkeypatch.setattr(settings, "database_url", "postgresql://nobody:wrong@127.0.0.1:1/nothing")
    try:
        response = await client.get("/ready")
        assert (
            response.status_code == 503
        ), "readiness reported healthy with an unreachable database"
        body = response.json()
        assert body["status"] == "unavailable"
        assert body["database"] == "unreachable"
    finally:
        monkeypatch.setattr(settings, "database_url", real)
        await db_module.close_pool()


async def test_readiness_does_not_leak_the_connection_string(client, monkeypatch):
    """A DSN parse failure can quote the DSN, which contains a password."""
    from app.core import db as db_module

    settings = get_settings()
    real = settings.database_url
    await db_module.close_pool()
    monkeypatch.setattr(settings, "database_url", "postgresql://user:hunter2@/nohost/db")
    try:
        response = await client.get("/ready")
        # Assert the endpoint actually exists and actually failed, or the
        # "no password in the body" check passes on a 404 and proves nothing.
        assert response.status_code == 503, response.text
        assert "hunter2" not in response.text
    finally:
        monkeypatch.setattr(settings, "database_url", real)
        await db_module.close_pool()


async def test_health_stays_up_when_the_database_is_down(client, monkeypatch):
    """Liveness must NOT depend on the database.

    If it did, a 30-second database blip would get every healthy API machine
    restarted, turning a short outage into a long one.
    """
    from app.core import db as db_module

    settings = get_settings()
    real = settings.database_url
    await db_module.close_pool()
    monkeypatch.setattr(settings, "database_url", "postgresql://nobody:wrong@127.0.0.1:1/nothing")
    try:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    finally:
        monkeypatch.setattr(settings, "database_url", real)
        await db_module.close_pool()


async def test_pool_uses_a_statement_timeout_not_a_client_side_cancel():
    """`command_timeout` aborts the transaction; `statement_timeout` does not.

    Asserted against the live server setting, because the two are configured in
    different places and the bug was picking the wrong one. The value must also
    leave room for a legitimate large ingest statement.
    """
    pool = await get_pool(get_settings())
    async with pool.acquire() as conn:
        raw = await conn.fetchval("show statement_timeout")
        # Postgres reports e.g. "2min" or "120000ms"; normalise.
        assert raw not in ("0", "0ms"), "no statement timeout configured"
        if raw.endswith("min"):
            seconds = float(raw.removesuffix("min")) * 60
        elif raw.endswith("ms"):
            seconds = float(raw.removesuffix("ms")) / 1000
        else:
            seconds = float(raw.removesuffix("s"))
        assert seconds >= 60, (
            f"statement_timeout is {raw}; too tight for a large document's "
            "chunk+vector insert, which is a single statement"
        )
    # Behavioural proof, not an attribute read: run a statement past the old
    # 30s cap inside a transaction and check the transaction survives. With
    # `command_timeout=30` this raised TimeoutError and aborted; with the
    # statement_timeout in place it completes normally.
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("select pg_sleep(31)")
        still_here = await conn.fetchval("select 1")
        assert still_here == 1, (
            "the transaction did not survive a 31s statement — a client-side "
            "command_timeout is still cancelling it, which loses a large "
            "document's entire chunk+vector write"
        )

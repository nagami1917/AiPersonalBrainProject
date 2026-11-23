"""Leaving a workspace, and the audit entry that must survive it.

Two defects were found here, both through `DELETE /workspaces/{id}/members/{me}`:

1. **A member or viewer could never leave.** The route's guard compared
   `member_id` (a *user* id) against `ctx.id` (a *workspace* id). Both are uuid
   strings, so nothing complained; the comparison was simply always true, so
   every caller was treated as removing somebody else and a non-manager got
   `403 insufficient_role` — from a route documented as "remove a member, or
   leave yourself".

2. **After that was fixed, the leave reported success and did nothing.** The
   route audited *after* deleting the membership row, and `record_audit` refuses
   to write for an actor who is no longer a member. `audit.record` caught that,
   but in Postgres a failed statement aborts the whole transaction, so the
   `COMMIT` that `user_db` issued silently became a rollback: HTTP 204, row
   still present. Fixing #1 alone turns a loud 403 into a silent lie, which is
   why both are covered — and why #2's fix has two independent halves.

The audit-order tests are the ones that matter most, because they are the only
place where "we audited it" and "it actually happened" are checked together.
"""

from __future__ import annotations

import asyncpg
import pytest
from app.core import audit
from app.core.config import get_settings
from app.core.db import close_pool
from app.core.deps import get_embedder, get_llm
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from jose import jwt

ALICE = "11111111-1111-1111-1111-111111111111"
CAROL = "33333333-3333-3333-3333-333333333333"
DAVE = "44444444-4444-4444-4444-444444444444"


def _token(user_id: str) -> str:
    settings = get_settings()
    return jwt.encode(
        {"sub": user_id, "role": "authenticated", "aud": "authenticated"},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest.fixture
async def client(embedder, llm):
    app = create_app()
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_llm] = lambda: llm
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api.test") as http:
        yield http
    await close_pool()


async def _db_available() -> bool:
    try:
        pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
        await pool.fetchval("select 1")
        await pool.close()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
async def require_db():
    if not await _db_available():
        pytest.skip("database unavailable")


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Match the application pool.

    Without the jsonb codec, asyncpg requires a pre-serialised *string* for a
    jsonb parameter — so `audit.record` (which correctly passes a dict) fails
    here and would look like a product bug. The pool under test must be configured
    like the one in `app.core.db`.
    """
    import json

    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(
        get_settings().database_url, min_size=1, max_size=2, init=_init_connection
    )
    yield p
    await p.close()


@pytest.fixture
async def alice_ws(pool):
    """Alice's workspace, with the seed's membership rows restored afterwards.

    Several tests here remove Carol (and Dave) from this workspace — that is the
    operation under test. Without an explicit restore, `test_security.py` fails
    downstream because it relies on the seed's "Carol is a viewer" fixture. A
    test that destroys shared state and does not put it back is a bug in the
    test, not in the code it is checking; this is the same debris class that was
    fixed in `test_security.py`'s own history.
    """
    ws = await pool.fetchval(
        "select id from public.workspaces where owner_id = $1 order by personal desc limit 1", ALICE
    )
    assert ws is not None, "seed data missing"
    ws = str(ws)

    async def restore() -> None:
        await pool.execute(
            "delete from public.workspace_members where workspace_id = $1 "
            "and user_id in ($2::uuid, $3::uuid)",
            ws,
            CAROL,
            DAVE,
        )
        # The seed's baseline: Carol is a viewer in Alice's workspace, Dave absent.
        await pool.execute(
            "insert into public.workspace_members (workspace_id, user_id, role) "
            "values ($1, $2, 'viewer') on conflict (workspace_id, user_id) "
            "do update set role = 'viewer'",
            ws,
            CAROL,
        )

    await restore()
    yield ws
    await restore()


async def _join(pool: asyncpg.Pool, ws: str, user_id: str, role: str) -> None:
    """Ensure the user exists, then set their role.

    DAVE is not in `seed.sql`, so the `auth.users` row has to be created before a
    membership row can reference it. `on conflict do nothing` keeps this cheap on
    the seeded users.
    """
    await pool.execute(
        "insert into auth.users (id, email) values ($1, $2) on conflict (id) do nothing",
        user_id,
        f"{user_id}@nexus.test",
    )
    await pool.execute(
        "insert into public.workspace_members (workspace_id, user_id, role) values ($1,$2,$3) "
        "on conflict (workspace_id, user_id) do update set role = excluded.role",
        ws,
        user_id,
        role,
    )


async def _role(pool: asyncpg.Pool, ws: str, user_id: str) -> str | None:
    return await pool.fetchval(
        "select role from public.workspace_members where workspace_id = $1 and user_id = $2",
        ws,
        user_id,
    )


async def _audit_mark(pool: asyncpg.Pool) -> int:
    """Highest audit id right now — the watermark for "written by this test".

    Necessary rather than tidy: the audit table is append-only and shared across
    runs, so `order by id desc limit 1` finds a row from an *earlier* run and
    reports success for a route that wrote nothing. That is how the
    audit-ordering regression first slipped past this file.
    """
    return await pool.fetchval("select coalesce(max(id), 0) from public.audit_events")


async def _audit_since(pool: asyncpg.Pool, mark: int, action: str) -> asyncpg.Record | None:
    """The newest entry for `action` written after `mark`, if any."""
    return await pool.fetchrow(
        "select action, actor_id::text as actor, target_id::text as target "
        "from public.audit_events where action = $1 and id > $2 order by id desc limit 1",
        action,
        mark,
    )


# ---------------------------------------------------------------------------
# Defect 1: a non-manager can leave.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["viewer", "member"])
async def test_a_non_manager_can_leave_a_workspace(client, pool, alice_ws, role):
    """The route is documented as "or leave yourself" — so it has to do that."""
    await _join(pool, alice_ws, CAROL, role)

    response = await client.delete(
        f"/api/v1/workspaces/{alice_ws}/members/{CAROL}", headers=_auth(CAROL)
    )
    assert (
        response.status_code == 204
    ), f"a {role} could not leave: {response.status_code} {response.text}"
    assert await _role(pool, alice_ws, CAROL) is None, "204 was returned but the row survived"


async def test_a_non_manager_cannot_remove_someone_else(client, pool, alice_ws):
    """The permission half of the same guard must still hold."""
    await _join(pool, alice_ws, CAROL, "member")
    await _join(pool, alice_ws, DAVE, "member")

    response = await client.delete(
        f"/api/v1/workspaces/{alice_ws}/members/{DAVE}", headers=_auth(CAROL)
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "insufficient_role"
    assert await _role(pool, alice_ws, DAVE) == "member", "a member removed another member"


async def test_an_admin_can_still_remove_a_member(client, pool, alice_ws):
    await _join(pool, alice_ws, CAROL, "admin")
    await _join(pool, alice_ws, DAVE, "member")

    response = await client.delete(
        f"/api/v1/workspaces/{alice_ws}/members/{DAVE}", headers=_auth(CAROL)
    )
    assert response.status_code == 204, response.text
    assert await _role(pool, alice_ws, DAVE) is None


async def test_the_owner_still_cannot_be_removed(client, pool, alice_ws):
    response = await client.delete(
        f"/api/v1/workspaces/{alice_ws}/members/{ALICE}", headers=_auth(ALICE)
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "cannot_remove_owner"
    assert await _role(pool, alice_ws, ALICE) == "owner"


async def test_removing_a_non_member_is_404(client, pool, alice_ws):
    await _join(pool, alice_ws, CAROL, "admin")

    response = await client.delete(
        f"/api/v1/workspaces/{alice_ws}/members/{DAVE}", headers=_auth(CAROL)
    )
    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Defect 2: the audit entry is written, and the write it describes happens.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["viewer", "member"])
async def test_leaving_is_audited(client, pool, alice_ws, role):
    """The entry is the whole point of `workspace.member.leave`.

    It also pins the ordering: `record_audit` refuses for a non-member, so if the
    route ever audits after the delete again, this is the test that fails.
    """
    await _join(pool, alice_ws, CAROL, role)
    mark = await _audit_mark(pool)

    response = await client.delete(
        f"/api/v1/workspaces/{alice_ws}/members/{CAROL}", headers=_auth(CAROL)
    )
    assert response.status_code == 204

    row = await _audit_since(pool, mark, "workspace.member.leave")
    assert row is not None, "the leave was not audited"
    assert row["actor"] == CAROL
    assert row["target"] == CAROL


async def test_removing_someone_is_audited_as_a_removal_not_a_leave(client, pool, alice_ws):
    """The two facts share SQL but must not share an audit action."""
    await _join(pool, alice_ws, CAROL, "admin")
    await _join(pool, alice_ws, DAVE, "member")
    mark = await _audit_mark(pool)

    response = await client.delete(
        f"/api/v1/workspaces/{alice_ws}/members/{DAVE}", headers=_auth(CAROL)
    )
    assert response.status_code == 204

    row = await _audit_since(pool, mark, "workspace.member.remove")
    assert row is not None, "the removal was not audited"
    assert row["actor"] == CAROL
    assert row["target"] == DAVE


async def test_a_failed_audit_does_not_silently_roll_back_the_operation(pool):
    """The mechanism, tested directly rather than through a route.

    This is the regression that matters: an audit insert that raises used to
    leave the transaction aborted, so `COMMIT` became a rollback while the route
    still returned 204. The savepoint in `audit.record` is what stops that.

    Driven with a deliberately impossible audit call (an actor who is not a
    member) rather than by mocking, so the real failure path is exercised.
    """
    ws = await pool.fetchval("select id from public.workspaces where owner_id = $1 limit 1", ALICE)
    note_id = await pool.fetchval(
        "insert into public.notes (workspace_id, created_by, title, content) "
        "values ($1,$2,'audit probe','x') returning id",
        ws,
        ALICE,
    )

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        # Act as a signed-in user who is not a member of the workspace.
        await conn.execute("set local role authenticated")
        await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", DAVE)
        await conn.execute("select set_config('request.jwt.claim.role', 'authenticated', true)")
        try:
            # A write the caller is not entitled to is impossible via RLS, so use
            # a superuser-visible no-op to stand in for "the route's real work":
            # what is being tested is that `record` does not poison the tx.
            await audit.record(
                conn,
                workspace_id=str(ws),
                action="note.delete",
                target_type="note",
                target_id=str(note_id),
            )
            # If the savepoint works, this still runs and the commit is real.
            ok = await conn.fetchval("select 1")
        finally:
            await tx.commit()
        assert ok == 1

    # Nothing was recorded (the actor is not a member), and nothing was broken.
    recorded = await pool.fetchval(
        "select count(*) from public.audit_events "
        "where action = 'note.delete' and actor_id = $1",
        DAVE,
    )
    assert recorded == 0


async def test_audit_record_succeeds_for_a_member(pool):
    """The positive half: the savepoint must not swallow successful writes."""
    ws = await pool.fetchval("select id from public.workspaces where owner_id = $1 limit 1", ALICE)
    before = await pool.fetchval(
        "select count(*) from public.audit_events where action = 'note.update'"
    )

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await conn.execute("set local role authenticated")
        await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", ALICE)
        await conn.execute("select set_config('request.jwt.claim.role', 'authenticated', true)")
        await audit.record(conn, workspace_id=str(ws), action="note.update", target_type="note")
        await tx.commit()

    after = await pool.fetchval(
        "select count(*) from public.audit_events where action = 'note.update'"
    )
    assert after == before + 1, "a successful audit write was lost"

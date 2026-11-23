"""Ownership escalation must be impossible from the *browser*, not just the route.

Migration 0018 let an invited admin take over a workspace with three statements
addressed to PostgREST — no FastAPI involved:

    update workspace_members set role = 'viewer' where user_id = <the owner>;
    update workspace_members set role = 'owner'  where user_id = <me>;
    update workspaces        set owner_id = <me> where id = <the workspace>;

The API route `PATCH /workspaces/{id}/members/{member_id}` already refused this
(`cannot_modify_owner`), which is exactly why it went unnoticed: every existing
test called the route, and the route was never the boundary. The browser holds
the anon key and the user's own JWT, so the boundary is the database.

This file is the browser's eye view. It deliberately does **not** use the API for
the attack half — it opens a connection, sets the same session claims PostgREST
sets, and issues the statement directly. That is the request an attacker writes,
and the only reason to go through the API would be to test the thing that was
already correct.

Discrimination: with migration 0019 reverted these tests fail (A1, A3 and A4 all
return affected rows). With it applied the same statements are refused. The
mirror-image half — legitimate management still working — is in the same file so
that "refuse everything" cannot pass as a fix.
"""

from __future__ import annotations

import asyncpg
import pytest
from app.core.config import get_settings

ALICE = "11111111-1111-1111-1111-111111111111"  # owner of Alice's workspace
BOB = "22222222-2222-2222-2222-222222222222"  # installed as admin below
CAROL = "33333333-3333-3333-3333-333333333333"  # installed as member below


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


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
async def fixture(pool):
    """Alice's workspace, with Bob as admin and Carol as member.

    Rows are restored on teardown, because these tests write membership rows and
    a leaked `admin` would make a later test fail for the wrong reason.
    """
    ws = await pool.fetchval(
        "select id from public.workspaces where owner_id = $1 limit 1", ALICE
    )
    assert ws is not None, "seed data missing: Alice has no workspace"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into public.workspace_members (workspace_id, user_id, role)
            values ($1, $2, 'admin'), ($1, $3, 'member')
            on conflict (workspace_id, user_id) do update set role = excluded.role
            """,
            ws,
            BOB,
            CAROL,
        )
        # Reset the owner in case an earlier run left it moved.
        await conn.execute(
            "update public.workspaces set owner_id = $1 where id = $2", ALICE, ws
        )
        await conn.execute(
            "update public.workspace_members set role = 'owner' "
            "where workspace_id = $1 and user_id = $2",
            ws,
            ALICE,
        )

    yield ws

    # Restore the seed exactly: Bob absent, Carol a *viewer*. Deleting both and
    # walking away leaves `test_security.py` and `test_api_workspaces.py` reading
    # a workspace Carol is no longer in, which surfaces as an unrelated failure
    # several files later. The baseline has to be put back precisely, not
    # approximately.
    async with pool.acquire() as conn:
        await conn.execute(
            "delete from public.workspace_members "
            "where workspace_id = $1 and user_id = any($2::uuid[])",
            ws,
            [BOB, CAROL],
        )
        await conn.execute(
            "insert into public.workspace_members (workspace_id, user_id, role) "
            "values ($1, $2, 'viewer') on conflict (workspace_id, user_id) "
            "do update set role = 'viewer'",
            ws,
            CAROL,
        )
        await conn.execute(
            "update public.workspaces set owner_id = $1 where id = $2", ALICE, ws
        )


async def _as(conn: asyncpg.Connection, user_id: str) -> None:
    """Become `user_id` for the duration of the current transaction.

    This is the exact session state PostgREST establishes from a JWT: the role
    `authenticated`, and the `request.jwt.claim.*` settings that `auth.uid()`
    reads. `set_config(..., true)` is transaction-local, so the caller must have
    opened a transaction — which is also what makes the `set local role` revert
    automatically and keeps the pool clean.
    """
    await conn.execute("set local role authenticated")
    await conn.execute(
        "select set_config('request.jwt.claim.sub', $1, true)", user_id
    )
    await conn.execute(
        "select set_config('request.jwt.claim.role', 'authenticated', true)"
    )


async def _attempt(pool: asyncpg.Pool, user_id: str, sql: str, *args) -> int:
    """Run `sql` as `user_id` and return the number of rows affected.

    A refused statement is either an exception (42501) or zero rows; both are
    returned as 0 so each test can assert the single fact that matters — the
    write did not happen. Rolling back afterwards means a regression cannot
    corrupt the fixture for the next test.
    """
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await _as(conn, user_id)
            status = await conn.execute(sql, *args)
        except asyncpg.InsufficientPrivilegeError:
            return 0
        finally:
            await tx.rollback()
    # asyncpg returns a command tag: "UPDATE 3", "DELETE 1".
    return int(status.split()[-1]) if status and status.split()[-1].isdigit() else 0


# ---------------------------------------------------------------------------
# The attacks. Each one was a successful write on migration 0018.
# ---------------------------------------------------------------------------


async def test_admin_cannot_demote_the_owner(fixture, pool):
    """Step one of the takeover: free the single-owner slot."""
    ws = fixture
    changed = await _attempt(
        pool,
        BOB,
        "update public.workspace_members set role = 'viewer' "
        "where workspace_id = $1 and user_id = $2",
        ws,
        ALICE,
    )
    assert changed == 0, "an admin demoted the workspace owner — takeover step 1 works"

    role = await pool.fetchval(
        "select role from public.workspace_members where workspace_id = $1 and user_id = $2",
        ws,
        ALICE,
    )
    assert role == "owner"


async def test_admin_cannot_promote_itself_to_owner(fixture, pool):
    ws = fixture
    changed = await _attempt(
        pool,
        BOB,
        "update public.workspace_members set role = 'owner' "
        "where workspace_id = $1 and user_id = $2",
        ws,
        BOB,
    )
    assert changed == 0, "an admin promoted itself to owner"

    role = await pool.fetchval(
        "select role from public.workspace_members where workspace_id = $1 and user_id = $2",
        ws,
        BOB,
    )
    assert role == "admin"


async def test_admin_cannot_rewrite_owner_id(fixture, pool):
    """The third statement of the chain, and the one that made it stick."""
    ws = fixture
    changed = await _attempt(
        pool,
        BOB,
        "update public.workspaces set owner_id = $1 where id = $2",
        BOB,
        ws,
    )
    assert changed == 0, "an admin rewrote workspaces.owner_id"

    owner = await pool.fetchval(
        "select owner_id from public.workspaces where id = $1", ws
    )
    assert str(owner) == ALICE


async def test_admin_cannot_delete_the_owner_membership(fixture, pool):
    """A second route to the same place: delete the owner row instead of demoting."""
    ws = fixture
    changed = await _attempt(
        pool,
        BOB,
        "delete from public.workspace_members where workspace_id = $1 and user_id = $2",
        ws,
        ALICE,
    )
    assert changed == 0, "an admin deleted the owner's membership row"

    exists = await pool.fetchval(
        "select exists(select 1 from public.workspace_members "
        "where workspace_id = $1 and user_id = $2)",
        ws,
        ALICE,
    )
    assert exists


async def test_member_cannot_promote_itself(fixture, pool):
    """A plain member has no management rights at all — the outermost check."""
    ws = fixture
    changed = await _attempt(
        pool,
        CAROL,
        "update public.workspace_members set role = 'admin' "
        "where workspace_id = $1 and user_id = $2",
        ws,
        CAROL,
    )
    assert changed == 0

    role = await pool.fetchval(
        "select role from public.workspace_members where workspace_id = $1 and user_id = $2",
        ws,
        CAROL,
    )
    assert role == "member"


async def test_admin_cannot_grant_ownership_to_someone_else(fixture, pool):
    """The subtle one: `using` sees a *member* row, so only `with check` can stop it."""
    ws = fixture
    changed = await _attempt(
        pool,
        BOB,
        "update public.workspace_members set role = 'owner' "
        "where workspace_id = $1 and user_id = $2",
        ws,
        CAROL,
    )
    assert changed == 0, "an admin handed ownership to a third party"

    role = await pool.fetchval(
        "select role from public.workspace_members where workspace_id = $1 and user_id = $2",
        ws,
        CAROL,
    )
    assert role == "member"


async def test_the_three_statement_takeover_chain_fails(fixture, pool):
    """The whole attack, in one transaction, as a browser would send it.

    Worth stating separately because the individual steps are misleading on their
    own: `uq_workspace_single_owner` already blocks promoting yourself while
    another owner exists, so a single-statement test of step 2 passes on 0018 for
    the wrong reason. The chain removes that obstacle first — demote the owner,
    *then* take the free slot — and on 0018 it completed all three statements.

    Modelled as one transaction because that is how an attacker does it: a
    half-applied takeover would leave the workspace ownerless and be noticed.
    """
    ws = fixture

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await _as(conn, BOB)

            # 1. demote the owner
            s1 = await conn.execute(
                "update public.workspace_members set role = 'viewer' "
                "where workspace_id = $1 and user_id = $2",
                ws,
                ALICE,
            )
            # 2. take the now-free owner slot
            s2 = await conn.execute(
                "update public.workspace_members set role = 'owner' "
                "where workspace_id = $1 and user_id = $2",
                ws,
                BOB,
            )
            # 3. make the workspace record agree
            s3 = await conn.execute(
                "update public.workspaces set owner_id = $1 where id = $2", BOB, ws
            )
        except asyncpg.InsufficientPrivilegeError:
            # Refused at step 1 or 3 by the trigger/privilege layer. That is the
            # fix working, and it unwinds any earlier step.
            await tx.rollback()
        else:
            await tx.rollback()
            assert (s1, s2, s3) == ("UPDATE 0", "UPDATE 0", "UPDATE 0"), (
                "TAKEOVER SUCCEEDED via three PostgREST statements: "
                f"demote={s1}, promote={s2}, owner_id={s3}"
            )

    # Whatever path was taken, the world is unchanged.
    owner = await pool.fetchval(
        "select owner_id from public.workspaces where id = $1", ws
    )
    assert str(owner) == ALICE, "workspaces.owner_id was moved"
    roles = dict(
        await pool.fetch(
            "select user_id::text, role from public.workspace_members where workspace_id = $1",
            ws,
        )
    )
    assert roles[ALICE] == "owner"
    assert roles[BOB] == "admin"


async def test_owner_cannot_be_demoted_once_more_via_a_different_column(fixture, pool):
    """`role` is not the only way to lose ownership; check the column the trigger guards.

    A regression that re-added the table-level UPDATE grant would let this
    through even with the policies in place, so it is worth its own assertion.
    """
    ws = fixture
    changed = await _attempt(
        pool,
        BOB,
        "update public.workspaces set owner_id = $1 where id = $2",
        CAROL,
        ws,
    )
    assert changed == 0

    owner = await pool.fetchval(
        "select owner_id from public.workspaces where id = $1", ws
    )
    assert str(owner) == ALICE


# ---------------------------------------------------------------------------
# Everything legitimate must still work. Without these, a migration that simply
# revoked UPDATE would pass the half above.
# ---------------------------------------------------------------------------


async def test_admin_can_still_change_another_members_role(fixture, pool):
    ws = fixture
    changed = await _attempt(
        pool,
        BOB,
        "update public.workspace_members set role = 'viewer' "
        "where workspace_id = $1 and user_id = $2",
        ws,
        CAROL,
    )
    assert changed == 1, "delegated management broke: an admin cannot demote a member"


async def test_admin_can_still_remove_a_non_owner_member(fixture, pool):
    ws = fixture
    changed = await _attempt(
        pool,
        BOB,
        "delete from public.workspace_members where workspace_id = $1 and user_id = $2",
        ws,
        CAROL,
    )
    assert changed == 1, "an admin can no longer remove a member"


async def test_a_member_can_still_leave(fixture, pool):
    """Leaving is not management and must not need it."""
    ws = fixture
    changed = await _attempt(
        pool,
        CAROL,
        "delete from public.workspace_members where workspace_id = $1 and user_id = $2",
        ws,
        CAROL,
    )
    assert changed == 1, "a member can no longer leave a workspace"


@pytest.mark.parametrize("who", [ALICE, BOB])
async def test_managers_can_still_rename_the_workspace(fixture, pool, who):
    """`name` must stay writable — the API's only workspace UPDATE."""
    ws = fixture
    changed = await _attempt(
        pool,
        who,
        "update public.workspaces set name = 'Renamed By Test' where id = $1",
        ws,
    )
    assert changed == 1, "renaming the workspace broke"


async def test_trusted_context_can_still_set_ownership(fixture, pool):
    """The guard is keyed on the *actor*, not the target.

    A service-role connection has no `request.jwt.claim.sub`, so provisioning and
    a future reviewed transfer function must keep working. This is asserted so
    the fix cannot regress into "nothing can ever move ownership", which would
    make an ownership-transfer feature impossible to build.
    """
    ws = fixture
    status = await pool.execute(
        "update public.workspaces set owner_id = $1 where id = $2", ALICE, ws
    )
    assert status.endswith("1")

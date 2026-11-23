"""HTTP-level tenancy tests.

These exercise the real router, the real dependencies and the real database, so
they cover the thing unit tests cannot: that the workspace in the URL decides
what the request can see, and that a foreign workspace is a 404 at the HTTP
boundary rather than only inside a repository function.
"""

from __future__ import annotations

import asyncpg
import pytest
from app.core.config import get_settings
from app.core.db import close_pool
from app.core.deps import get_embedder, get_llm
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from jose import jwt

ALICE = "11111111-1111-1111-1111-111111111111"
CAROL = "33333333-3333-3333-3333-333333333333"


def _token(user_id: str, settings=None) -> str:  # type: ignore[no-untyped-def]
    settings = settings or get_settings()
    return jwt.encode(
        {"sub": user_id, "role": "authenticated", "aud": "authenticated"},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )


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


async def _workspace_of(user_id: str) -> str:
    pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
    try:
        workspace_id = await pool.fetchval(
            "select id from public.workspaces where owner_id=$1 and personal limit 1", user_id
        )
    finally:
        await pool.close()
    if workspace_id is None:
        pytest.skip("seed data missing: run `make db-reset` first")
    return str(workspace_id)


class TestWorkspaceRouting:
    async def test_lists_only_the_callers_workspaces(self, client):
        response = await client.get(
            "/api/v1/workspaces", headers={"Authorization": f"Bearer {_token(CAROL)}"}
        )
        assert response.status_code == 200
        payload = response.json()
        # Carol owns her personal workspace and was invited to Alice's as viewer.
        assert len(payload) == 2
        roles = {w["role"] for w in payload}
        assert "viewer" in roles and "owner" in roles

    async def test_foreign_workspace_is_not_found(self, client):
        alice_ws = await _workspace_of(ALICE)
        response = await client.get(
            f"/api/v1/workspaces/{alice_ws}/notes",
            headers={"Authorization": f"Bearer {_token(CAROL)}"},
        )
        # Carol is a viewer *in* Alice's workspace, so she may read.
        assert response.status_code == 200

        # But a workspace she is not a member of at all is invisible.
        bob_ws = await _workspace_of("22222222-2222-2222-2222-222222222222")
        response = await client.get(
            f"/api/v1/workspaces/{bob_ws}/notes",
            headers={"Authorization": f"Bearer {_token(CAROL)}"},
        )
        assert response.status_code == 404

    async def test_missing_token_is_401(self, client):
        response = await client.get("/api/v1/workspaces")
        assert response.status_code == 401

    async def test_viewer_cannot_create_a_note(self, client):
        alice_ws = await _workspace_of(ALICE)
        response = await client.post(
            f"/api/v1/workspaces/{alice_ws}/notes",
            json={"title": "smuggled", "content": "a viewer must not be able to write this"},
            headers={"Authorization": f"Bearer {_token(CAROL)}"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "insufficient_role"

    async def test_owner_can_create_a_note(self, client):
        alice_ws = await _workspace_of(ALICE)
        response = await client.post(
            f"/api/v1/workspaces/{alice_ws}/notes",
            json={"title": "An API-created note", "content": "written through the router"},
            headers={"Authorization": f"Bearer {_token(ALICE)}"},
        )
        assert response.status_code == 202
        payload = response.json()
        assert payload["note"]["status"] == "pending"
        assert payload["job_id"]

        # Clean up so the test is re-runnable against the same database.
        note_id = payload["note"]["id"]
        delete = await client.delete(
            f"/api/v1/workspaces/{alice_ws}/notes/{note_id}",
            headers={"Authorization": f"Bearer {_token(ALICE)}"},
        )
        assert delete.status_code == 204


class TestMeta:
    async def test_capabilities_are_published(self, client):
        response = await client.get("/api/v1/meta")
        assert response.status_code == 200
        payload = response.json()
        assert payload["embedding_model"] == get_settings().pinned_embedding_model
        assert payload["embedding_dim"] == 1536


class TestWorkspaceCreation:
    """Regression tests for migrations 0014 and 0015.

    Workspace creation was broken twice, and both failures were invisible to
    this suite because every seeded user already had a workspace created by the
    SECURITY DEFINER signup trigger — which bypasses RLS — so nothing ever
    exercised `POST /workspaces` as an ordinary `authenticated` user.

    0014: `insert ... returning *` needs a SELECT policy that can see the new
          row, and the only one was `is_workspace_member(id)` — false for a
          workspace whose membership row did not exist yet.
    0015: the owner's first membership row could only be written by someone who
          was already a managing member.
    """

    async def test_owner_can_create_a_workspace(self, client):
        response = await client.post(
            "/api/v1/workspaces",
            headers={"Authorization": f"Bearer {_token(ALICE)}"},
            json={"name": "Regression workspace"},
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["role"] == "owner"
        assert payload["owner_id"] == ALICE
        assert payload["member_count"] == 1

        # The workspace must be readable afterwards: 0014 fixed the RETURNING
        # read-back, but a policy that only satisfied the write would still
        # leave the caller unable to open what they just created.
        workspace_id = payload["id"]
        read_back = await client.get(
            f"/api/v1/workspaces/{workspace_id}",
            headers={"Authorization": f"Bearer {_token(ALICE)}"},
        )
        assert read_back.status_code == 200

        # And it must appear in the caller's own listing.
        listing = await client.get(
            "/api/v1/workspaces", headers={"Authorization": f"Bearer {_token(ALICE)}"}
        )
        assert workspace_id in [w["id"] for w in listing.json()]

        # A new workspace is private to its creator on creation.
        foreign = await client.get(
            f"/api/v1/workspaces/{workspace_id}",
            headers={"Authorization": f"Bearer {_token(CAROL)}"},
        )
        assert foreign.status_code == 404

        cleanup = await client.delete(
            f"/api/v1/workspaces/{workspace_id}",
            headers={"Authorization": f"Bearer {_token(ALICE)}"},
        )
        assert cleanup.status_code in (204, 200)

    async def test_creation_ignores_client_supplied_ownership(self, client):
        # owner_id and id are not fields of WorkspaceCreate; if they were ever
        # added, the INSERT policy pins owner_id to auth.uid() anyway. This
        # asserts the policy, not the schema, so it keeps holding if the schema
        # changes.
        response = await client.post(
            "/api/v1/workspaces",
            headers={"Authorization": f"Bearer {_token(ALICE)}"},
            json={
                "name": "Injection attempt",
                "owner_id": CAROL,
                "id": "00000000-0000-0000-0000-000000000001",
            },
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["owner_id"] == ALICE
        assert payload["id"] != "00000000-0000-0000-0000-000000000001"

        await client.delete(
            f"/api/v1/workspaces/{payload['id']}",
            headers={"Authorization": f"Bearer {_token(ALICE)}"},
        )

    async def test_owner_role_cannot_be_granted_through_the_api(self, client):
        # Ownership is not delegable. `MemberInvite.role` is a closed set with no
        # "owner" member, so this is a 422 at the schema boundary. That is the
        # outermost of four independent guards (schema, require_manage, the
        # 0015 policy's `role = 'owner'` clause, and workspaces.owner_id).
        alice_ws = await _workspace_of(ALICE)
        response = await client.post(
            f"/api/v1/workspaces/{alice_ws}/members",
            headers={"Authorization": f"Bearer {_token(ALICE)}"},
            json={"email": "carol@example.com", "role": "owner"},
        )
        assert response.status_code == 422, response.text

    async def test_viewer_cannot_promote_themselves_to_admin(self, client):
        # 0015 lets an owner bootstrap their own row. The escape hatch must not
        # be reachable by anyone else: `user_id = auth.uid() and role='owner'`
        # only holds when the workspace is already theirs.
        #
        # Carol is a *viewer* of Alice's workspace, so this is a 403 and not a
        # 404 — she can see the workspace, so its existence is not a secret from
        # her. `test_foreign_workspace_is_not_found` covers the non-member case
        # where the answer must be 404. The distinction is deliberate.
        alice_ws = await _workspace_of(ALICE)
        response = await client.post(
            f"/api/v1/workspaces/{alice_ws}/members",
            headers={"Authorization": f"Bearer {_token(CAROL)}"},
            json={"email": "carol@example.com", "role": "admin"},
        )
        assert response.status_code == 403, response.text

        # Any change the attempt might have made is absent: Carol is still a
        # viewer, and Alice's workspace gained no member.
        roster = await client.get(
            f"/api/v1/workspaces/{alice_ws}/members",
            headers={"Authorization": f"Bearer {_token(ALICE)}"},
        )
        assert roster.status_code == 200
        carol = [m for m in roster.json() if m["user_id"] == CAROL]
        assert [m["role"] for m in carol] == ["viewer"]

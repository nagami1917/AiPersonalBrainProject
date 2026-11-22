"""The security architecture, tested end to end where it can be.

`tests/unit/` proves the modules behave. This file proves the **routes** behave:
that the limiter actually rejects a third chat, that the audit function refuses
a forged actor, and that a document's storage path cannot be walked out of its
workspace. Those are the failures that a unit test on a pure function cannot
catch, because the bug would be a route that forgot to call it.
"""

from __future__ import annotations

import asyncpg
import pytest
from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import close_pool
from app.core.deps import get_embedder, get_llm
from app.core.ratelimit import Limit, RateLimiter
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from jose import jwt

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
CAROL = "33333333-3333-3333-3333-333333333333"


def _auth(user_id: str = ALICE) -> dict[str, str]:
    settings = get_settings()
    token = jwt.encode(
        {"sub": user_id, "role": "authenticated", "aud": "authenticated"},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client(embedder, llm):
    app = create_app()
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_llm] = lambda: llm
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api.test") as http:
        yield http
    await close_pool()


@pytest.fixture
async def made_documents():
    """Collects ids so every test's rows are removed even when it fails."""
    created: list[str] = []
    yield created
    await _cleanup_documents(*created)


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


async def _pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=4)


async def _cleanup_documents(*document_ids):
    """Remove documents this suite created.

    The database tests share one database, and a row with no `raw_text` is a
    landmine for any later test that summarizes "a document". Rather than asking
    every other test to defend itself, this suite takes its own rubbish out.
    """
    ids = [str(value) for value in document_ids if value]
    if not ids:
        return
    pool = await _pool()
    try:
        await pool.execute("delete from public.documents where id = any($1::uuid[])", ids)
    finally:
        await pool.close()


async def _make_document(
    workspace: str,
    *,
    title: str,
    file_path: str | None = None,
    created: list[str] | None = None,
) -> str:
    """Insert a document and register it for cleanup.

    One helper rather than six inline inserts: it is the single place that
    decides a test document has no `raw_text`, which is what makes it invisible
    to a later summarize test instead of a trap.
    """
    pool = await _pool()
    try:
        document_id = await pool.fetchval(
            """insert into public.documents
                 (workspace_id, uploaded_by, title, filename, mime_type, file_path, status)
               values ($1, $2, $3, 'a.pdf', 'application/pdf',
                       coalesce($4, $1::uuid::text || '/' || gen_random_uuid()::text || '/a.pdf'),
                       'ready')
               returning id""",
            workspace,
            ALICE,
            title,
            file_path,
        )
    finally:
        await pool.close()
    if created is not None:
        created.append(str(document_id))
    return str(document_id)


async def _workspace_of(user_id: str = ALICE) -> str:
    pool = await _pool()
    try:
        value = await pool.fetchval(
            "select id from public.workspaces where owner_id=$1 and personal limit 1", user_id
        )
    finally:
        await pool.close()
    if value is None:
        pytest.skip("seed data missing")
    return str(value)


@pytest.fixture(autouse=True)
async def reset_limiter():
    """Each test starts with a clean budget and leaves none behind."""
    yield
    ratelimit.set_limiter(None)


# ---------------------------------------------------------------------------
# Rule 8 — rate limits on expensive endpoints
# ---------------------------------------------------------------------------


class TestRateLimitOnChat:
    async def test_the_third_chat_is_rejected_when_the_budget_is_two(self, client, monkeypatch):
        """The limit must be enforced by the route, not merely available to it."""
        monkeypatch.setattr(get_settings(), "rate_limit_enabled", True, raising=False)
        ratelimit.set_limiter(
            RateLimiter(
                user_limit=Limit(calls=2, seconds=60),
                workspace_limit=Limit(calls=100, seconds=60),
            )
        )
        workspace = await _workspace_of()

        for _ in range(2):
            response = await client.post(
                f"/v1/workspaces/{workspace}/chat",
                headers=_auth(),
                json={"message": "what is in my notes?"},
            )
            assert response.status_code == 200, response.text

        blocked = await client.post(
            f"/v1/workspaces/{workspace}/chat",
            headers=_auth(),
            json={"message": "what is in my notes?"},
        )
        assert blocked.status_code == 429
        error = blocked.json()["error"]
        assert error["code"] == "rate_limited"
        assert error["details"]["scope"] == "user"
        assert error["details"]["retry_after"] >= 1

    async def test_the_body_carried_workspace_route_is_limited_too(self, client, monkeypatch):
        """`/ai/chat` takes the workspace in the body; it must not be a bypass.

        A limit applied only to the canonical path would leave the alias as an
        unlimited door to the same provider call.
        """
        monkeypatch.setattr(get_settings(), "rate_limit_enabled", True, raising=False)
        ratelimit.set_limiter(
            RateLimiter(
                user_limit=Limit(calls=1, seconds=60),
                workspace_limit=Limit(calls=100, seconds=60),
            )
        )
        workspace = await _workspace_of()
        first = await client.post(
            "/v1/ai/chat", headers=_auth(), json={"workspace_id": workspace, "message": "hi"}
        )
        assert first.status_code == 200, first.text

        second = await client.post(
            "/v1/ai/chat", headers=_auth(), json={"workspace_id": workspace, "message": "hi"}
        )
        assert second.status_code == 429

    async def test_streaming_is_limited_before_the_stream_opens(self, client, monkeypatch):
        """A 429 must be a real HTTP status, not an event inside a 200 stream."""
        monkeypatch.setattr(get_settings(), "rate_limit_enabled", True, raising=False)
        ratelimit.set_limiter(
            RateLimiter(
                user_limit=Limit(calls=1, seconds=60),
                workspace_limit=Limit(calls=100, seconds=60),
            )
        )
        workspace = await _workspace_of()
        await client.post(
            f"/v1/workspaces/{workspace}/chat",
            headers=_auth(),
            json={"message": "first"},
        )
        blocked = await client.post(
            f"/v1/workspaces/{workspace}/chat/stream",
            headers=_auth(),
            json={"message": "second"},
        )
        assert blocked.status_code == 429

    async def test_summarize_has_its_own_budget(self, client, monkeypatch):
        """Chat and summarise are separate pools, so one cannot starve the other."""
        monkeypatch.setattr(get_settings(), "rate_limit_enabled", True, raising=False)
        ratelimit.set_limiter(
            RateLimiter(
                user_limit=Limit(calls=1, seconds=60),
                workspace_limit=Limit(calls=100, seconds=60),
            )
        )
        workspace = await _workspace_of()
        await client.post(
            f"/v1/workspaces/{workspace}/chat", headers=_auth(), json={"message": "hi"}
        )
        summary = await client.post(
            "/v1/ai/summarize",
            headers=_auth(),
            json={"workspace_id": workspace, "text": "some text to summarize"},
        )
        assert summary.status_code == 200, summary.text

    async def test_a_retry_after_header_is_not_required_but_details_are(self, client, monkeypatch):
        """The 429 body must say when to come back, or a client can only guess."""
        monkeypatch.setattr(get_settings(), "rate_limit_enabled", True, raising=False)
        ratelimit.set_limiter(
            RateLimiter(
                user_limit=Limit(calls=0, seconds=30),
                workspace_limit=Limit(calls=100, seconds=60),
            )
        )
        workspace = await _workspace_of()
        response = await client.post(
            f"/v1/workspaces/{workspace}/chat", headers=_auth(), json={"message": "hi"}
        )
        assert response.status_code == 429
        assert response.json()["error"]["details"]["retry_after"] <= 31


# ---------------------------------------------------------------------------
# Rule 5 / 7 — downloads
# ---------------------------------------------------------------------------


class TestDownloadUrls:
    async def test_a_member_gets_a_signed_url_for_a_real_document(self, client, made_documents):
        workspace = await _workspace_of()
        document_id = await _make_document(
            workspace, title="Signed url doc", created=made_documents
        )

        response = await client.get(
            f"/v1/workspaces/{workspace}/documents/{document_id}/download-url", headers=_auth()
        )
        # The signature call goes to the real Supabase URL, which is unreachable
        # in this environment — so a 503 here still proves authorisation passed.
        assert response.status_code in (200, 503), response.text

    async def test_a_path_outside_the_workspace_is_refused(self, client, made_documents):
        """The belt-and-braces check: a corrupted row must not be signed.

        Without it, a row pointing at another tenant's prefix would produce a
        working download URL — the service role signs anything it is asked to.
        """
        workspace = await _workspace_of()
        document_id = await _make_document(
            workspace,
            title="Escaped doc",
            file_path="00000000-0000-0000-0000-000000000000/x/a.pdf",
            created=made_documents,
        )

        response = await client.get(
            f"/v1/workspaces/{workspace}/documents/{document_id}/download-url", headers=_auth()
        )
        assert response.status_code == 404

    async def test_another_workspace_cannot_sign_my_document(self, client, made_documents):
        alice_ws = await _workspace_of(ALICE)
        bob_ws = await _workspace_of(BOB)
        document_id = await _make_document(alice_ws, title="Alice only", created=made_documents)

        response = await client.get(
            f"/v1/workspaces/{bob_ws}/documents/{document_id}/download-url", headers=_auth(BOB)
        )
        assert response.status_code == 404

    async def test_the_document_detail_no_longer_leaks_the_storage_path(
        self, client, made_documents
    ):
        """Rule 5: the client has no use for the path, and it is a hint for abuse."""
        workspace = await _workspace_of()
        document_id = await _make_document(workspace, title="Path hidden", created=made_documents)

        response = await client.get(
            f"/v1/workspaces/{workspace}/documents/{document_id}", headers=_auth()
        )
        assert response.status_code == 200
        assert response.json()["file_path"] is None


# ---------------------------------------------------------------------------
# Rule 9 — uploads at the API boundary
# ---------------------------------------------------------------------------


class TestUploadValidation:
    async def test_a_pdf_declared_as_text_is_refused(self, client):
        workspace = await _workspace_of()
        response = await client.post(
            f"/v1/workspaces/{workspace}/documents/upload-url",
            headers=_auth(),
            json={"filename": "report.pdf", "mime_type": "text/plain", "file_size": 1024},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "extension_mime_mismatch"

    async def test_an_executable_is_refused(self, client):
        workspace = await _workspace_of()
        response = await client.post(
            f"/v1/workspaces/{workspace}/documents/upload-url",
            headers=_auth(),
            json={
                "filename": "setup.exe",
                "mime_type": "application/x-msdownload",
                "file_size": 10,
            },
        )
        assert response.status_code == 422

    async def test_an_oversized_file_is_refused(self, client):
        workspace = await _workspace_of()
        response = await client.post(
            f"/v1/workspaces/{workspace}/documents/upload-url",
            headers=_auth(),
            json={"filename": "big.pdf", "mime_type": "application/pdf", "file_size": 999_999_999},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "file_too_large"

    async def test_a_valid_pdf_gets_a_url(self, client):
        workspace = await _workspace_of()
        response = await client.post(
            f"/v1/workspaces/{workspace}/documents/upload-url",
            headers=_auth(),
            json={"filename": "report.pdf", "mime_type": "application/pdf", "file_size": 2048},
        )
        # 503 is the unreachable-Supabase signature call; authorisation and
        # validation both passed, which is what this test is for.
        assert response.status_code in (200, 503), response.text


# ---------------------------------------------------------------------------
# Rule 10 — the audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    async def test_deleting_a_document_is_recorded(self, client, made_documents):
        workspace = await _workspace_of()
        document_id = await _make_document(workspace, title="To be deleted", created=made_documents)

        response = await client.delete(
            f"/v1/workspaces/{workspace}/documents/{document_id}", headers=_auth()
        )
        assert response.status_code == 204

        events = await client.get(
            f"/v1/workspaces/{workspace}/audit?action=document.delete", headers=_auth()
        )
        assert events.status_code == 200, events.text
        items = events.json()["items"]
        assert any(item["target_id"] == str(document_id) for item in items), items
        recorded = next(item for item in items if item["target_id"] == str(document_id))
        assert recorded["target_label"] == "To be deleted"
        assert recorded["actor_id"] == ALICE

    async def test_a_non_admin_cannot_read_the_trail(self, client):
        """Knowing a colleague's access was removed is admin-tier information.

        Carol is a `viewer` in Alice's workspace from the seed, so this is a real
        non-admin member — a 403, not a 404, because she knows the workspace exists.
        """
        workspace = await _workspace_of(ALICE)
        response = await client.get(f"/v1/workspaces/{workspace}/audit", headers=_auth(CAROL))
        assert response.status_code == 403

    async def test_a_non_member_cannot_read_the_trail(self, client):
        """404, not 403 — the workspace's existence is not confirmed to a stranger.

        Bob is not a member of Alice's workspace in the seed, so this needs no
        setup — and deliberately makes no cleanup either. An earlier version
        deleted his membership to "ensure" it, which silently depended on a state
        it had itself destroyed and broke an unrelated routing test downstream.
        """
        workspace = await _workspace_of(ALICE)
        pool = await _pool()
        try:
            member = await pool.fetchval(
                "select 1 from public.workspace_members where workspace_id=$1 and user_id=$2",
                workspace,
                BOB,
            )
            assert member is None, "this test assumes Bob is not a member; restore the seed"
        finally:
            await pool.close()

        response = await client.get(f"/v1/workspaces/{workspace}/audit", headers=_auth(BOB))
        assert response.status_code == 404


class TestAuditFunction:
    """`record_audit` is SECURITY DEFINER, so its own checks are load-bearing."""

    async def test_an_event_requires_an_authenticated_actor(self):
        workspace = await _workspace_of()
        pool = await _pool()
        try:
            async with pool.acquire() as conn, conn.transaction():
                # No JWT claim on this session at all.
                await conn.execute("select set_config('request.jwt.claim.sub', '', true)")
                with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                    await conn.fetchval(
                        "select public.record_audit("
                        "p_workspace_id => $1, p_action => 'test.event')",
                        workspace,
                    )
        finally:
            await pool.close()

    async def test_the_actor_cannot_be_forged(self):
        """The actor comes from the JWT claim, so a parameter cannot override it."""
        workspace = await _workspace_of(ALICE)
        pool = await _pool()
        try:
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute("select set_config('role', $1, true)", "authenticated")
                await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", ALICE)
                event_id = await conn.fetchval(
                    """select public.record_audit(
                         p_workspace_id => $1, p_action => 'test.actor',
                         p_detail => '{"actor_id": "forged"}'::jsonb)""",
                    workspace,
                )
                actor = await conn.fetchval(
                    "select actor_id from public.audit_events where id=$1", event_id
                )
                assert str(actor) == ALICE
        finally:
            await pool.close()

    async def test_a_non_member_cannot_record_an_event(self):
        """A compromised route cannot plant evidence in another tenant."""
        workspace = await _workspace_of(ALICE)
        pool = await _pool()
        try:
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute("select set_config('role', $1, true)", "authenticated")
                await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", BOB)
                with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                    await conn.fetchval(
                        "select public.record_audit("
                        "p_workspace_id => $1, p_action => 'test.forged')",
                        workspace,
                    )
        finally:
            await pool.close()

    async def test_detail_is_stored_as_a_json_object_not_a_string(self):
        """A double-encoded jsonb is a silent, type-changing bug.

        asyncpg encodes a `dict` as JSON; handing it `json.dumps(dict)` stores a
        JSON *string*, so `detail` arrives as `"{}"` for every consumer that
        expects an object. Caught here because no other test reads it back.
        """
        workspace = await _workspace_of(ALICE)
        pool = await _pool()
        try:
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute("select set_config('role', $1, true)", "authenticated")
                await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", ALICE)
                event_id = await conn.fetchval(
                    """select public.record_audit(
                         p_workspace_id => $1, p_action => 'test.detail_shape',
                         p_detail => '{"role": "member", "count": 3}'::jsonb)""",
                    workspace,
                )
                detail = await conn.fetchval(
                    "select detail from public.audit_events where id=$1", event_id
                )
                # asyncpg decodes jsonb to a str unless a codec is installed, so
                # the assertion is on the JSON *text*: an object starts with '{'
                # and its keys are unquoted, a double-encoded string starts with '"'.
                assert detail.strip().startswith("{"), detail
                assert '"role":"member"' in detail.replace(" ", ""), detail
        finally:
            await pool.close()

    async def test_the_api_returns_detail_as_an_object(self, client, made_documents):
        """The wire shape must be an object, so the UI can read `detail.role`."""
        workspace = await _workspace_of(ALICE)
        # Deleting a document writes a detail-free event; upload writes fields.
        document_id = await _make_document(workspace, title="Detail shape", created=made_documents)
        await client.delete(f"/v1/workspaces/{workspace}/documents/{document_id}", headers=_auth())
        response = await client.get(
            f"/v1/workspaces/{workspace}/audit?action=document.delete", headers=_auth()
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert items, "the delete should have been recorded"
        assert isinstance(items[0]["detail"], dict), items[0]["detail"]

    async def test_a_viewer_may_be_recorded_about(self):
        """A viewer acting on their own behalf is still a member.

        The check is membership, not role: a viewer can leave a workspace, and
        that departure is an auditable event.
        """
        workspace = await _workspace_of(ALICE)
        pool = await _pool()
        try:
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute("select set_config('role', $1, true)", "authenticated")
                await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", CAROL)
                event_id = await conn.fetchval(
                    "select public.record_audit(p_workspace_id => $1, p_action => 'test.viewer')",
                    workspace,
                )
                assert event_id is not None
        finally:
            await pool.close()

    async def test_events_are_append_only_for_users(self):
        """No update/delete policy exists, so a user cannot rewrite history."""
        workspace = await _workspace_of(ALICE)
        pool = await _pool()
        try:
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute("select set_config('role', $1, true)", "authenticated")
                await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", ALICE)
                event_id = await conn.fetchval(
                    "select public.record_audit("
                    "p_workspace_id => $1, p_action => 'test.append_only')",
                    workspace,
                )
                touched = await conn.execute(
                    "update public.audit_events set action='rewritten' where id=$1", event_id
                )
                # No update policy: zero rows affected, and the row is unchanged.
                assert stroke_is_zero(touched), touched
                action = await conn.fetchval(
                    "select action from public.audit_events where id=$1", event_id
                )
                assert action == "test.append_only"
        finally:
            await pool.close()


def stroke_is_zero(command_tag: str) -> bool:
    """asyncpg returns e.g. 'UPDATE 0'."""
    return command_tag.strip().upper().endswith(" 0")


# ---------------------------------------------------------------------------
# Rule 4 — retrieval must not cross tenants
# ---------------------------------------------------------------------------


class TestRetrievalIsolation:
    """Rule 4 has two independent mechanisms, and both are exercised here.

    RLS is not enough by itself: `hybrid_search` is SECURITY INVOKER and takes
    `p_workspace_id` as an argument, so the *function* is the tenant boundary for
    retrieval. A test that only checked RLS would not notice if the API ever
    called it with a client-supplied workspace id.
    """

    async def test_a_query_never_returns_another_workspaces_chunk(self, client):
        alice_ws = await _workspace_of(ALICE)
        bob_ws = await _workspace_of(BOB)

        # A distinctive phrase that exists only in Alice's workspace.
        phrase = "zebra-quartz-andromeda-eleven"
        pool = await _pool()
        try:
            await pool.execute(
                """insert into public.document_chunks
                     (workspace_id, document_id, chunk_index, content, embedding_model)
                   values ($1,
                           (select id from public.documents
                             where workspace_id=$1 and deleted_at is null limit 1),
                           9001, $2, 'text-embedding-3-small')
                   on conflict do nothing""",
                alice_ws,
                f"The secret phrase is {phrase} and it belongs to Alice.",
            )
        finally:
            await pool.close()

        # Bob searches his own workspace for Alice's phrase.
        response = await client.post(
            f"/v1/workspaces/{bob_ws}/search",
            headers=_auth(BOB),
            json={"query": phrase, "limit": 10},
        )
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert all(
            phrase not in item["snippet"] for item in results
        ), "retrieval crossed the tenant boundary"

    async def test_bob_cannot_retrieve_through_alices_workspace_id(self, client):
        """Even with Alice's workspace id in the path, membership is checked first."""
        alice_ws = await _workspace_of(ALICE)
        response = await client.post(
            f"/v1/workspaces/{alice_ws}/search",
            headers=_auth(BOB),
            json={"query": "anything at all", "limit": 5},
        )
        assert response.status_code == 404

    async def test_the_sql_function_itself_is_scoped(self):
        """The lowest-level guarantee: the function cannot widen the tenant.

        Called directly with Alice's workspace id on a session that can read
        everything (superuser here), the function still returns only her rows —
        proving the scope is the argument, not the caller's privilege.
        """
        alice_ws = await _workspace_of(ALICE)
        bob_ws = await _workspace_of(BOB)
        phrase = "zebra-quartz-andromeda-eleven"

        pool = await _pool()
        try:
            alice_hits = await pool.fetchval(
                """select count(*) from public.hybrid_search(
                     p_workspace_id => $1, p_query => $2)""",
                alice_ws,
                phrase,
            )
            bob_hits = await pool.fetchval(
                """select count(*) from public.hybrid_search(
                     p_workspace_id => $1, p_query => $2)""",
                bob_ws,
                phrase,
            )
        finally:
            await pool.close()

        assert alice_hits >= 1, "the phrase should be found in its own workspace"
        assert bob_hits == 0, "the same phrase must be invisible from another workspace"

    async def test_every_returned_chunk_belongs_to_the_requested_workspace(self):
        """The exhaustive version: no row may carry a foreign workspace_id."""
        alice_ws = await _workspace_of(ALICE)
        pool = await _pool()
        try:
            foreign = await pool.fetchval(
                """select count(*)
                   from public.hybrid_search(p_workspace_id => $1, p_query => 'the') h
                   join public.document_chunks c on c.id = h.chunk_id
                   where c.workspace_id <> $1""",
                alice_ws,
            )
        finally:
            await pool.close()
        assert foreign == 0

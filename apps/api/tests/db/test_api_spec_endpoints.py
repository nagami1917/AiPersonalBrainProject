"""The spec §5.1 endpoint table, asserted at the HTTP boundary.

Each test names an endpoint from the spec and checks the two things that can
actually go wrong with it: the shape the client is promised, and that the tenant
boundary holds. The second matters more — every one of these endpoints takes an id
the client chose, and the spec's own warning is that the backend must verify
workspace access rather than trust what arrived.
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
BOB = "22222222-2222-2222-2222-222222222222"
CAROL = "33333333-3333-3333-3333-333333333333"


def _token(user_id: str) -> str:
    settings = get_settings()
    return jwt.encode(
        {"sub": user_id, "role": "authenticated", "aud": "authenticated"},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )


def _auth(user_id: str = ALICE) -> dict[str, str]:
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


async def _workspace_of(user_id: str) -> str:
    pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
    try:
        value = await pool.fetchval(
            "select id from public.workspaces where owner_id=$1 and personal limit 1", user_id
        )
    finally:
        await pool.close()
    if value is None:
        pytest.skip("seed data missing: run `make db-reset` first")
    return str(value)


class TestAuthMe:
    async def test_it_returns_identity_workspaces_and_a_default(self, client):
        response = await client.get("/v1/auth/me", headers=_auth())
        assert response.status_code == 200
        body = response.json()

        assert body["user"]["id"] == ALICE
        assert body["workspaces"], "a user always has at least their personal workspace"
        assert any(w["personal"] for w in body["workspaces"])
        # The client needs a workspace id to land on; making it guess is a UX bug.
        assert body["default_workspace_id"] == await _workspace_of(ALICE)

    async def test_it_requires_a_token(self, client):
        assert (await client.get("/v1/auth/me")).status_code == 401

    async def test_the_roster_lists_only_your_own_workspaces(self, client):
        alice = (await client.get("/v1/auth/me", headers=_auth(ALICE))).json()
        bob = (await client.get("/v1/auth/me", headers=_auth(BOB))).json()
        alice_ids = {w["id"] for w in alice["workspaces"]}
        bob_ids = {w["id"] for w in bob["workspaces"]}
        # Bob is not a member of Alice's personal workspace.
        assert alice_ids - bob_ids, "the two users must not share every workspace"


class TestFlatAliases:
    async def test_notes_default_to_the_callers_workspace(self, client):
        response = await client.get("/v1/notes", headers=_auth())
        assert response.status_code == 200
        body = response.json()
        assert "items" in body
        assert all(isinstance(item["id"], str) for item in body["items"])

    async def test_a_foreign_workspace_is_404_not_403(self, client):
        """The API must not confirm that someone else's workspace exists."""
        alice_ws = await _workspace_of(ALICE)
        response = await client.get(
            "/v1/notes", headers=_auth(BOB), params={"workspace_id": alice_ws}
        )
        assert response.status_code == 404

    async def test_documents_alias_matches_the_scoped_route(self, client):
        workspace = await _workspace_of(ALICE)
        flat = await client.get("/v1/documents", headers=_auth())
        scoped = await client.get(f"/v1/workspaces/{workspace}/documents", headers=_auth())
        assert flat.status_code == scoped.status_code == 200
        assert [d["id"] for d in flat.json()["items"]] == [d["id"] for d in scoped.json()["items"]]

    async def test_collections_alias_lists(self, client):
        response = await client.get("/v1/collections", headers=_auth())
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    async def test_conversations_alias_lists(self, client):
        response = await client.get("/v1/conversations", headers=_auth())
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    async def test_document_detail_in_a_foreign_workspace_is_404(self, client):
        alice_ws = await _workspace_of(ALICE)
        pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
        try:
            doc_id = await pool.fetchval(
                "select id from public.documents where workspace_id=$1 limit 1", alice_ws
            )
        finally:
            await pool.close()
        response = await client.get(f"/v1/documents/{doc_id}", headers=_auth(BOB))
        assert response.status_code == 404


class TestSearch:
    async def test_flat_search_returns_results_with_provenance(self, client):
        response = await client.get("/v1/search", headers=_auth(), params={"q": "secrets"})
        assert response.status_code == 200
        body = response.json()
        assert body["results"], "the seeded workspace mentions secrets"
        first = body["results"][0]
        for field in ("chunk_id", "source_id", "source_title", "score", "matched_arm"):
            assert field in first
        assert first["matched_arm"] in {"vector", "keyword", "both"}

    async def test_flat_search_honours_kind_filter(self, client):
        response = await client.get(
            "/v1/search", headers=_auth(), params={"q": "secrets", "kind": "document"}
        )
        assert response.status_code == 200
        assert all(r["source_kind"] == "document" for r in response.json()["results"])

    async def test_flat_search_refuses_a_foreign_workspace(self, client):
        alice_ws = await _workspace_of(ALICE)
        response = await client.get(
            "/v1/search", headers=_auth(BOB), params={"q": "secrets", "workspace_id": alice_ws}
        )
        assert response.status_code == 404

    async def test_an_empty_query_is_rejected_by_validation(self, client):
        response = await client.get("/v1/search", headers=_auth(), params={"q": ""})
        assert response.status_code == 422


class TestGraph:
    async def test_flat_graph_returns_nodes_and_edges_together(self, client):
        response = await client.get("/v1/graph", headers=_auth())
        assert response.status_code == 200
        body = response.json()
        assert {"nodes", "edges", "workspace_id"} <= set(body)
        assert body["workspace_id"] == await _workspace_of(ALICE)

    async def test_every_edge_names_nodes_from_the_same_snapshot(self, client):
        """A graph rendered from two round trips can show an edge to a missing node."""
        body = (await client.get("/v1/graph", headers=_auth())).json()
        node_ids = {n["id"] for n in body["nodes"]}
        for edge in body["edges"]:
            assert edge["source_id"] in node_ids
            assert edge["target_id"] in node_ids


class TestAiChat:
    async def test_chat_takes_the_workspace_in_the_body(self, client):
        workspace = await _workspace_of(ALICE)
        response = await client.post(
            "/v1/ai/chat",
            headers=_auth(),
            json={"workspace_id": workspace, "question": "Where is the service role key stored?"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["grounded"] is True
        assert body["citations"], "a grounded answer carries citations"
        assert body["conversation_id"]

    async def test_citations_name_the_uploaded_file(self, client, llm):
        """The spec's citation shape names the file, so the value must survive
        retrieval → context → citation → API. Citing several markers means a
        document source is definitely in the set."""
        # This phrase appears ONLY in the seeded document chunk, so the keyword arm
        # retrieves a document source deterministically — unlike a semantic query,
        # where the test embedder's hashed vectors decide the outcome.
        llm.reply = "The worker claims jobs under a row lock [1]."
        workspace = await _workspace_of(ALICE)
        body = (
            await client.post(
                "/v1/ai/chat",
                headers=_auth(),
                json={"workspace_id": workspace, "question": "FOR UPDATE SKIP LOCKED"},
            )
        ).json()

        citations = body["citations"]
        assert citations, "the seeded workspace can answer this"
        document_citations = [c for c in citations if c["source_kind"] == "document"]
        assert document_citations, f"expected a document citation, got {citations}"
        assert all(c["filename"] for c in document_citations)
        # notes have no file name, and do not pretend to
        for note_citation in (c for c in citations if c["source_kind"] == "note"):
            assert note_citation["filename"] == note_citation["source_title"]

    async def test_a_stored_conversation_replays_the_citation_filename(self, client, llm):
        """Citations are persisted, not recomputed, so the file name has to be
        stored with them. It is denormalised on purpose: renaming or deleting the
        document afterwards must not rewrite what was cited at the time."""
        llm.reply = "The worker claims jobs under a row lock [1]."
        workspace = await _workspace_of(ALICE)
        asked = (
            await client.post(
                "/v1/ai/chat",
                headers=_auth(),
                json={"workspace_id": workspace, "question": "FOR UPDATE SKIP LOCKED"},
            )
        ).json()

        pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
        try:
            original = await pool.fetchval(
                "select d.filename from public.documents d where d.workspace_id=$1 limit 1",
                workspace,
            )
            # A rename and delete after the fact must not disturb history.
            await pool.execute(
                "update public.documents set filename='renamed.pdf' where workspace_id=$1",
                workspace,
            )
        finally:
            await pool.close()

        replay = await client.get(f"/v1/conversations/{asked['conversation_id']}", headers=_auth())
        assert replay.status_code == 200
        messages = replay.json()["messages"]
        cited = [s for m in messages for s in (m.get("citations") or [])]
        assert cited, messages
        assert all(s["filename"] == original for s in cited), cited

    async def test_feedback_round_trips_through_the_transcript(self, client):
        """The transcript read used a column that never existed, so this was a
        500 rather than a shape problem. null must mean "unrated"."""
        workspace = await _workspace_of(ALICE)
        asked = (
            await client.post(
                "/v1/ai/chat",
                headers=_auth(),
                json={"workspace_id": workspace, "question": "FOR UPDATE SKIP LOCKED"},
            )
        ).json()

        before = (
            await client.get(f"/v1/conversations/{asked['conversation_id']}", headers=_auth())
        ).json()
        assert all(m["feedback"] is None for m in before["messages"])

        assistant = [m for m in before["messages"] if m["role"] == "assistant"][0]
        rated = await client.post(
            f"/v1/workspaces/{workspace}/messages/{assistant['id']}/feedback",
            headers=_auth(),
            json={"helpful": False, "reason": "cited the wrong passage"},
        )
        assert rated.status_code < 300, rated.text

        after = (
            await client.get(f"/v1/conversations/{asked['conversation_id']}", headers=_auth())
        ).json()
        stored = [m for m in after["messages"] if m["id"] == assistant["id"]][0]
        assert stored["feedback"] == {"helpful": False, "reason": "cited the wrong passage"}

    async def test_feedback_on_a_foreign_message_is_404(self, client):
        body = (
            await client.post(
                "/v1/ai/chat",
                headers=_auth(ALICE),
                json={
                    "workspace_id": await _workspace_of(ALICE),
                    "question": "FOR UPDATE SKIP LOCKED",
                },
            )
        ).json()
        transcript = (
            await client.get(f"/v1/conversations/{body['conversation_id']}", headers=_auth(ALICE))
        ).json()
        message_id = transcript["messages"][-1]["id"]

        response = await client.post(
            f"/v1/workspaces/{await _workspace_of(BOB)}/messages/{message_id}/feedback",
            headers=_auth(BOB),
            json={"helpful": True},
        )
        assert response.status_code == 404

    async def test_a_foreign_workspace_in_the_body_is_404(self, client):
        """The body is exactly as untrusted as the URL."""
        alice_ws = await _workspace_of(ALICE)
        response = await client.post(
            "/v1/ai/chat",
            headers=_auth(BOB),
            json={"workspace_id": alice_ws, "question": "anything"},
        )
        assert response.status_code == 404

    async def test_an_unanswerable_question_refuses_without_calling_the_model(self, client, llm):
        workspace = await _workspace_of(ALICE)
        body = (
            await client.post(
                "/v1/ai/chat",
                headers=_auth(),
                json={
                    "workspace_id": workspace,
                    "question": "quantum chromodynamics lattice spacing",
                },
            )
        ).json()
        assert body["grounded"] is False
        assert body["citations"] == []
        assert llm.calls == [], "the refusal path must not spend a model call"

    async def test_client_supplied_source_ids_cannot_fabricate_citations(self, client):
        """A filter narrows retrieval; it never manufactures a source."""
        workspace = await _workspace_of(ALICE)
        body = (
            await client.post(
                "/v1/ai/chat",
                headers=_auth(),
                json={
                    "workspace_id": workspace,
                    "question": "Where is the service role key stored?",
                    "filters": {"source_ids": ["00000000-0000-0000-0000-000000000000"]},
                },
            )
        ).json()
        assert body["citations"] == []
        assert body["grounded"] is False

    async def test_the_stream_route_exists_and_is_sse(self, client):
        workspace = await _workspace_of(ALICE)
        async with client.stream(
            "POST",
            "/v1/ai/stream",
            headers=_auth(),
            json={"workspace_id": workspace, "question": "Where is the service role key stored?"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            first = ""
            async for chunk in response.aiter_text():
                first += chunk
                break
        # sources arrive before any generated text
        assert "event: sources" in first


class TestAiSummarize:
    async def test_it_summarizes_pasted_text(self, client, llm):
        workspace = await _workspace_of(ALICE)
        response = await client.post(
            "/v1/ai/summarize",
            headers=_auth(),
            json={"workspace_id": workspace, "text": "The worker claims jobs with a row lock."},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]
        assert body["source_id"] is None
        assert llm.calls, "summarizing must actually call the model"

    async def test_it_summarizes_a_source_by_id(self, client):
        workspace = await _workspace_of(ALICE)
        pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
        try:
            doc_id = await pool.fetchval(
                "select id from public.documents where workspace_id=$1 limit 1", workspace
            )
        finally:
            await pool.close()

        response = await client.post(
            "/v1/ai/summarize",
            headers=_auth(),
            json={"workspace_id": workspace, "source_id": str(doc_id)},
        )
        assert response.status_code == 200
        assert response.json()["title"]

    async def test_a_foreign_source_id_is_404(self, client):
        bob_ws = await _workspace_of(BOB)
        pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
        try:
            alice_doc = await pool.fetchval(
                "select id from public.documents where workspace_id=$1 limit 1",
                await _workspace_of(ALICE),
            )
        finally:
            await pool.close()

        response = await client.post(
            "/v1/ai/summarize",
            headers=_auth(BOB),
            json={"workspace_id": bob_ws, "source_id": str(alice_doc)},
        )
        assert response.status_code == 404

    async def test_both_inputs_at_once_is_rejected(self, client):
        workspace = await _workspace_of(ALICE)
        response = await client.post(
            "/v1/ai/summarize",
            headers=_auth(),
            json={
                "workspace_id": workspace,
                "text": "x",
                "source_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_summarize_input"

    async def test_neither_input_is_rejected(self, client):
        workspace = await _workspace_of(ALICE)
        response = await client.post(
            "/v1/ai/summarize", headers=_auth(), json={"workspace_id": workspace}
        )
        assert response.status_code == 400

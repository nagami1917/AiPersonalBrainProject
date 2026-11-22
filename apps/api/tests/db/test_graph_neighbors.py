"""The graph neighbourhood endpoint.

This endpoint is the fix for a page that was calling a route that did not exist.
The tests assert the two things that page needs: the seed is always in the
result, and depth actually bounds the walk (a recursive CTE that ignores depth
is an infinite query waiting for a cycle).
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


async def _workspace_of(user_id: str = ALICE) -> str:
    pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
    try:
        value = await pool.fetchval(
            "select id from public.workspaces where owner_id=$1 and personal limit 1", user_id
        )
    finally:
        await pool.close()
    if value is None:
        pytest.skip("seed data missing")
    return str(value)


async def _seed_chain(workspace_id: str, length: int = 4) -> list[str]:
    """A -> B -> C -> …, so depth has something to stop at."""
    pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
    ids: list[str] = []
    try:
        for index in range(length):
            ids.append(
                str(
                    await pool.fetchval(
                        """insert into public.knowledge_entities
                             (workspace_id, name, normalized_name, entity_type, mention_count)
                           values ($1, $2, $3, 'concept', 1)
                           on conflict (workspace_id, normalized_name)
                           do update set mention_count = public.knowledge_entities.mention_count + 1
                           returning id""",
                        workspace_id,
                        f"Chain node {index}",
                        f"chain node {index}",
                    )
                )
            )
        for left, right in zip(ids, ids[1:], strict=False):
            await pool.execute(
                """insert into public.knowledge_links
                     (workspace_id, source_id, target_id, relation, weight)
                   values ($1,$2,$3,'chain',1)
                   on conflict do nothing""",
                workspace_id,
                left,
                right,
            )
    finally:
        await pool.close()
    return ids


class TestNeighbors:
    async def test_the_seed_is_always_present_and_first(self, client):
        workspace = await _workspace_of()
        ids = await _seed_chain(workspace, 3)

        response = await client.get(
            f"/v1/workspaces/{workspace}/graph/entities/{ids[0]}/neighbors", headers=_auth()
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["seed_id"] == ids[0]
        assert body["nodes"][0]["id"] == ids[0], "the seed must be nodes[0] for layout"
        assert {n["id"] for n in body["nodes"]} >= {ids[0], ids[1]}

    async def test_depth_one_excludes_the_two_hop_node(self, client):
        workspace = await _workspace_of()
        ids = await _seed_chain(workspace, 4)

        one = (
            await client.get(
                f"/v1/workspaces/{workspace}/graph/entities/{ids[0]}/neighbors",
                headers=_auth(),
                params={"depth": 1},
            )
        ).json()
        assert ids[1] in {n["id"] for n in one["nodes"]}
        assert ids[2] not in {n["id"] for n in one["nodes"]}, "depth=1 must stop at one hop"

        two = (
            await client.get(
                f"/v1/workspaces/{workspace}/graph/entities/{ids[0]}/neighbors",
                headers=_auth(),
                params={"depth": 2},
            )
        ).json()
        assert ids[2] in {n["id"] for n in two["nodes"]}, "depth=2 reaches the second hop"

    async def test_depth_is_capped(self, client):
        workspace = await _workspace_of()
        ids = await _seed_chain(workspace, 2)
        response = await client.get(
            f"/v1/workspaces/{workspace}/graph/entities/{ids[0]}/neighbors",
            headers=_auth(),
            params={"depth": 5},
        )
        assert response.status_code == 422, "depth beyond 2 must be rejected, not clamped silently"

    async def test_every_edge_connects_two_drawn_nodes(self, client):
        workspace = await _workspace_of()
        ids = await _seed_chain(workspace, 3)
        body = (
            await client.get(
                f"/v1/workspaces/{workspace}/graph/entities/{ids[0]}/neighbors",
                headers=_auth(),
                params={"depth": 2},
            )
        ).json()
        node_ids = {n["id"] for n in body["nodes"]}
        for edge in body["edges"]:
            assert edge["source"] in node_ids, "an edge to a missing node would render as a lie"
            assert edge["target"] in node_ids

    async def test_a_foreign_entity_is_404(self, client):
        alice_ws = await _workspace_of(ALICE)
        ids = await _seed_chain(alice_ws, 2)
        response = await client.get(
            f"/v1/workspaces/{await _workspace_of(BOB)}/graph/entities/{ids[0]}/neighbors",
            headers=_auth(BOB),
        )
        assert response.status_code == 404

    async def test_an_unknown_entity_is_404(self, client):
        workspace = await _workspace_of()
        response = await client.get(
            f"/v1/workspaces/{workspace}/graph/entities/"
            "00000000-0000-0000-0000-000000000000/neighbors",
            headers=_auth(),
        )
        assert response.status_code == 404

    async def test_a_lone_entity_returns_itself_and_no_edges(self, client):
        workspace = await _workspace_of()
        pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
        try:
            lone = str(
                await pool.fetchval(
                    """insert into public.knowledge_entities
                         (workspace_id, name, normalized_name, entity_type, mention_count)
                       values ($1, 'Lone entity', 'lone entity', 'concept', 0)
                       on conflict (workspace_id, normalized_name)
                       do update set name = 'Lone entity'
                       returning id""",
                    workspace,
                )
            )
        finally:
            await pool.close()

        body = (
            await client.get(
                f"/v1/workspaces/{workspace}/graph/entities/{lone}/neighbors", headers=_auth()
            )
        ).json()
        assert [n["id"] for n in body["nodes"]] == [lone]
        assert body["edges"] == []

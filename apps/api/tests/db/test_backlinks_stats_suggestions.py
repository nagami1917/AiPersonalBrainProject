"""Backlinks, workspace stats and suggested connections, at the HTTP boundary.

The link logic is the part worth testing hardest: it is the one place where a
title typed into a note body becomes a navigable edge, and getting it wrong is
invisible until someone clicks.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from app.core.config import get_settings
from app.core.db import close_pool
from app.core.deps import get_embedder, get_llm
from app.main import create_app
from app.repositories.knowledge import extract_wiki_links
from httpx import ASGITransport, AsyncClient
from jose import jwt

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def _unique(prefix: str) -> str:
    """Titles are the link contract, so tests must not collide on them.

    The database persists across tests in a session (and across runs, when the
    validation cluster is reused), so a fixed title like "Link target" would let
    a previous run's note satisfy this run's link.
    """
    return f"{prefix} {uuid.uuid4().hex[:8]}"


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


class TestExtraction:
    """The parser is pure, so these need no database and no HTTP."""

    def test_plain_links_are_found(self):
        # The author's capitalisation is preserved: it is what the graph shows.
        assert extract_wiki_links("see [[Q3 Budget]]") == ["Q3 Budget"]

    def test_repeated_links_collapse_to_the_first_spelling(self):
        found = extract_wiki_links("[[Q3 Budget]] and again [[q3  budget]]")
        assert found == ["Q3 Budget"], "dedupe on the normalised form, keep the first spelling"

    def test_alias_keeps_the_title_not_the_label(self):
        assert extract_wiki_links("[[Real Title|what you see]]") == ["Real Title"]

    def test_body_without_links_is_empty(self):
        assert extract_wiki_links("nothing here") == []

    def test_none_is_safe(self):
        assert extract_wiki_links(None) == []

    def test_empty_brackets_are_not_a_link(self):
        assert extract_wiki_links("[[]] [[]]") == []


class TestStats:
    async def test_it_counts_only_this_workspace(self, client):
        alice = await _workspace_of(ALICE)
        bob = await _workspace_of(BOB)

        a = (await client.get(f"/v1/workspaces/{alice}/stats", headers=_auth(ALICE))).json()
        b = (await client.get(f"/v1/workspaces/{bob}/stats", headers=_auth(BOB))).json()

        assert a["workspace_id"] == alice
        assert b["workspace_id"] == bob
        # The seeded workspaces differ, so identical counts would mean the filter
        # is not being applied.
        assert (a["notes"], a["documents"]) != (b["notes"], b["documents"])

    async def test_a_foreign_workspace_is_404(self, client):
        alice = await _workspace_of(ALICE)
        response = await client.get(f"/v1/workspaces/{alice}/stats", headers=_auth(BOB))
        assert response.status_code == 404

    async def test_counts_move_when_a_note_is_added(self, client):
        workspace = await _workspace_of(ALICE)
        before = (await client.get(f"/v1/workspaces/{workspace}/stats", headers=_auth())).json()

        created = await client.post(
            f"/v1/workspaces/{workspace}/notes",
            headers=_auth(),
            json={"title": "Stats probe note", "content": "A note, to move a counter."},
        )
        assert created.status_code in (201, 202), created.text

        after = (await client.get(f"/v1/workspaces/{workspace}/stats", headers=_auth())).json()
        assert after["notes"] == before["notes"] + 1


class TestBacklinks:
    async def test_a_note_created_links_to_a_note_created_later(self, client):
        """The interesting direction: [[link]] written before its target exists.

        This is the case a naive implementation gets wrong — the link is stored
        as dangling and only resolves when the target is created.
        """
        workspace = await _workspace_of(ALICE)
        target_title = _unique("Link target")

        first = (
            await client.post(
                f"/v1/workspaces/{workspace}/notes",
                headers=_auth(),
                json={"title": _unique("Link source"), "content": f"Points at [[{target_title}]]."},
            )
        ).json()["note"]

        # Before the target exists, the link is visible but unresolved.
        detail = (
            await client.get(f"/v1/workspaces/{workspace}/notes/{first['id']}", headers=_auth())
        ).json()
        outgoing = detail["outgoing_links"]
        assert [o["target_title"] for o in outgoing] == [target_title]
        assert outgoing[0]["resolved"] is False
        assert outgoing[0]["target_id"] is None

        second = (
            await client.post(
                f"/v1/workspaces/{workspace}/notes",
                headers=_auth(),
                json={"title": target_title, "content": "Now it exists."},
            )
        ).json()["note"]

        # The target now sees the backlink, and the source's link resolved.
        target = (
            await client.get(f"/v1/workspaces/{workspace}/notes/{second['id']}", headers=_auth())
        ).json()
        assert [b["id"] for b in target["backlinks"]] == [first["id"]]

        source = (
            await client.get(f"/v1/workspaces/{workspace}/notes/{first['id']}", headers=_auth())
        ).json()
        assert source["outgoing_links"][0]["resolved"] is True
        assert source["outgoing_links"][0]["target_id"] == second["id"]

    async def test_editing_the_body_rewrites_the_links(self, client):
        workspace = await _workspace_of(ALICE)
        note = (
            await client.post(
                f"/v1/workspaces/{workspace}/notes",
                headers=_auth(),
                json={"title": _unique("Rewrites links"), "content": "Old [[gone]]"},
            )
        ).json()["note"]

        await client.patch(
            f"/v1/workspaces/{workspace}/notes/{note['id']}",
            headers=_auth(),
            json={"content": "New [[kept]]"},
        )

        detail = (
            await client.get(f"/v1/workspaces/{workspace}/notes/{note['id']}", headers=_auth())
        ).json()
        titles = {o["target_title"].lower() for o in detail["outgoing_links"]}
        assert titles == {"kept"}, "a removed link must not linger"

    async def test_links_cannot_cross_workspaces(self, client):
        """A [[link]] in Alice's workspace must never resolve to Bob's note."""
        alice = await _workspace_of(ALICE)
        bob = await _workspace_of(BOB)
        bob_title = _unique("Bob private")

        bob_note = (
            await client.post(
                f"/v1/workspaces/{bob}/notes",
                headers=_auth(BOB),
                json={"title": bob_title, "content": "Bob's own note."},
            )
        ).json()["note"]

        alice_note = (
            await client.post(
                f"/v1/workspaces/{alice}/notes",
                headers=_auth(ALICE),
                json={"title": _unique("Alice tries"), "content": f"See [[{bob_title}]]."},
            )
        ).json()["note"]

        detail = (
            await client.get(
                f"/v1/workspaces/{alice}/notes/{alice_note['id']}", headers=_auth(ALICE)
            )
        ).json()
        assert detail["outgoing_links"][0]["resolved"] is False
        assert detail["outgoing_links"][0]["target_id"] is None

        # And the reverse direction must not leak either.
        bob_detail = (
            await client.get(f"/v1/workspaces/{bob}/notes/{bob_note['id']}", headers=_auth(BOB))
        ).json()
        assert bob_detail["backlinks"] == []

    async def test_a_self_link_is_not_a_backlink(self, client):
        workspace = await _workspace_of(ALICE)
        self_title = _unique("Self referential")
        note = (
            await client.post(
                f"/v1/workspaces/{workspace}/notes",
                headers=_auth(),
                json={"title": self_title, "content": f"Me: [[{self_title}]]"},
            )
        ).json()["note"]

        detail = (
            await client.get(f"/v1/workspaces/{workspace}/notes/{note['id']}", headers=_auth())
        ).json()
        assert detail["backlinks"] == [], "a note is not its own backlink"


class TestSuggestions:
    async def test_it_suggests_unlinked_pairs_and_stops_after_linking(self, client):
        workspace = await _workspace_of(ALICE)
        # A per-run tag keeps this pair out of unrelated suggestions.
        tag = {"tags": [f"probe-{uuid.uuid4().hex[:8]}"]}
        tag_name = tag["tags"][0]
        # Titles sort A < B, and the pair is ordered by title, so the expectation
        # of which is source/target is deterministic rather than UUID-dependent.
        first_title = _unique("Suggestion A")
        second_title = _unique("Suggestion B")

        first_note = (
            await client.post(
                f"/v1/workspaces/{workspace}/notes",
                headers=_auth(),
                json={"title": first_title, "content": "Content A.", **tag},
            )
        ).json()["note"]
        await client.post(
            f"/v1/workspaces/{workspace}/notes",
            headers=_auth(),
            json={"title": second_title, "content": "Content B.", **tag},
        )

        # Queried through the anchor so older accumulated pairs cannot crowd this
        # one out of a global top-N.
        found = (
            await client.get(
                f"/v1/workspaces/{workspace}/suggestions",
                headers=_auth(),
                params={"note_id": first_note["id"], "limit": 20},
            )
        ).json()
        probe = next(
            (
                s
                for s in found
                if {s["source"]["title"], s["target"]["title"]} == {first_title, second_title}
            ),
            None,
        )
        assert probe is not None, [f"{s['source']['title']}|{s['target']['title']}" for s in found]
        assert tag_name in probe["shared_tags"]
        assert probe["reason"]
        assert probe["source"]["title"] == first_title, "pairs are ordered by title"

        # Linking them must retire the suggestion: it is no longer unlinked.
        await client.patch(
            f"/v1/workspaces/{workspace}/notes/{probe['source']['id']}",
            headers=_auth(),
            json={"content": f"Content A, now linking [[{second_title}]]."},
        )
        after = (
            await client.get(
                f"/v1/workspaces/{workspace}/suggestions",
                headers=_auth(),
                params={"note_id": probe["source"]["id"], "limit": 20},
            )
        ).json()
        assert not any(
            {s["source"]["title"], s["target"]["title"]} == {first_title, second_title}
            for s in after
        ), "linking the pair must retire the suggestion"

    async def test_anchoring_on_one_note_only_returns_its_pairs(self, client):
        workspace = await _workspace_of(ALICE)
        anchor_tag = f"anchor-{uuid.uuid4().hex[:8]}"
        anchor = (
            await client.post(
                f"/v1/workspaces/{workspace}/notes",
                headers=_auth(),
                json={"title": _unique("Anchor note"), "content": "x", "tags": [anchor_tag]},
            )
        ).json()["note"]
        neighbour = (
            await client.post(
                f"/v1/workspaces/{workspace}/notes",
                headers=_auth(),
                json={"title": _unique("Neighbour note"), "content": "y", "tags": [anchor_tag]},
            )
        ).json()["note"]

        found = (
            await client.get(
                f"/v1/workspaces/{workspace}/suggestions",
                headers=_auth(),
                params={"note_id": anchor["id"]},
            )
        ).json()
        assert found, "the pair shares a fresh tag, so it must be suggested"
        assert all(anchor["id"] in (s["source"]["id"], s["target"]["id"]) for s in found)
        titles = {frozenset((s["source"]["title"], s["target"]["title"])) for s in found}
        assert frozenset((anchor["title"], neighbour["title"])) in titles

    async def test_suggestions_never_cross_workspaces(self, client):
        bob = await _workspace_of(BOB)
        found = (await client.get(f"/v1/workspaces/{bob}/suggestions", headers=_auth(BOB))).json()
        alice_ws = await _workspace_of(ALICE)
        pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
        try:
            alice_notes = {
                str(r["id"])
                for r in await pool.fetch(
                    "select id from public.notes where workspace_id=$1", alice_ws
                )
            }
        finally:
            await pool.close()
        for item in found:
            assert item["source"]["id"] not in alice_notes
            assert item["target"]["id"] not in alice_notes

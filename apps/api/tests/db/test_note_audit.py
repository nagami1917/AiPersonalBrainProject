"""Notes must be audited, not just documents.

The audit trail covered documents and workspace membership but not the things a
person actually writes. `create_note`, `update_note` and `delete_note` all wrote
no audit row, so "Note created" and "Note updated" from the security
architecture were simply absent — the trail could show that a PDF arrived but
not that someone rewrote the note describing it.

That is the more damaging half of the gap. A document's bytes are immutable once
uploaded; a note's body is editable, so an unanswered "who changed this?" is
worse there than anywhere else.

These go through the real routes, because the bug was a route that forgot to
call `audit.record` — a unit test on `record` itself would have passed
throughout.
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


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Match the app pool so `detail` decodes as a dict, not a JSON string."""
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
async def workspace(pool):
    ws = await pool.fetchval(
        "select id from public.workspaces where owner_id = $1 order by personal desc limit 1",
        ALICE,
    )
    assert ws is not None, "seed data missing"
    return str(ws)


async def _watermark(pool: asyncpg.Pool) -> int:
    """Highest audit id now, so a leftover row from an earlier run cannot pass."""
    return await pool.fetchval("select coalesce(max(id), 0) from public.audit_events")


async def _events_since(pool: asyncpg.Pool, mark: int, action: str) -> list[asyncpg.Record]:
    return await pool.fetch(
        "select action, actor_id::text as actor, target_id::text as target, "
        "target_label, detail from public.audit_events "
        "where action = $1 and id > $2 order by id",
        action,
        mark,
    )


async def _cleanup(pool: asyncpg.Pool, note_id: str) -> None:
    """Hard-delete a test note (the routes only soft-delete)."""
    await pool.execute("delete from public.document_chunks where note_id = $1", note_id)
    await pool.execute("delete from public.note_links where source_note_id = $1", note_id)
    await pool.execute("delete from public.note_tags where note_id = $1", note_id)
    await pool.execute("delete from public.notes where id = $1", note_id)


async def _create(client, workspace, pool, title: str = "Audited note") -> str:
    response = await client.post(
        f"/api/v1/workspaces/{workspace}/notes",
        json={"title": title, "content": "Body with a [[Wiki Link]] inside."},
        headers=_auth(),
    )
    # 202: the note is accepted and queued for indexing, not yet searchable.
    assert response.status_code == 202, response.text
    return response.json()["note"]["id"]


# ---------------------------------------------------------------------------
# The three operations decision #3 names explicitly
# ---------------------------------------------------------------------------


async def test_creating_a_note_is_recorded(client, workspace, pool):
    mark = await _watermark(pool)
    note_id = await _create(client, workspace, pool, "Created and recorded")
    try:
        rows = await _events_since(pool, mark, "note.create")
        assert rows, "create_note wrote no audit row"
        row = rows[0]
        assert row["target"] == note_id
        assert row["target_label"] == "Created and recorded"
        assert row["actor"] == ALICE, "the actor must be the caller, not a parameter"
    finally:
        await _cleanup(pool, note_id)


async def test_updating_a_note_is_recorded_with_what_changed(client, workspace, pool):
    note_id = await _create(client, workspace, pool, "Before rename")
    try:
        mark = await _watermark(pool)
        response = await client.patch(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}",
            json={"title": "After rename"},
            headers=_auth(),
        )
        assert response.status_code == 200, response.text

        rows = await _events_since(pool, mark, "note.update")
        assert rows, "update_note wrote no audit row"
        detail = rows[0]["detail"]
        # jsonb comes back as a dict through the pool's codec.
        assert detail["title_before"] == "Before rename", detail
        assert detail["title_after"] == "After rename", detail
        assert "title" in detail["fields_changed"], detail
    finally:
        await _cleanup(pool, note_id)


async def test_updating_a_note_records_only_the_fields_that_moved(client, workspace, pool):
    """`fields_changed` must be the patch, not every column.

    The failure this guards against is an audit row that says "content, title,
    tags" for a request that only sent a title — which makes the trail actively
    misleading rather than merely incomplete.
    """
    note_id = await _create(client, workspace, pool, "Fields test")
    try:
        mark = await _watermark(pool)
        await client.patch(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}",
            json={"content": "New body only."},
            headers=_auth(),
        )
        rows = await _events_since(pool, mark, "note.update")
        assert rows
        changed = rows[0]["detail"]["fields_changed"]
        assert "content" in changed
        assert "title" not in changed, f"title was not sent but was recorded as changed: {changed}"
    finally:
        await _cleanup(pool, note_id)


async def test_deleting_a_note_is_recorded(client, workspace, pool):
    note_id = await _create(client, workspace, pool, "Deleted and recorded")
    try:
        mark = await _watermark(pool)
        response = await client.delete(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth()
        )
        assert response.status_code == 204

        rows = await _events_since(pool, mark, "note.delete")
        assert rows, "delete_note wrote no audit row"
        # The label is a copy, which is the point: it must survive the eventual
        # hard purge of the row it describes.
        assert rows[0]["target_label"] == "Deleted and recorded"
        assert rows[0]["target"] == note_id
    finally:
        await _cleanup(pool, note_id)


async def test_the_audit_entry_survives_the_note_being_purged(client, workspace, pool):
    """An audit trail whose rows vanish with their subject is not a trail."""
    note_id = await _create(client, workspace, pool, "Will be purged")
    mark = await _watermark(pool)
    await client.delete(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())

    # Hard-delete, as the purge job eventually does.
    await _cleanup(pool, note_id)

    assert await pool.fetchval("select count(*) from public.notes where id = $1", note_id) == 0
    rows = await _events_since(pool, mark, "note.delete")
    assert rows, "the audit row disappeared with the note it described"
    assert rows[0]["target_label"] == "Will be purged"


async def test_the_trail_never_stores_the_note_body(client, workspace, pool):
    """Rule 7: audit what changed, not what was written.

    Checked end to end because the whitelist in `audit.scrub` is only protective
    if the *caller* never puts the body in a permitted field.
    """
    secret = "the-entire-private-body-must-not-be-copied"
    note_id = await _create(client, workspace, pool, "Body secrecy")
    try:
        mark = await _watermark(pool)
        await client.patch(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}",
            json={"content": secret},
            headers=_auth(),
        )
        rows = await pool.fetch(
            "select detail::text as d from public.audit_events where id > $1", mark
        )
        # Assert there IS a row to inspect, or "no body found" passes because
        # nothing was audited at all — which is how this test read before the
        # note routes audited anything.
        assert rows, "nothing was audited, so the no-body check proves nothing"
        for row in rows:
            assert secret not in row["d"], "the note body was written into the audit trail"
    finally:
        await _cleanup(pool, note_id)


async def test_a_viewer_creating_a_note_is_refused_and_records_nothing(client, workspace, pool):
    """A refused write must not leave an audit row claiming it happened."""
    mark = await _watermark(pool)
    response = await client.post(
        f"/api/v1/workspaces/{workspace}/notes",
        json={"title": "Should not exist", "content": "x"},
        headers=_auth(CAROL),
    )
    assert response.status_code == 403, response.text

    rows = await pool.fetch(
        "select id from public.audit_events where id > $1 and action = 'note.create'", mark
    )
    assert rows == [], "a refused write was still audited as a success"


# ---------------------------------------------------------------------------
# Knowledge relationships — "Knowledge relationship created"
#
# The interesting part is what must NOT be recorded. `replace_note_links`
# deletes and re-inserts the whole outgoing set on every save, so a naive
# implementation emits an identical event on every autosave. The trail would
# then be full of events describing no change, which is worse than empty: it
# trains a reader to ignore it.
# ---------------------------------------------------------------------------


async def test_a_new_wiki_link_is_recorded(client, workspace, pool):
    note_id = await _create(client, workspace, pool, "Linker")
    try:
        mark = await _watermark(pool)
        response = await client.patch(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}",
            json={"content": "Now referencing [[Second Note]] and [[Third Note]]."},
            headers=_auth(),
        )
        assert response.status_code == 200, response.text

        rows = await _events_since(pool, mark, "note.link.create")
        assert len(rows) == 1, f"expected one relationship event, got {len(rows)}"
        # The original body already contained [[Wiki Link]], so only the two new
        # targets count — 3 links present, 2 created.
        assert rows[0]["detail"]["link_count"] == 2, rows[0]["detail"]
    finally:
        await _cleanup(pool, note_id)


async def test_re_saving_unchanged_content_records_no_link_event(client, workspace, pool):
    """The autosave case: same links, no event."""
    note_id = await _create(client, workspace, pool, "Stable links")
    body = "Body referencing [[Alpha]] only."
    await client.patch(
        f"/api/v1/workspaces/{workspace}/notes/{note_id}",
        json={"content": body},
        headers=_auth(),
    )
    mark = await _watermark(pool)

    # Same links, different prose — the delta is still zero.
    await client.patch(
        f"/api/v1/workspaces/{workspace}/notes/{note_id}",
        json={"content": "Reworded, still referencing [[Alpha]] only."},
        headers=_auth(),
    )
    rows = await _events_since(pool, mark, "note.link.create")
    assert rows == [], f"an event was recorded for links that already existed: {rows}"
    await _cleanup(pool, note_id)


async def test_removing_a_link_records_no_create_event(client, workspace, pool):
    note_id = await _create(client, workspace, pool, "Link remover")
    try:
        await client.patch(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}",
            json={"content": "Has [[Gone]] in it."},
            headers=_auth(),
        )
        mark = await _watermark(pool)
        await client.patch(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}",
            json={"content": "The link is gone now."},
            headers=_auth(),
        )
        rows = await _events_since(pool, mark, "note.link.create")
        assert rows == [], "removing a link was recorded as creating one"
    finally:
        await _cleanup(pool, note_id)


async def test_link_events_are_case_insensitive(client, workspace, pool):
    """[[Alpha]] -> [[alpha]] is the same link, not a new one.

    Resolution is a case-insensitive title match everywhere else in the system,
    so a trail that treats case as a new relationship contradicts the UI.
    """
    note_id = await _create(client, workspace, pool, "Case links")
    try:
        await client.patch(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}",
            json={"content": "See [[Alpha]]."},
            headers=_auth(),
        )
        mark = await _watermark(pool)
        await client.patch(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}",
            json={"content": "See [[ALPHA]] now."},
            headers=_auth(),
        )
        rows = await _events_since(pool, mark, "note.link.create")
        assert rows == [], "a case-only change was recorded as a new relationship"
    finally:
        await _cleanup(pool, note_id)


async def test_creating_a_collection_is_recorded(client, workspace, pool):
    mark = await _watermark(pool)
    response = await client.post(
        f"/api/v1/workspaces/{workspace}/collections",
        json={"name": "Audited collection"},
        headers=_auth(),
    )
    assert response.status_code == 201, response.text
    collection_id = response.json()["id"]
    try:
        rows = await _events_since(pool, mark, "collection.create")
        assert rows, "create_collection wrote no audit row"
        assert rows[0]["target"] == collection_id
        assert rows[0]["target_label"] == "Audited collection"
    finally:
        await pool.execute("delete from public.collections where id = $1", collection_id)

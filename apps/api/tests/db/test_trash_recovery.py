"""Deletion and recovery, at the HTTP boundary.

The bug these tests exist for: `DELETE /notes/{id}` soft-deleted the row and
then enqueued a `purge` job with the default `run_after = now()`, so the worker
hard-deleted it seconds later. The `deleted_at` column was real and correct and
nothing could ever read it — "deletion and recovery" was recovery in name only.

Three properties are asserted here, and each is a different failure mode:

  * **A deleted source disappears from the normal views.** If it does not, trash
    is a fiction and every list has to filter for itself.
  * **It is restorable, and restoring makes it searchable again.** If a restore
    only clears `deleted_at`, the note returns visible but with no chunks — the
    most confusing possible outcome, because it looks fine and silently is not.
  * **The purge refuses to run early, and does run late.** The retention check
    lives in SQL against `deleted_at`, so it holds even when the job was queued
    under a different `trash_retention_days`.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from app.core.config import get_settings
from app.core.db import close_pool
from app.core.deps import get_embedder, get_llm
from app.main import create_app
from app.repositories import knowledge as repo
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


TRASH_COUNT_SQL = (
    "select count(*) from public.notes where workspace_id=$1 and deleted_at is not null"
)


def _pool() -> asyncpg.Pool:
    return asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)


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


async def _make_note(client, workspace: str, title: str = "Recoverable") -> str:
    response = await client.post(
        f"/api/v1/workspaces/{workspace}/notes",
        headers=_auth(),
        json={"title": f"{title} {uuid.uuid4().hex[:8]}", "content": "body text"},
    )
    assert response.status_code == 202, response.text
    return response.json()["note"]["id"]


async def _purge_now(note_id: str) -> None:
    """Remove a test note for real.

    `DELETE /notes/{id}` only soft-deletes, and the scheduled purge is 30 days
    out — so a test that used the API to tidy up left every note it ever created
    sitting in the trash. Against a reused database that grew without bound, and
    it made the trash-count assertions below meaningless because they were
    counting other tests' leftovers.

    The row is deleted directly, which is what the worker would do once the
    window elapsed. Chunks and note_links go with it by cascade.
    """
    pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=1)
    try:
        await pool.execute("delete from public.notes where id=$1", uuid.UUID(note_id))
    finally:
        await pool.close()


class TestNoteTrash:
    async def test_a_deleted_note_leaves_the_normal_listing(self, client):
        workspace = await _workspace_of()
        note_id = await _make_note(client, workspace)

        before = await client.get(f"/api/v1/workspaces/{workspace}/notes", headers=_auth())
        assert note_id in [n["id"] for n in before.json()["items"]]

        assert (
            await client.delete(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())
        ).status_code == 204

        after = await client.get(f"/api/v1/workspaces/{workspace}/notes", headers=_auth())
        assert note_id not in [n["id"] for n in after.json()["items"]]
        # And it is not readable directly either: "deleted" must mean gone from
        # every view, not just from the one list the UI happens to show.
        assert (
            await client.get(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())
        ).status_code == 404

        await _purge_now(note_id)

    async def test_a_deleted_note_appears_in_the_trash_with_an_expiry(self, client):
        workspace = await _workspace_of()
        note_id = await _make_note(client, workspace)
        await client.delete(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())

        response = await client.get(f"/api/v1/workspaces/{workspace}/notes/trash", headers=_auth())
        assert response.status_code == 200, response.text
        payload = response.json()
        items = [i for i in payload["items"] if i["id"] == note_id]
        assert items, payload
        item = items[0]
        assert item["kind"] == "note"
        # The UI shows "N days left", so the timestamp has to be present and in
        # the future — a retention window the response cannot express is not one
        # the user can be warned about.
        assert item["deleted_at"]
        assert item["expires_at"] > item["deleted_at"]
        assert payload["retention_days"] == get_settings().trash_retention_days

        await _purge_now(note_id)

    async def test_restore_brings_a_note_back(self, client):
        workspace = await _workspace_of()
        note_id = await _make_note(client, workspace, title="Restore me")
        await client.delete(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())

        restored = await client.post(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}/restore", headers=_auth()
        )
        assert restored.status_code == 200, restored.text
        body = restored.json()
        # 'pending', not 'ready': the chunks were deleted on trash, so claiming
        # 'ready' would advertise searchable content that does not exist yet.
        assert body["status"] == "pending"

        listed = await client.get(f"/api/v1/workspaces/{workspace}/notes", headers=_auth())
        assert note_id in [n["id"] for n in listed.json()["items"]]

        # Restoring re-queues ingestion, so it becomes citable again.
        pool = await _pool()
        try:
            job = await pool.fetchrow(
                "select type, status from public.processing_jobs "
                "where note_id=$1 and type='ingest_note' order by created_at desc limit 1",
                uuid.UUID(note_id),
            )
        finally:
            await pool.close()
        assert job is not None and job["type"] == "ingest_note"

        await _purge_now(note_id)

    async def test_restoring_a_live_note_is_not_found(self, client):
        # Idempotence would be wrong here: a 200 would tell a caller their
        # restore worked when nothing was in the trash.
        workspace = await _workspace_of()
        note_id = await _make_note(client, workspace)
        response = await client.post(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}/restore", headers=_auth()
        )
        assert response.status_code == 404
        await _purge_now(note_id)


class TestTrashTenancy:
    async def test_another_workspace_cannot_see_or_restore_the_trash(self, client):
        workspace = await _workspace_of()
        note_id = await _make_note(client, workspace, title="Alice private")
        await client.delete(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())

        bob_ws = await _workspace_of(BOB)
        # Bob's own trash: Alice's deleted note must not be in it.
        bob_trash = await client.get(f"/api/v1/workspaces/{bob_ws}/notes/trash", headers=_auth(BOB))
        assert bob_trash.status_code == 200
        assert note_id not in [i["id"] for i in bob_trash.json()["items"]]

        # Bob addressing Alice's workspace at all is a 404, as everywhere else.
        assert (
            await client.get(f"/api/v1/workspaces/{workspace}/notes/trash", headers=_auth(BOB))
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/workspaces/{workspace}/notes/{note_id}/restore", headers=_auth(BOB)
            )
        ).status_code == 404

        # Alice can still restore it, so Bob's attempts changed nothing.
        restored = await client.post(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}/restore", headers=_auth()
        )
        assert restored.status_code == 200
        await _purge_now(note_id)


class TestPurgeHonoursRetention:
    """The purge is the one operation that destroys data, so it is tested
    directly against the repository rather than only through a queued job."""

    async def test_purge_refuses_a_note_inside_its_window(self, client):
        workspace = await _workspace_of()
        note_id = await _make_note(client, workspace, title="Not yet")
        await client.delete(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())

        pool = await _pool()
        try:
            # 30 days: just deleted, so it must survive.
            purged = await repo.purge_note(pool, note_id, 30)
            assert purged is False, "a note deleted seconds ago must not be purged"
            still_there = await pool.fetchval(
                "select count(*) from public.notes where id=$1", uuid.UUID(note_id)
            )
            assert still_there == 1
        finally:
            await pool.close()

        await _purge_now(note_id)

    async def test_purge_removes_a_note_whose_window_has_passed(self, client):
        workspace = await _workspace_of()
        note_id = await _make_note(client, workspace, title="Due for purge")
        await client.delete(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())

        pool = await _pool()
        try:
            # Backdate the deletion rather than waiting 30 days. This is the same
            # condition the scheduler produces, reached directly.
            await pool.execute(
                "update public.notes set deleted_at = now() - interval '31 days' where id=$1",
                uuid.UUID(note_id),
            )
            purged = await repo.purge_note(pool, note_id, 30)
            assert purged is True
            gone = await pool.fetchval(
                "select count(*) from public.notes where id=$1", uuid.UUID(note_id)
            )
            assert gone == 0
        finally:
            await pool.close()

    async def test_purge_skips_a_live_row(self, client):
        workspace = await _workspace_of()
        note_id = await _make_note(client, workspace, title="Never deleted")

        pool = await _pool()
        try:
            # Not soft-deleted at all: purging must not touch it, however old the
            # retention threshold is set. "Age" only applies to deleted rows.
            assert await repo.purge_note(pool, note_id, 0) is False
            assert (
                await pool.fetchval(
                    "select count(*) from public.notes where id=$1", uuid.UUID(note_id)
                )
                == 1
            )
        finally:
            await pool.close()

        await _purge_now(note_id)

    async def test_delete_schedules_the_purge_in_the_future(self, client):
        """The regression test for the original bug.

        The route used to enqueue `purge` with the helper's default `run_after`,
        which is `now()`. Asserting on the scheduled time is the only way to
        catch a return to that behaviour, because everything else about the
        delete looks correct.
        """
        workspace = await _workspace_of()
        note_id = await _make_note(client, workspace, title="Scheduled")
        await client.delete(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())

        pool = await _pool()
        try:
            row = await pool.fetchrow(
                "select run_after, now() as at from public.processing_jobs "
                "where note_id=$1 and type='purge' order by created_at desc limit 1",
                uuid.UUID(note_id),
            )
        finally:
            await pool.close()
        assert row is not None, "deleting must queue a purge"
        days = (row["run_after"] - row["at"]).total_seconds() / 86400
        assert days > 29, f"purge scheduled only {days:.2f} days out; recovery would be impossible"

        await _purge_now(note_id)


class TestNoteSearch:
    """The notes-list filter (`GET /notes?q=`), distinct from semantic search.

    Semantic search embeds the query, costs an API call, and returns passages.
    This filter answers "which of my notes mention this word" from the local
    database — which is what a search box on a list page should do.
    """

    async def test_filters_by_title_and_body(self, client):
        workspace = await _workspace_of()
        marker = uuid.uuid4().hex[:10]
        in_title = await _make_note(client, workspace, title=f"Title {marker}")
        # Body-only hit: the substring must be found in content as well.
        body_note = (
            await client.post(
                f"/api/v1/workspaces/{workspace}/notes",
                headers=_auth(),
                json={"title": f"Untitled {uuid.uuid4().hex[:6]}", "content": f"mentions {marker}"},
            )
        ).json()["note"]["id"]
        unrelated = await _make_note(client, workspace, title="Nothing to see")

        response = await client.get(
            f"/api/v1/workspaces/{workspace}/notes", params={"q": marker}, headers=_auth()
        )
        assert response.status_code == 200, response.text
        ids = [n["id"] for n in response.json()["items"]]
        assert in_title in ids
        assert body_note in ids
        assert unrelated not in ids

        for note_id in (in_title, body_note, unrelated):
            await _purge_now(note_id)

    async def test_is_case_insensitive(self, client):
        workspace = await _workspace_of()
        marker = "MiXeDcase" + uuid.uuid4().hex[:6]
        note_id = await _make_note(client, workspace, title=marker)

        lower = await client.get(
            f"/api/v1/workspaces/{workspace}/notes",
            params={"q": marker.lower()},
            headers=_auth(),
        )
        assert note_id in [n["id"] for n in lower.json()["items"]]
        await _purge_now(note_id)

    async def test_wildcards_are_literal(self, client):
        """`%` must not behave as a wildcard.

        This is the property that separates `position()` from `ILIKE`, and it
        only shows up with a **bare** `%`: embedded in a longer string, both
        implementations agree. Measured directly against the seeded workspace, a
        bare `%` matches every note under ILIKE and nothing under `position()`.

        The test asserts "not everything", not "exactly zero": a note that
        genuinely contains a percent sign should still be findable, so the
        correct answer is "only the notes that actually contain %".
        """
        workspace = await _workspace_of()
        total = await client.get(f"/api/v1/workspaces/{workspace}/notes", headers=_auth())
        total_count = len(total.json()["items"])

        marker = uuid.uuid4().hex[:8]
        with_percent = await _make_note(client, workspace, title=f"Battery at 100% {marker}")

        response = await client.get(
            f"/api/v1/workspaces/{workspace}/notes", params={"q": "%"}, headers=_auth()
        )
        assert response.status_code == 200, response.text
        ids = {n["id"] for n in response.json()["items"]}
        assert with_percent in ids, "a note containing % must be found by a search for %"
        assert (
            len(ids) < total_count
        ), f"a bare % matched {len(ids)} of {total_count} notes — it is acting as a wildcard"

        await _purge_now(with_percent)

    async def test_search_does_not_cross_workspaces(self, client):
        workspace = await _workspace_of()
        marker = uuid.uuid4().hex[:10]
        note_id = await _make_note(client, workspace, title=f"Secret {marker}")

        bob_ws = await _workspace_of(BOB)
        response = await client.get(
            f"/api/v1/workspaces/{bob_ws}/notes", params={"q": marker}, headers=_auth(BOB)
        )
        assert response.status_code == 200
        assert note_id not in [n["id"] for n in response.json()["items"]]

        await _purge_now(note_id)

    async def test_deleted_notes_do_not_match(self, client):
        workspace = await _workspace_of()
        marker = uuid.uuid4().hex[:10]
        note_id = await _make_note(client, workspace, title=f"Gone {marker}")
        await client.delete(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())

        response = await client.get(
            f"/api/v1/workspaces/{workspace}/notes", params={"q": marker}, headers=_auth()
        )
        assert note_id not in [n["id"] for n in response.json()["items"]]

        await _purge_now(note_id)


class TestFlatAliasFilters:
    """The flat `/notes` alias must accept every filter the scoped route does.

    When `q` was added to the scoped route, the alias was not updated: it called
    the scoped function without `q`, so the `Query(...)` default object leaked
    into the SQL parameter list and the alias 500'd. It type-checks either way,
    which is exactly why this needs a request-level test.
    """

    async def test_the_alias_forwards_the_search_filter(self, client):
        workspace = await _workspace_of()
        marker = uuid.uuid4().hex[:10]
        note_id = await _make_note(client, workspace, title=f"Alias {marker}")

        response = await client.get("/api/v1/notes", params={"q": marker}, headers=_auth())
        assert response.status_code == 200, response.text
        assert note_id in [n["id"] for n in response.json()["items"]]

        await _purge_now(note_id)

    async def test_the_alias_forwards_the_tag_filter(self, client):
        workspace = await _workspace_of()
        marker = uuid.uuid4().hex[:10]
        note_id = await _make_note(client, workspace, title=f"Tagged {marker}")
        await client.patch(
            f"/api/v1/workspaces/{workspace}/notes/{note_id}",
            headers=_auth(),
            json={"tags": [f"tag-{marker}"]},
        )

        response = await client.get(
            "/api/v1/notes", params={"tag": f"tag-{marker}"}, headers=_auth()
        )
        assert response.status_code == 200, response.text
        ids = [n["id"] for n in response.json()["items"]]
        assert note_id in ids
        # A filter that returns every note would also satisfy the line above, so
        # assert the exclusion too.
        other = await _make_note(client, workspace, title=f"Untagged {uuid.uuid4().hex[:6]}")
        excluded = await client.get(
            "/api/v1/notes", params={"tag": f"tag-{marker}"}, headers=_auth()
        )
        assert other not in [n["id"] for n in excluded.json()["items"]]

        for cleanup in (note_id, other):
            await _purge_now(cleanup)


class TestTrashDoesNotAccumulate:
    """A test suite must not grow the trash without bound.

    The first version of these tests tidied up with `DELETE`, which only soft-
    deletes. Against a reused database every run left its notes behind — by the
    time this was noticed there were 106 rows in the trash, and the count-based
    assertions were comparing against other tests' debris. This asserts the
    invariant directly, so the leak cannot come back silently.
    """

    async def test_a_full_create_delete_purge_leaves_nothing_behind(self, client):
        workspace = await _workspace_of()

        pool = await _pool()
        try:
            before = await pool.fetchval(TRASH_COUNT_SQL, uuid.UUID(workspace))
        finally:
            await pool.close()

        note_id = await _make_note(client, workspace, title="Leak check")
        await client.delete(f"/api/v1/workspaces/{workspace}/notes/{note_id}", headers=_auth())
        await _purge_now(note_id)

        pool = await _pool()
        try:
            after = await pool.fetchval(TRASH_COUNT_SQL, uuid.UUID(workspace))
        finally:
            await pool.close()

        assert after == before, f"trash grew from {before} to {after} rows during one test"

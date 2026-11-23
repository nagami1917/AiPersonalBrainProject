"""Worker entry point: claim, dispatch, retry, recover.

One process handles all job types. Concurrency is bounded by
WORKER_CONCURRENCY so a large PDF cannot starve chat-adjacent work, and the
staleness sweep runs on an interval so a killed process never strands a
document in `processing`.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.ai.providers import build_embedder, build_llm
from app.core.config import get_settings
from app.core.db import close_pool, get_pool
from app.core.logging import configure_logging
from app.repositories import knowledge as repo
from app.workers import enrich, ingest
from app.workers.queue import (
    Job,
    claim_job,
    mark_failed,
    mark_succeeded,
    requeue_stale,
)

log = logging.getLogger("nexus.worker")
_stopping = asyncio.Event()


IN_PROGRESS_TYPES = ("ingest_note", "ingest_document", "reindex")


async def handle_job(job: Job, pool, embedder, llm, settings) -> None:  # type: ignore[no-untyped-def]
    async with pool.acquire() as conn:
        target = job.target
        if job.type in IN_PROGRESS_TYPES:
            if target is None:
                raise ValueError(f"{job.type} job without a note or document")
            kind, source_id = target
            await ingest.run_ingest(
                conn,
                job_id=job.id,
                kind=kind,
                source_id=source_id,
                embedder=embedder,
                settings=settings,
            )
        elif job.type == "summary":
            if target is not None:
                kind, source_id = target
                await enrich.run_summary(
                    conn, kind=kind, source_id=source_id, payload=job.payload, llm=llm
                )
        elif job.type == "entities":
            if target is not None:
                kind, source_id = target
                await enrich.run_entities(
                    conn, kind=kind, source_id=source_id, payload=job.payload, llm=llm
                )
        elif job.type == "purge":
            await purge_document(conn, job, settings)
        else:
            log.warning("unknown job type %s", job.type)


async def purge_document(conn, job: Job, settings) -> None:  # type: ignore[no-untyped-def]
    """Hard-delete a soft-deleted source, and its storage object if it has one.

    Only runs once the retention window has elapsed. The check is repeated in
    `repo.purge_*` (in SQL, against `deleted_at`) rather than trusted from the
    job's `run_after`: retention is configured per environment, so a job queued
    under one value could run under another, and the database is the only thing
    that knows when the deletion actually happened. An early job is a no-op that
    logs, not data loss.
    """
    target = job.target
    if target is None:
        return
    kind, source_id = target
    retention_days = settings.trash_retention_days

    file_path = await _claim_for_purge(conn, kind=kind, source_id=source_id, days=retention_days)
    if file_path is _NOT_DELETED:
        return

    # The row is gone by the time we get here; the object is best-effort. A
    # storage failure must not fail the job, because retrying would try to
    # delete an object whose row no longer exists — the job would retry five
    # times and then dead-letter, permanently. An orphaned object is cleaned up
    # by the bucket's lifecycle rules.
    if file_path:
        await _delete_storage_object(settings, file_path)


# Distinguishes "the row was deleted and it had no file" (None) from "nothing was
# deleted" — a plain Optional would collapse those two very different outcomes.
_NOT_DELETED = object()


async def _claim_for_purge(  # type: ignore[no-untyped-def]
    conn, *, kind: str, source_id: str, days: int
):
    """Delete the row if it is eligible; return its `file_path` if it was.

    Returns `_NOT_DELETED` when nothing was removed, so the caller can stay a
    straight line instead of a ladder of early returns.
    """
    if kind == "note":
        row = await conn.fetchrow("select deleted_at from public.notes where id=$1", source_id)
        if row is None or row["deleted_at"] is None:
            log.info("purge skipped: note %s is not soft-deleted", source_id)
            return _NOT_DELETED
        if not await repo.purge_note(conn, source_id, days):
            log.info(
                "purge deferred: note %s is still inside its %s-day retention window",
                source_id,
                days,
            )
            return _NOT_DELETED
        return None

    row = await conn.fetchrow(
        "select file_path, deleted_at from public.documents where id=$1", source_id
    )
    if row is None or row["deleted_at"] is None:
        log.info("purge skipped: document %s is not soft-deleted", source_id)
        return _NOT_DELETED
    if not await repo.purge_document(conn, source_id, days):
        log.info(
            "purge deferred: document %s is still inside its %s-day retention window",
            source_id,
            days,
        )
        return _NOT_DELETED
    return row["file_path"]


async def _delete_storage_object(settings, file_path: str) -> None:  # type: ignore[no-untyped-def]
    import httpx

    url = f"{settings.supabase_url}/storage/v1/object/{settings.storage_bucket}/{file_path}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            await client.delete(
                url,
                headers={
                    "Authorization": f"Bearer {settings.supabase_service_role_key}",
                    "apikey": settings.supabase_service_role_key,
                },
            )
    except Exception:
        log.exception("purge: could not delete storage object %s", file_path)


async def worker_loop(slot: int) -> None:
    settings = get_settings()
    pool = await get_pool(settings)
    embedder = build_embedder(settings)
    llm = build_llm(settings)
    log.info("worker slot %s started", slot)

    while not _stopping.is_set():
        try:
            async with pool.acquire() as conn:
                job = await claim_job(conn)
            if job is None:
                await asyncio.sleep(settings.worker_poll_interval_seconds)
                continue

            log.info("claimed job %s (%s)", job.id, job.type)
            try:
                await handle_job(job, pool, embedder, llm, settings)
                async with pool.acquire() as conn:
                    await mark_succeeded(conn, job.id)
            except Exception as exc:  # retry or dead-letter, never silent
                log.exception("job %s failed", job.id)
                async with pool.acquire() as conn:
                    await mark_failed(conn, job, str(exc))
                    target = job.target
                    if target is not None:
                        kind, source_id = target
                        table = "notes" if kind == "note" else "documents"
                        await conn.execute(
                            f"""update public.{table}
                                set status = 'failed', error_message = $2
                                where id = $1 and status <> 'ready'""",
                            source_id,
                            str(exc)[:500],
                        )
        except Exception:
            log.exception("worker loop error")
            await asyncio.sleep(5)


async def sweeper_loop() -> None:
    settings = get_settings()
    pool = await get_pool(settings)
    while not _stopping.is_set():
        try:
            async with pool.acquire() as conn:
                await requeue_stale(conn, settings.job_stale_after_seconds)
        except Exception:
            log.exception("sweeper error")
        await asyncio.sleep(60)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("worker starting (concurrency=%s)", settings.worker_concurrency)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _stopping.set)

    tasks = [asyncio.create_task(worker_loop(i)) for i in range(settings.worker_concurrency)]
    tasks.append(asyncio.create_task(sweeper_loop()))

    await _stopping.wait()
    log.info("worker draining")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())

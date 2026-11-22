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
        if job.job_type in IN_PROGRESS_TYPES:
            if target is None:
                raise ValueError(f"{job.job_type} job without a note or document")
            kind, source_id = target
            await ingest.run_ingest(
                conn,
                job_id=job.id,
                kind=kind,
                source_id=source_id,
                embedder=embedder,
                settings=settings,
            )
        elif job.job_type == "summary":
            if target is not None:
                kind, source_id = target
                await enrich.run_summary(
                    conn, kind=kind, source_id=source_id, payload=job.payload, llm=llm
                )
        elif job.job_type == "entities":
            if target is not None:
                kind, source_id = target
                await enrich.run_entities(
                    conn, kind=kind, source_id=source_id, payload=job.payload, llm=llm
                )
        elif job.job_type == "purge":
            await purge_document(conn, job, settings)
        else:
            log.warning("unknown job type %s", job.job_type)


async def purge_document(conn, job: Job, settings) -> None:  # type: ignore[no-untyped-def]
    """Hard-delete a soft-deleted source, and its storage object if it has one."""
    target = job.target
    if target is None:
        return
    kind, source_id = target

    if kind == "note":
        row = await conn.fetchrow("select deleted_at from public.notes where id=$1", source_id)
        if row is None or row["deleted_at"] is None:
            log.info("purge skipped: note %s is not soft-deleted", source_id)
            return
        await conn.execute("delete from public.notes where id=$1", source_id)
        return

    row = await conn.fetchrow(
        "select storage_path, deleted_at from public.documents where id=$1", source_id
    )
    if row is None:
        return
    if row["deleted_at"] is None:
        log.info("purge skipped: document %s is not soft-deleted", source_id)
        return

    if row["storage_path"]:
        import httpx

        url = (
            f"{settings.supabase_url}/storage/v1/object/"
            f"{settings.storage_bucket}/{row['storage_path']}"
        )
        async with httpx.AsyncClient(timeout=60) as client:
            await client.delete(
                url,
                headers={
                    "Authorization": f"Bearer {settings.supabase_service_role_key}",
                    "apikey": settings.supabase_service_role_key,
                },
            )

    await conn.execute("delete from public.documents where id=$1", source_id)


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

            log.info("claimed job %s (%s)", job.id, job.job_type)
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

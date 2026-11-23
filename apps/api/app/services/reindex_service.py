"""Reprocessing when the embedding model changes.

## The problem

Chunks are embedded once, with whichever model was configured at the time, and
`document_chunks.embedding_model` records it. Nothing compared that string to the
configured model. Changing `PINNED_EMBEDDING_MODEL` therefore left the corpus
mixed: old vectors stay in the index and keep being ranked against new query
vectors. Cosine distance across two embedding spaces is not a small error — it is
a number with no meaning, silently interleaved with numbers that have one.

The dimension guard in `core/schema_guard.py` catches the *incompatible* case (a
3072-dim model against a `vector(1536)` column) by refusing to boot. It cannot
catch the dangerous case: a model swap at the same width, which is exactly what
`text-embedding-3-small` at a different provider, or the same model at a new
version, produces.

## What this module does

  * `report()` — says what the corpus looks like, so the decision is informed
    (how many chunks, which models, how many sources affected).
  * `plan()` — says which sources need reprocessing, without writing anything.
  * `enqueue()` — queues the work, one job per source, using the existing
    `reindex` job type. No new job type, no new worker code path: a reindex is
    the operation that already exists and is already tested.

## Why the API endpoint defaults to a dry run

A reindex is expensive and it is not reversible in the sense a user expects —
the old vectors are gone once chunks are replaced. A caller must opt in with
`confirm=true`. The dry run is not ceremony: it answers "how much will this
cost" before the answer is paid for.

## Why stale sources are found by model string, not by a marker column

The string is already written by the worker on every ingest, it is already
indexed (0018), and it describes the truth: which model produced this vector.
A separate "needs reindex" boolean would be a cached answer to a question the
schema can already answer, and cache invalidation is the bug factory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import asyncpg

from app.core.config import Settings

log = logging.getLogger(__name__)


@dataclass
class ModelStat:
    model: str
    dim: int | None
    chunks: int
    is_current: bool


@dataclass
class ReindexPlan:
    """What a reindex would do. Returned by the dry run, and by the real run."""

    configured_model: str
    configured_dim: int
    models: list[ModelStat] = field(default_factory=list)
    stale_chunks: int = 0
    stale_sources: int = 0
    # Populated only when jobs were actually created.
    queued_jobs: int = 0
    job_ids: list[str] = field(default_factory=list)

    @property
    def needs_reindex(self) -> bool:
        return self.stale_sources > 0


async def report(conn: asyncpg.Connection, *, workspace_id: str, settings: Settings) -> ReindexPlan:
    """The corpus, by model, compared to the configured one.

    Reads the SQL aggregate from 0018 rather than counting in Python: the table
    holds one row per chunk and a workspace can have thousands.
    """
    rows = await conn.fetch(
        "select embedding_model, embedding_dim, chunk_count, is_current "
        "from public.embedding_model_stats($1)",
        workspace_id,
    )
    stale = await conn.fetchrow(
        "select stale_chunks, stale_sources from public.stale_chunk_counts($1, $2)",
        workspace_id,
        settings.pinned_embedding_model,
    )

    return ReindexPlan(
        configured_model=settings.pinned_embedding_model,
        configured_dim=settings.pinned_embedding_dim,
        models=[
            ModelStat(
                model=r["embedding_model"],
                dim=r["embedding_dim"],
                chunks=int(r["chunk_count"]),
                is_current=r["embedding_model"] == settings.pinned_embedding_model,
            )
            for r in rows
        ],
        stale_chunks=int(stale["stale_chunks"] or 0),
        stale_sources=int(stale["stale_sources"] or 0),
    )


async def _stale_source_rows(
    conn: asyncpg.Connection, *, workspace_id: str, model: str
) -> list[asyncpg.Record]:
    """One row per source (note or document) holding a stale vector.

    DISTINCT on the parent with the kind attached, because a source with 40 stale
    chunks is still one job. Ordered oldest-first so a partial run makes progress
    on the oldest material rather than re-touching the same recent sources.
    """
    return list(
        await conn.fetch(
            """
            select distinct on (c.note_id, c.document_id)
                   c.note_id, c.document_id, c.created_at
              from public.document_chunks c
             where c.workspace_id = $1
               and c.embedding_model is distinct from $2
               and c.embedding is not null
             order by c.note_id, c.document_id, c.created_at
            """,
            workspace_id,
            model,
        )
    )


async def enqueue(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    settings: Settings,
    limit: int | None = None,
) -> ReindexPlan:
    """Queue one `reindex` job per stale source. Idempotent per source.

    Idempotency: a source that already has a pending/running reindex is skipped,
    so calling this twice (or a user double-clicking, or a cron overlapping with a
    manual run) does not multiply the work. The claim query below is the dedupe —
    it is a database fact, not an in-process set.
    """
    plan = await report(conn, workspace_id=workspace_id, settings=settings)
    if not plan.needs_reindex:
        return plan

    rows = await _stale_source_rows(
        conn, workspace_id=workspace_id, model=settings.pinned_embedding_model
    )
    if limit is not None:
        rows = rows[:limit]

    for row in rows:
        is_note = row["note_id"] is not None
        source_id = row["note_id"] if is_note else row["document_id"]

        already = await conn.fetchval(
            """
            select 1 from public.processing_jobs
             where workspace_id = $1
               and type = 'reindex'
               and status in ('pending','queued','running')
               and coalesce(note_id, document_id) = $2
             limit 1
            """,
            workspace_id,
            source_id,
        )
        if already:
            continue

        # Inserted directly rather than via public.enqueue_job(): that helper
        # authorises with can_write_workspace()/auth.uid(), and the caller here
        # may be either a user request or the startup check. The route already
        # enforced membership before calling this, so the authorisation has
        # happened — it just does not live in this statement.
        job_id = await conn.fetchval(
            """
            insert into public.processing_jobs
              (workspace_id, type, note_id, document_id, payload, priority)
            values ($1, 'reindex', $2, $3, $4::jsonb, 50)
            returning id
            """,
            workspace_id,
            source_id if is_note else None,
            None if is_note else source_id,
            '{"reason": "embedding_model_changed"}',
        )
        plan.job_ids.append(str(job_id))
        plan.queued_jobs += 1

    log.info(
        "reindex queued workspace=%s model=%s jobs=%d stale_sources=%d",
        workspace_id,
        settings.pinned_embedding_model,
        plan.queued_jobs,
        plan.stale_sources,
    )
    return plan


async def needs_reindex_anywhere(conn: asyncpg.Connection, settings: Settings) -> int:
    """Count stale chunks across every workspace — for the startup warning.

    Deliberately not per-workspace here: this runs before any request, and its
    only job is to log "your corpus does not match your configuration" loudly
    enough that an operator sees it in the boot log rather than discovering it as
    a retrieval-quality complaint weeks later.
    """
    return int(
        await conn.fetchval(
            "select count(*) from public.document_chunks "
            "where embedding is not null and embedding_model is distinct from $1",
            settings.pinned_embedding_model,
        )
        or 0
    )

"""Postgres job queue (ADR-002).

At-least-once delivery via `FOR UPDATE SKIP LOCKED`, exponential backoff into
`run_after`, dead-lettering after `max_attempts`, and a staleness sweep so a
crash mid-job recovers instead of stranding a source in `processing` forever.

A job targets at most one of a note or a document; the CHECK in migration 0006
enforces that, so `Job` can expose a single `target` rather than a tuple of
optionals.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from typing import Any

import asyncpg

log = logging.getLogger(__name__)

CLAIM_SQL = """
update public.processing_jobs j
set status = 'running',
    locked_by = $1,
    locked_at = now(),
    attempts = j.attempts + 1
where j.id = (
  select id from public.processing_jobs
  where status = 'queued'
    and run_after <= now()
  order by priority, run_after
  for update skip locked
  limit 1
)
returning j.id, j.workspace_id, j.note_id, j.document_id, j.type,
          j.attempts, j.max_attempts, j.payload
"""

SWEEP_SQL = """
update public.processing_jobs
set status = 'queued', locked_by = null, locked_at = null
where status = 'running'
  and locked_at < now() - make_interval(secs => $1)
returning id
"""


@dataclass
class Job:
    id: str
    workspace_id: str
    note_id: str | None
    document_id: str | None
    type: str
    attempts: int
    max_attempts: int
    payload: dict[str, Any]

    @property
    def target(self) -> tuple[str, str] | None:
        """(kind, id) for the content this job operates on."""
        if self.note_id:
            return ("note", self.note_id)
        if self.document_id:
            return ("document", self.document_id)
        return None

    @classmethod
    def from_row(cls, row: Any) -> Job:
        record = dict(row)
        payload = record.get("payload") or {}
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        return cls(
            id=str(record["id"]),
            workspace_id=str(record["workspace_id"]),
            note_id=str(record["note_id"]) if record["note_id"] else None,
            document_id=str(record["document_id"]) if record["document_id"] else None,
            type=record["type"],
            attempts=record["attempts"],
            max_attempts=record["max_attempts"],
            payload=payload,
        )


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def claim_job(conn: asyncpg.Connection) -> Job | None:
    row = await conn.fetchrow(CLAIM_SQL, worker_id())
    return Job.from_row(row) if row else None


async def mark_succeeded(conn: asyncpg.Connection, job_id: str) -> None:
    await conn.execute(
        """update public.processing_jobs
           set status='succeeded', locked_by=null, locked_at=null, completed_at=now()
           where id=$1""",
        job_id,
    )


async def mark_failed(conn: asyncpg.Connection, job: Job, error: str) -> None:
    """Backoff or dead-letter. Never leaves a job silently stuck."""
    if job.attempts >= job.max_attempts:
        await conn.execute(
            """update public.processing_jobs
               set status='dead', locked_by=null, locked_at=null,
                   completed_at=now(), error_message=$2
               where id=$1""",
            job.id,
            error[:2000],
        )
        log.error("job %s dead after %s attempts: %s", job.id, job.attempts, error)
        return

    delay = min(60 * (2 ** (job.attempts - 1)), 3600)
    await conn.execute(
        """update public.processing_jobs
           set status='queued', locked_by=null, locked_at=null,
               error_message=$2, run_after=now() + make_interval(secs => $3)
           where id=$1""",
        job.id,
        error[:2000],
        float(delay),
    )
    log.warning("job %s requeued in %ss: %s", job.id, delay, error)


async def requeue_stale(conn: asyncpg.Connection, stale_after_seconds: int) -> int:
    rows = await conn.fetch(SWEEP_SQL, float(stale_after_seconds))
    if rows:
        log.warning("requeued %s stale job(s)", len(rows))
    return len(rows)


async def mark_superseded(conn: asyncpg.Connection, job_id: str) -> None:
    """A newer version of the source already produced results."""
    await conn.execute(
        "update public.processing_jobs set status='superseded', locked_by=null where id=$1",
        job_id,
    )

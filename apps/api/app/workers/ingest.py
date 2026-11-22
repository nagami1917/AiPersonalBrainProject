"""Ingestion: download → extract → chunk → embed → write.

Handles both parents. The only difference between a note and a document is where
the text comes from; everything downstream is shared, which is the point of
having one chunk table with a constrained owner.

Ordering rules that matter (invariants 3–5):
  * chunks are written, then status flips to 'ready' — in ONE transaction, so
    'ready' can never describe a source with no chunks.
  * writes are guarded by the source's `version`, so a stale worker that
    finishes after a re-index cannot overwrite fresher chunks; it is superseded.
  * re-running converges on the same chunk set for the same version.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import asyncpg

from app.ai.chunking import chunk_text
from app.ai.extract import ExtractionError, extract
from app.ai.providers import Embedder
from app.core.config import Settings

log = logging.getLogger(__name__)


class SupersededError(Exception):
    """The source changed while this job was running."""


@dataclass(frozen=True)
class IngestTarget:
    kind: str  # 'note' | 'document'
    id: str
    workspace_id: str
    version: int
    text: str
    page_map: list[tuple[int, int, int]] | None = None
    page_count: int | None = None


def _table(kind: str) -> str:
    return "notes" if kind == "note" else "documents"


async def _set_stage(conn: asyncpg.Connection, target: IngestTarget, stage: str) -> None:
    await conn.execute(
        f"""update public.{_table(target.kind)}
            set status='processing', processing_stage=$2
            where id=$1 and version=$3""",
        target.id,
        stage,
        target.version,
    )


async def _fail(conn: asyncpg.Connection, target: IngestTarget, message: str) -> None:
    await conn.execute(
        f"""update public.{_table(target.kind)}
            set status='failed', processing_stage=null, error_message=$2
            where id=$1 and version=$3""",
        target.id,
        message,
        target.version,
    )


async def _load_target(
    conn: asyncpg.Connection,
    *,
    kind: str,
    source_id: str,
    settings: Settings,
) -> IngestTarget | None:
    if kind == "note":
        row = await conn.fetchrow(
            """select id, workspace_id, title, body, version
               from public.notes where id=$1""",
            source_id,
        )
        if row is None:
            return None
        return IngestTarget(
            kind="note",
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            version=int(row["version"]),
            text=row["body"] or "",
        )

    row = await conn.fetchrow(
        """select id, workspace_id, title, mime_type, storage_path, raw_text, version
           from public.documents where id=$1""",
        source_id,
    )
    if row is None:
        return None

    text = row["raw_text"] or ""
    page_map: list[tuple[int, int, int]] | None = None
    page_count: int | None = None

    if not text and row["storage_path"]:
        data = await _download(row["storage_path"], settings)
        result = extract(data, row["mime_type"] or "", row["title"])
        text = result.text
        page_map = result.page_map or None
        page_count = result.page_count
        await conn.execute(
            """update public.documents
               set raw_text=$3, page_count=$4, metadata = metadata || $5::jsonb
               where id=$1 and version=$2""",
            source_id,
            int(row["version"]),
            text,
            page_count,
            {"extraction_method": result.method, "warnings": result.warnings},
        )

    return IngestTarget(
        kind="document",
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        version=int(row["version"]),
        text=text,
        page_map=page_map,
        page_count=page_count,
    )


async def run_ingest(
    conn: asyncpg.Connection,
    *,
    job_id: str,
    kind: str,
    source_id: str,
    embedder: Embedder,
    settings: Settings,
) -> None:
    target = await _load_target(conn, kind=kind, source_id=source_id, settings=settings)
    if target is None:
        log.warning("ingest: %s %s no longer exists", kind, source_id)
        return

    try:
        # --- 1. text -------------------------------------------------------
        await _set_stage(conn, target, "extracting")
        if not target.text.strip():
            await _fail(conn, target, "No text could be extracted from this source.")
            return

        # --- 2. chunk ------------------------------------------------------
        await _set_stage(conn, target, "chunking")
        chunks = chunk_text(target.text, target.page_map)
        if not chunks:
            await _fail(conn, target, "No text could be extracted from this source.")
            return

        # --- 3. embed ------------------------------------------------------
        await _set_stage(conn, target, "embedding")
        embeddings = await embedder.embed([c.content for c in chunks])

        # --- 4. write, version-guarded, then flip status --------------------
        parent_col = "note_id" if target.kind == "note" else "document_id"
        async with conn.transaction():
            current = await conn.fetchval(
                f"select version from public.{_table(target.kind)} where id=$1 for update",
                target.id,
            )
            if current != target.version:
                raise SupersededError(
                    f"{target.kind} moved from version {target.version} to {current} mid-job"
                )

            # Remove any other version's rows, then replace this version's rows.
            await conn.execute(
                f"delete from public.document_chunks where {parent_col}=$1", target.id
            )

            for chunk, embedding in zip(chunks, embeddings, strict=True):
                await conn.execute(
                    f"""insert into public.document_chunks
                        (workspace_id, {parent_col}, ordinal, content, token_count,
                         char_start, char_end, page_start, page_end, heading_path,
                         embedding, embedding_model, version)
                        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::vector,$12,$13)""",
                    target.workspace_id,
                    target.id,
                    chunk.ordinal,
                    chunk.content,
                    chunk.token_count,
                    chunk.char_start,
                    chunk.char_end,
                    chunk.page_start,
                    chunk.page_end,
                    chunk.heading_path,
                    _vector_literal(embedding),
                    embedder.model,
                    target.version,
                )

            await conn.execute(
                f"""update public.{_table(target.kind)}
                    set status='ready', processing_stage=null, error_message=null,
                        embedding_model=$2, page_count=coalesce($3, page_count)
                    where id=$1 and version=$4""",
                target.id,
                embedder.model,
                target.page_count,
                target.version,
            )

        # Follow-up jobs are best-effort and cannot block retrieval: the source is
        # already searchable at this point.
        await conn.execute(
            """select public.enqueue_job(
                 p_workspace_id => $1, p_job_type => 'summary',
                 p_note_id => $2, p_document_id => $3,
                 p_payload => $4::jsonb, p_priority => 150)""",
            target.workspace_id,
            target.id if target.kind == "note" else None,
            target.id if target.kind == "document" else None,
            {"version": target.version},
        )
        await conn.execute(
            """select public.enqueue_job(
                 p_workspace_id => $1, p_job_type => 'entities',
                 p_note_id => $2, p_document_id => $3,
                 p_payload => $4::jsonb, p_priority => 200)""",
            target.workspace_id,
            target.id if target.kind == "note" else None,
            target.id if target.kind == "document" else None,
            {"version": target.version},
        )
        log.info(
            "ingest complete %s=%s chunks=%s version=%s",
            target.kind,
            target.id,
            len(chunks),
            target.version,
        )

    except SupersededError as exc:
        log.info("ingest superseded %s=%s: %s", target.kind, target.id, exc)
        await conn.execute(
            "update public.processing_jobs set status='superseded', locked_by=null where id=$1",
            job_id,
        )
    except ExtractionError as exc:
        await _fail(conn, target, exc.message)
        raise
    except Exception:
        await conn.execute(
            f"""update public.{_table(target.kind)}
                set processing_stage=null,
                    error_message=coalesce(error_message,'Processing failed')
                where id=$1""",
            target.id,
        )
        raise


async def _download(storage_path: str, settings: Settings) -> bytes:
    """Fetch the original bytes with the service role.

    Object storage has no FK, so the worker is the only thing that transfers
    bytes; the API never proxies file content.
    """
    import httpx

    url = f"{settings.supabase_url}/storage/v1/object/{settings.storage_bucket}/{storage_path}"
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
                "apikey": settings.supabase_service_role_key,
            },
        )
        if response.status_code == 404:
            raise ExtractionError(
                "The uploaded file could not be found in storage.", code="storage_missing"
            )
        response.raise_for_status()
        return response.content


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

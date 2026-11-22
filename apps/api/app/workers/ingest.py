"""Document ingestion: download → extract → chunk → embed → write.

Ordering rules that matter (invariants 3–5):
  * chunks are written, then status flips to 'ready' — in ONE transaction, so
    'ready' can never describe a document with no chunks.
  * writes are guarded by documents.version, so a stale worker that finishes
    after a re-index cannot overwrite fresher chunks; it is marked superseded.
  * re-running converges on the same chunk set for the same version.
"""

from __future__ import annotations

import hashlib
import logging

import asyncpg

from app.ai.chunking import chunk_text
from app.ai.extract import ExtractionError, extract
from app.ai.providers import Embedder
from app.core.config import Settings

log = logging.getLogger(__name__)


class SupersededError(Exception):
    """The document changed while this job was running."""


async def _set_stage(conn: asyncpg.Connection, document_id: str, stage: str) -> None:
    await conn.execute(
        "update public.documents set status='processing', processing_stage=$2 where id=$1",
        document_id,
        stage,
    )


async def _fail(conn: asyncpg.Connection, document_id: str, message: str) -> None:
    await conn.execute(
        """update public.documents
           set status='failed', processing_stage=null, error_message=$2
           where id=$1""",
        document_id,
        message,
    )


async def run_ingest(
    conn: asyncpg.Connection,
    *,
    job_id: str,
    user_id: str,
    document_id: str,
    embedder: Embedder,
    settings: Settings,
) -> None:
    document = await conn.fetchrow(
        """select id, user_id, title, mime_type, storage_path, source_type, raw_text,
                  version, status
           from public.documents where id = $1""",
        document_id,
    )
    if document is None:
        log.warning("ingest: document %s no longer exists", document_id)
        return

    version = int(document["version"])

    try:
        # --- 1. obtain text ------------------------------------------------
        await _set_stage(conn, document_id, "extracting")
        if document["source_type"] == "note" or not document["storage_path"]:
            text = document["raw_text"] or ""
            page_map: list[tuple[int, int, int]] | None = None
            page_count = None
        else:
            data = await _download(document["storage_path"], settings)
            result = extract(data, document["mime_type"] or "", document["title"])
            text = result.text
            page_map = result.page_map or None
            page_count = result.page_count
            await conn.execute(
                """update public.documents
                   set raw_text=$3, page_count=$4,
                       metadata = metadata || $5::jsonb
                   where id=$1 and version=$2""",
                document_id,
                version,
                text,
                page_count,
                {"extraction_method": result.method, "warnings": result.warnings},
            )

        # --- 2. chunk ------------------------------------------------------
        await _set_stage(conn, document_id, "chunking")
        chunks = chunk_text(text, page_map)
        if not chunks:
            await _fail(conn, document_id, "No text could be extracted from this document.")
            return

        # --- 3. embed ------------------------------------------------------
        await _set_stage(conn, document_id, "embedding")
        embeddings = await embedder.embed([c.content for c in chunks])

        # --- 4. write, version-guarded, then flip status --------------------
        async with conn.transaction():
            current = await conn.fetchval(
                "select version from public.documents where id=$1 for update", document_id
            )
            if current != version:
                raise SupersededError(f"document moved from version {version} to {current} mid-job")

            await conn.execute(
                "delete from public.chunks where document_id=$1 and version <> $2",
                document_id,
                version,
            )
            await conn.execute(
                "delete from public.chunks where document_id=$1 and version=$2",
                document_id,
                version,
            )

            for chunk, embedding in zip(chunks, embeddings, strict=True):
                await conn.execute(
                    """insert into public.chunks
                       (user_id, document_id, ordinal, content, token_count, char_start, char_end,
                        page_start, page_end, heading_path, embedding, embedding_model, version)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::vector,$12,$13)""",
                    user_id,
                    document_id,
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
                    version,
                )

            await conn.execute(
                """update public.documents
                   set status='ready', processing_stage=null, error_message=null,
                       embedding_model=$2, page_count=coalesce($3, page_count)
                   where id=$1 and version=$4""",
                document_id,
                embedder.model,
                page_count,
                version,
            )

        await conn.execute(
            "select public.enqueue_job($1,'summary',$2,$3::jsonb,$4)",
            user_id,
            document_id,
            {"version": version},
            150,
        )
        await conn.execute(
            "select public.enqueue_job($1,'entities',$2,$3::jsonb,$4)",
            user_id,
            document_id,
            {"version": version},
            200,
        )
        log.info("ingest complete doc=%s chunks=%s version=%s", document_id, len(chunks), version)

    except SupersededError as exc:
        log.info("ingest superseded doc=%s: %s", document_id, exc)
        await conn.execute(
            "update public.jobs set status='superseded', locked_by=null where id=$1", job_id
        )
    except ExtractionError as exc:
        await _fail(conn, document_id, exc.message)
        raise
    except Exception:
        await conn.execute(
            """update public.documents
               set processing_stage=null,
                   error_message=coalesce(error_message,'Processing failed')
               where id=$1""",
            document_id,
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

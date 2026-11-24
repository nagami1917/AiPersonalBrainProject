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

from app.ai.ingestion import (
    EmptySourceError,
    ExtractionError,
    extract,
    prepare_chunks,
)
from app.ai.providers import Embedder
from app.core.config import Settings
from app.core.supabase_headers import storage_admin_headers
from app.core.upload_validation import UploadValidationError, validate_content

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
    # completed_at exists on documents only: it answers "how long did this file
    # take, and how did it end?" for both outcomes, so a failure stamps it too.
    # Notes have no such column, hence the per-kind assignment list.
    assignments = "status='failed', processing_stage=null, error_message=$2"
    if target.kind == "document":
        assignments += ", completed_at=now()"
    await conn.execute(
        f"""update public.{_table(target.kind)}
            set {assignments}
            where id=$1 and version=$3""",
        target.id,
        message,
        target.version,
    )


async def _fail_by_id(conn: asyncpg.Connection, *, kind: str, source_id: str, message: str) -> None:
    """Mark a source failed when its version could not be loaded.

    The version-guarded `_fail` needs an `IngestTarget`; this is the same write
    for the case where building that target is what failed. It does not filter
    on version because there is none to compare against — a load failure cannot
    have written chunks, so there is nothing stale to protect.

    `status` is only downgraded from a non-final state: a document that is
    already `ready` (a reindex of a good file failing, say) keeps its chunks and
    its citable status, and the error is recorded alongside. Losing a working
    document because a later reindex hit a transient error would be the worse
    failure.
    """
    assignments = (
        "status = case when status = 'ready' then 'ready' else 'failed' end, "
        "processing_stage = null, error_message = $2"
    )
    if kind == "document":
        assignments += ", completed_at = now()"
    await conn.execute(
        f"update public.{_table(kind)} set {assignments} where id=$1",
        source_id,
        message,
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
            """select id, workspace_id, title, content, version
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
            text=row["content"] or "",
        )

    row = await conn.fetchrow(
        """select id, workspace_id, title, mime_type, file_path, raw_text, version
           from public.documents where id=$1""",
        source_id,
    )
    if row is None:
        return None

    text = row["raw_text"] or ""
    page_map: list[tuple[int, int, int]] | None = None
    page_count: int | None = None

    # file_path is the storage key (schema column). raw_text short-circuits the
    # download for re-indexing and for text pasted directly.
    if not text and row["file_path"]:
        # Rule 5: the stored path must sit inside this document's workspace.
        # The worker uses the service role, which bypasses storage policies — so
        # this check is the only thing standing between a corrupted row and a
        # cross-tenant fetch.
        stored_path = str(row["file_path"])
        if not stored_path.startswith(f"{row['workspace_id']}/"):
            raise ExtractionError(
                "This document's file is stored outside its workspace.",
                code="storage_path_escape",
            )

        data = await _download(stored_path, settings)

        # Rule 9, second half: the bytes exist here for the first time, so this
        # is where content validation actually happens. Truncated to the sniff
        # window — every signature we check lives in the first few KB.
        try:
            validate_content(
                data[: settings.sniff_bytes],
                mime_type=row["mime_type"] or "",
                filename=stored_path.rsplit("/", 1)[-1],
            )
        except UploadValidationError as exc:
            raise ExtractionError(exc.message, code=exc.code) from exc

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
    try:
        # `_load_target` is inside the try on purpose. It downloads the file,
        # validates its content and extracts the text — every one of which can
        # fail — and when it was outside, a failed download or a storage path
        # escaping its workspace left the document at `status='pending'` with a
        # null error message forever. The worker's outer handler marks the *job*
        # failed, but it cannot reach the row: `_load_target` never returned the
        # document id. The status is what the UI shows, so this was a silent
        # spinner rather than a visible error.
        target = await _load_target(conn, kind=kind, source_id=source_id, settings=settings)
    except ExtractionError as exc:
        await _fail_by_id(conn, kind=kind, source_id=source_id, message=exc.message)
        raise
    except Exception as exc:
        await _fail_by_id(conn, kind=kind, source_id=source_id, message=str(exc)[:500])
        raise

    if target is None:
        log.warning("ingest: %s %s no longer exists", kind, source_id)
        return

    try:
        # --- 1. extract ----------------------------------------------------
        await _set_stage(conn, target, "extracting")
        if not target.text.strip():
            await _fail(conn, target, "No text could be extracted from this source.")
            return

        # --- 2. clean + normalize, 3. chunk, 4. embed ----------------------
        # The ordered pipeline lives in ai/ingestion/processor.py, where it can be
        # tested without a database. The stage updates stay here because they are
        # database writes; the stage *sequence* mirrors the processor's steps, and
        # that mirroring is the thing a reviewer must be able to check by eye.
        await _set_stage(conn, target, "normalizing")
        await _set_stage(conn, target, "chunking")
        await _set_stage(conn, target, "embedding")
        prepared = await prepare_chunks(target.text, embedder, page_map=target.page_map)
        chunks = [item.chunk for item in prepared]

        # --- 5. store chunks + vectors, version-guarded ---------------------
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

            for item in prepared:
                await conn.execute(
                    f"""insert into public.document_chunks
                        (workspace_id, {parent_col}, chunk_index, content, token_count,
                         char_start, char_end, page_number, page_end, heading_path,
                         embedding, embedding_model, embedding_dim, version)
                        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::vector,$12,$13,$14)""",
                    target.workspace_id,
                    target.id,
                    item.chunk.chunk_index,
                    item.chunk.content,
                    item.chunk.token_count,
                    item.chunk.char_start,
                    item.chunk.char_end,
                    item.chunk.page_number,
                    item.chunk.page_end,
                    item.chunk.heading_path,
                    _vector_literal(item.embedding),
                    embedder.model,
                    embedder.dim,
                    target.version,
                )

            # "Mark document completed" is the last step of §4.1, which is why
            # completed_at is set here and not when chunks were stored.
            assignments = (
                "status='ready', processing_stage=null, error_message=null, "
                "embedding_model=$2, embedding_dim=$5, "
                "page_count=coalesce($3, page_count)"
            )
            if target.kind == "document":
                assignments += ", completed_at=now()"
            await conn.execute(
                f"""update public.{_table(target.kind)}
                    set {assignments}
                    where id=$1 and version=$4""",
                target.id,
                embedder.model,
                target.page_count,
                target.version,
                embedder.dim,
            )

        # --- 6/7. summary, then concepts + relationships --------------------
        # Design §4.1 orders summary and entity extraction BEFORE the completed
        # mark; the worker satisfies that by queueing both in the same run, and
        # lower priority numbers run first, so the summary lands before entities.
        #
        # These are inserted directly rather than through public.enqueue_job():
        # that helper is the *user* path and authorises with can_write_workspace(),
        # which needs auth.uid(). The worker has no end-user JWT, and it owns this
        # queue (0011: processing_jobs is service-writable, user-read-only), so a
        # direct insert is the honest expression of that ownership.
        #
        # Neither job can block retrieval: the source is already searchable here.
        for job_type, priority in (("summary", 150), ("entities", 200)):
            await conn.execute(
                """insert into public.processing_jobs
                     (workspace_id, type, note_id, document_id, payload, priority)
                   values ($1, $2, $3, $4, $5, $6)""",
                target.workspace_id,
                job_type,
                target.id if target.kind == "note" else None,
                target.id if target.kind == "document" else None,
                {"version": target.version},
                priority,
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
    except EmptySourceError as exc:
        # Extraction succeeded but nothing usable survived: same user-visible
        # outcome as an unreadable file, same failure path.
        await _fail(conn, target, str(exc))
        return
    except Exception:
        await conn.execute(
            f"""update public.{_table(target.kind)}
                set processing_stage=null,
                    error_message=coalesce(error_message,'Processing failed')
                where id=$1""",
            target.id,
        )
        raise


async def _download(file_path: str, settings: Settings) -> bytes:
    """Fetch the original bytes with the service role.

    Object storage has no FK, so the worker is the only thing that transfers
    bytes; the API never proxies file content.
    """
    import httpx

    url = f"{settings.supabase_url}/storage/v1/object/{settings.storage_bucket}/{file_path}"
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(
            url,
            headers=storage_admin_headers(settings),
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

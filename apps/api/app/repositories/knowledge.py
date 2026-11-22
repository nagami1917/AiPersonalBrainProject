"""Notes, documents, chunks and tags.

Notes and documents are separate tables, so this module exposes a small
`SourceKind` notion to keep callers honest: the few places that must treat them
uniformly (search results, citations, chunk previews) go through
`find_source_title` / `chunk_parent`, and everything else names the table it
means.

Every query carries `workspace_id` explicitly *in addition to* the RLS policy —
the same deliberate redundancy as before, now keyed on membership.
"""

from __future__ import annotations

from typing import Any, Literal

import asyncpg

SourceKind = Literal["note", "document"]

NOTE_COLUMNS = """
  id, title, status, processing_stage, collection_id, created_by,
  summary, content, error_message, version, embedding_model, created_at, updated_at
"""

DOCUMENT_COLUMNS = """
  id, title, status, processing_stage, collection_id, uploaded_by,
  summary, filename, mime_type, file_size, page_count, file_path, raw_text,
  error_message, version, embedding_model, created_at, updated_at
"""


# --- notes -------------------------------------------------------------------


async def get_note(
    conn: asyncpg.Connection, note_id: str, workspace_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"""select {NOTE_COLUMNS} from public.notes
            where id=$1 and workspace_id=$2 and deleted_at is null""",
        note_id,
        workspace_id,
    )


async def list_notes(
    conn: asyncpg.Connection,
    workspace_id: str,
    *,
    status: str | None = None,
    collection_id: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> list[asyncpg.Record]:
    conditions = ["workspace_id=$1", "deleted_at is null"]
    args: list[Any] = [workspace_id]
    if status:
        args.append(status)
        conditions.append(f"status=${len(args)}")
    if collection_id:
        args.append(collection_id)
        conditions.append(f"collection_id=${len(args)}")
    if cursor:
        args.append(cursor)
        conditions.append(f"updated_at < ${len(args)}::timestamptz")
    args.append(limit)

    return list(
        await conn.fetch(
            f"""select {NOTE_COLUMNS} from public.notes
                where {' and '.join(conditions)}
                order by updated_at desc limit ${len(args)}""",
            *args,
        )
    )


async def insert_note(conn: asyncpg.Connection, payload: dict[str, Any]) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""insert into public.notes
            (id, workspace_id, collection_id, created_by, title, content, content_format,
             status, metadata)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
            returning {NOTE_COLUMNS}""",
        payload["id"],
        payload["workspace_id"],
        payload.get("collection_id"),
        payload["created_by"],
        payload["title"],
        payload.get("content", ""),
        payload.get("content_format", "markdown"),
        payload.get("status", "pending"),
        payload.get("metadata", {}),
    )


async def update_note(
    conn: asyncpg.Connection, note_id: str, workspace_id: str, patch: dict[str, Any]
) -> asyncpg.Record | None:
    if not patch:
        return await get_note(conn, note_id, workspace_id)

    assignments: list[str] = []
    args: list[Any] = [note_id, workspace_id]
    for column, value in patch.items():
        args.append(value)
        assignments.append(f"{column} = ${len(args)}")

    return await conn.fetchrow(
        f"""update public.notes set {', '.join(assignments)}
            where id=$1 and workspace_id=$2 and deleted_at is null
            returning {NOTE_COLUMNS}""",
        *args,
    )


async def bump_note_version(conn: asyncpg.Connection, note_id: str, workspace_id: str) -> int:
    value = await conn.fetchval(
        """update public.notes
           set version = version + 1, status='pending', processing_stage=null,
               error_message=null
           where id=$1 and workspace_id=$2 and deleted_at is null
           returning version""",
        note_id,
        workspace_id,
    )
    return int(value or 0)


# --- documents ---------------------------------------------------------------


async def get_document(
    conn: asyncpg.Connection, document_id: str, workspace_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"""select {DOCUMENT_COLUMNS} from public.documents
            where id=$1 and workspace_id=$2 and deleted_at is null""",
        document_id,
        workspace_id,
    )


async def list_documents(
    conn: asyncpg.Connection,
    workspace_id: str,
    *,
    status: str | None = None,
    collection_id: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> list[asyncpg.Record]:
    conditions = ["workspace_id=$1", "deleted_at is null"]
    args: list[Any] = [workspace_id]
    if status:
        args.append(status)
        conditions.append(f"status=${len(args)}")
    if collection_id:
        args.append(collection_id)
        conditions.append(f"collection_id=${len(args)}")
    if cursor:
        args.append(cursor)
        conditions.append(f"updated_at < ${len(args)}::timestamptz")
    args.append(limit)

    return list(
        await conn.fetch(
            f"""select {DOCUMENT_COLUMNS} from public.documents
                where {' and '.join(conditions)}
                order by updated_at desc limit ${len(args)}""",
            *args,
        )
    )


async def find_document_by_checksum(
    conn: asyncpg.Connection, workspace_id: str, checksum: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"""select {DOCUMENT_COLUMNS} from public.documents
            where workspace_id=$1 and checksum_sha256=$2 and deleted_at is null""",
        workspace_id,
        checksum,
    )


async def insert_document(conn: asyncpg.Connection, payload: dict[str, Any]) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""insert into public.documents
            (id, workspace_id, collection_id, uploaded_by, title, filename, mime_type,
             file_path, file_size, checksum_sha256, raw_text, status, metadata)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
            returning {DOCUMENT_COLUMNS}""",
        payload["id"],
        payload["workspace_id"],
        payload.get("collection_id"),
        payload["uploaded_by"],
        payload["title"],
        payload["filename"],
        payload.get("mime_type"),
        payload["file_path"],
        payload.get("file_size"),
        payload.get("checksum_sha256"),
        payload.get("raw_text"),
        payload.get("status", "pending"),
        payload.get("metadata", {}),
    )


async def update_document(
    conn: asyncpg.Connection, document_id: str, workspace_id: str, patch: dict[str, Any]
) -> asyncpg.Record | None:
    if not patch:
        return await get_document(conn, document_id, workspace_id)

    assignments: list[str] = []
    args: list[Any] = [document_id, workspace_id]
    for column, value in patch.items():
        args.append(value)
        assignments.append(f"{column} = ${len(args)}")

    return await conn.fetchrow(
        f"""update public.documents set {', '.join(assignments)}
            where id=$1 and workspace_id=$2 and deleted_at is null
            returning {DOCUMENT_COLUMNS}""",
        *args,
    )


async def soft_delete_document(
    conn: asyncpg.Connection, document_id: str, workspace_id: str
) -> bool:
    result = await conn.execute(
        """update public.documents set deleted_at = now(), status='archived'
           where id=$1 and workspace_id=$2 and deleted_at is null""",
        document_id,
        workspace_id,
    )
    await conn.execute("delete from public.document_chunks where document_id=$1", document_id)
    return str(result).endswith("1")


async def soft_delete_note(conn: asyncpg.Connection, note_id: str, workspace_id: str) -> bool:
    result = await conn.execute(
        """update public.notes set deleted_at = now(), status='archived'
           where id=$1 and workspace_id=$2 and deleted_at is null""",
        note_id,
        workspace_id,
    )
    await conn.execute("delete from public.document_chunks where note_id=$1", note_id)
    return str(result).endswith("1")


# --- chunks ------------------------------------------------------------------


async def list_chunks(
    conn: asyncpg.Connection,
    workspace_id: str,
    *,
    note_id: str | None = None,
    document_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[asyncpg.Record]:
    if note_id is None and document_id is None:
        return []
    return list(
        await conn.fetch(
            """select id, chunk_index, content, heading_path, page_number, page_end,
                      char_start, char_end, token_count, note_id, document_id
               from public.document_chunks
               where workspace_id=$1
                 and ($2::uuid is null or note_id=$2::uuid)
                 and ($3::uuid is null or document_id=$3::uuid)
               order by chunk_index limit $4 offset $5""",
            workspace_id,
            note_id,
            document_id,
            limit,
            offset,
        )
    )


async def chunk_count(
    conn: asyncpg.Connection,
    workspace_id: str,
    *,
    note_id: str | None = None,
    document_id: str | None = None,
) -> int:
    value = await conn.fetchval(
        """select count(*) from public.document_chunks
           where workspace_id=$1
             and ($2::uuid is null or note_id=$2::uuid)
             and ($3::uuid is null or document_id=$3::uuid)""",
        workspace_id,
        note_id,
        document_id,
    )
    return int(value or 0)


async def find_source_title(
    conn: asyncpg.Connection, *, chunk_id: str, workspace_id: str
) -> tuple[SourceKind, str, str] | None:
    """Resolve a chunk to (kind, parent_id, title) in one round trip."""
    row = await conn.fetchrow(
        """select case when c.note_id is not null then 'note' else 'document' end as kind,
                  coalesce(c.note_id, c.document_id) as parent_id,
                  coalesce(n.title, d.title) as title
           from public.document_chunks c
           left join public.notes n on n.id = c.note_id
           left join public.documents d on d.id = c.document_id
           where c.id=$1 and c.workspace_id=$2""",
        chunk_id,
        workspace_id,
    )
    if row is None:
        return None
    return row["kind"], str(row["parent_id"]), row["title"]


# --- tags --------------------------------------------------------------------


async def tag_names(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    note_id: str | None = None,
    document_id: str | None = None,
) -> list[str]:
    if note_id:
        rows = await conn.fetch(
            """select t.name from public.note_tags nt
               join public.tags t on t.id = nt.tag_id
               where nt.note_id=$1 and nt.workspace_id=$2 order by t.name""",
            note_id,
            workspace_id,
        )
    elif document_id:
        rows = await conn.fetch(
            """select t.name from public.document_tags dt
               join public.tags t on t.id = dt.tag_id
               where dt.document_id=$1 and dt.workspace_id=$2 order by t.name""",
            document_id,
            workspace_id,
        )
    else:
        return []
    return [r["name"] for r in rows]


async def replace_tags(
    conn: asyncpg.Connection,
    workspace_id: str,
    names: list[str],
    *,
    note_id: str | None = None,
    document_id: str | None = None,
) -> None:
    if note_id:
        await conn.execute("delete from public.note_tags where note_id=$1", note_id)
        link_table, link_col = "note_tags", "note_id"
    elif document_id:
        await conn.execute("delete from public.document_tags where document_id=$1", document_id)
        link_table, link_col = "document_tags", "document_id"
    else:
        return

    for raw in names:
        name = raw.strip()
        if not name:
            continue
        tag_id = await conn.fetchval(
            """insert into public.tags (workspace_id, name) values ($1,$2)
               on conflict (workspace_id, lower(btrim(name))) do update
                 set name = excluded.name
               returning id""",
            workspace_id,
            name[:60],
        )
        await conn.execute(
            f"""insert into public.{link_table} ({link_col}, tag_id, workspace_id)
                values ($1,$2,$3) on conflict do nothing""",
            note_id or document_id,
            tag_id,
            workspace_id,
        )

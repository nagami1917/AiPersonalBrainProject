"""Document data access. Every query runs on a connection bound to the caller,
so RLS applies even though user_id is also passed explicitly (defense in depth).
"""

from __future__ import annotations

from typing import Any

import asyncpg

DOC_COLUMNS = """
  id, title, source_type, status, processing_stage, collection_id, mime_type,
  file_size, page_count, summary, storage_path, raw_text, error_message,
  version, embedding_model, created_at, updated_at
"""


async def get(conn: asyncpg.Connection, document_id: str, user_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"""select {DOC_COLUMNS} from public.documents
            where id=$1 and user_id=$2 and deleted_at is null""",
        document_id,
        user_id,
    )


async def list_documents(
    conn: asyncpg.Connection,
    user_id: str,
    *,
    status: str | None = None,
    collection_id: str | None = None,
    source_type: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> list[asyncpg.Record]:
    conditions = ["user_id=$1", "deleted_at is null"]
    args: list[Any] = [user_id]

    if status:
        args.append(status)
        conditions.append(f"status=${len(args)}")
    if collection_id:
        args.append(collection_id)
        conditions.append(f"collection_id=${len(args)}")
    if source_type:
        args.append(source_type)
        conditions.append(f"source_type=${len(args)}")
    if cursor:
        args.append(cursor)
        conditions.append(f"updated_at < ${len(args)}::timestamptz")

    args.append(limit)
    query = f"""select {DOC_COLUMNS} from public.documents
                where {' and '.join(conditions)}
                order by updated_at desc
                limit ${len(args)}"""
    return list(await conn.fetch(query, *args))


async def tag_names(conn: asyncpg.Connection, document_id: str) -> list[str]:
    rows = await conn.fetch(
        """select t.name from public.document_tags dt
           join public.tags t on t.id = dt.tag_id
           where dt.document_id = $1 order by t.name""",
        document_id,
    )
    return [r["name"] for r in rows]


async def chunk_count(conn: asyncpg.Connection, document_id: str) -> int:
    value = await conn.fetchval(
        "select count(*) from public.chunks where document_id=$1", document_id
    )
    return int(value or 0)


async def find_by_checksum(
    conn: asyncpg.Connection, user_id: str, checksum: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"""select {DOC_COLUMNS} from public.documents
            where user_id=$1 and checksum_sha256=$2 and deleted_at is null""",
        user_id,
        checksum,
    )


async def insert_document(conn: asyncpg.Connection, payload: dict[str, Any]) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""insert into public.documents
            (id, user_id, collection_id, source_type, title, mime_type, storage_path,
             file_size, checksum_sha256, raw_text, status, metadata)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
            returning {DOC_COLUMNS}""",
        payload["id"],
        payload["user_id"],
        payload.get("collection_id"),
        payload.get("source_type", "upload"),
        payload["title"],
        payload.get("mime_type"),
        payload.get("storage_path"),
        payload.get("file_size"),
        payload.get("checksum_sha256"),
        payload.get("raw_text"),
        payload.get("status", "pending"),
        payload.get("metadata", {}),
    )


async def update_document(
    conn: asyncpg.Connection, document_id: str, user_id: str, patch: dict[str, Any]
) -> asyncpg.Record | None:
    if not patch:
        return await get(conn, document_id, user_id)

    assignments: list[str] = []
    args: list[Any] = [document_id, user_id]
    for column, value in patch.items():
        args.append(value)
        assignments.append(f"{column} = ${len(args)}")

    return await conn.fetchrow(
        f"""update public.documents set {', '.join(assignments)}
            where id=$1 and user_id=$2 and deleted_at is null
            returning {DOC_COLUMNS}""",
        *args,
    )


async def bump_version(conn: asyncpg.Connection, document_id: str, user_id: str) -> int:
    value = await conn.fetchval(
        """update public.documents
           set version = version + 1, status='pending', processing_stage=null,
               error_message=null
           where id=$1 and user_id=$2 and source_type='note' and deleted_at is null
           returning version""",
        document_id,
        user_id,
    )
    return int(value or 0)


async def soft_delete(conn: asyncpg.Connection, document_id: str, user_id: str) -> bool:
    result = await conn.execute(
        """update public.documents set deleted_at = now(), status='archived'
           where id=$1 and user_id=$2 and deleted_at is null""",
        document_id,
        user_id,
    )
    # Chunks go immediately so the document stops surfacing in retrieval before
    # the storage object is cleaned up asynchronously.
    await conn.execute("delete from public.chunks where document_id=$1", document_id)
    return str(result).endswith("1")


async def replace_tags(
    conn: asyncpg.Connection, document_id: str, user_id: str, names: list[str]
) -> None:
    await conn.execute("delete from public.document_tags where document_id=$1", document_id)
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        tag_id = await conn.fetchval(
            """insert into public.tags (user_id, name) values ($1,$2)
               on conflict (user_id, lower(btrim(name))) do update set name = excluded.name
               returning id""",
            user_id,
            name[:60],
        )
        await conn.execute(
            """insert into public.document_tags (document_id, tag_id, user_id)
               values ($1,$2,$3) on conflict do nothing""",
            document_id,
            tag_id,
            user_id,
        )

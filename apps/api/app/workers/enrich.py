"""Summary and entity extraction jobs.

Both are best-effort by design: a failure here degrades the graph and the list
view, but must never block retrieval — the source is already `ready` with
searchable chunks by the time these run.

Works on either parent. The chunks table gives us the text and the entity links
regardless of which table the source lives in.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import asyncpg

from app.ai.prompts import build_entity_prompt, build_summary_prompt
from app.ai.providers import LLM

log = logging.getLogger(__name__)

VALID_TYPES = {"person", "organization", "project", "concept", "place", "event", "other"}
_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def _table(kind: str) -> str:
    return "notes" if kind == "note" else "documents"


def _text_column(kind: str) -> str:
    return "content" if kind == "note" else "raw_text"


async def _load_source(
    conn: asyncpg.Connection, *, kind: str, source_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"""select id, workspace_id, version, {_text_column(kind)} as text
            from public.{_table(kind)} where id=$1""",
        source_id,
    )


async def run_summary(
    conn: asyncpg.Connection,
    *,
    kind: str,
    source_id: str,
    payload: dict[str, Any],
    llm: LLM,
) -> None:
    version = int(payload.get("version", 1))
    row = await _load_source(conn, kind=kind, source_id=source_id)
    if not row or not row["text"]:
        return
    if int(row["version"]) != version:
        log.info("summary superseded for %s=%s", kind, source_id)
        return

    summary = (
        await llm.complete(
            "You summarize documents factually and tersely.",
            build_summary_prompt(row["text"]),
        )
    ).strip()

    await conn.execute(
        f"update public.{_table(kind)} set summary=$2 where id=$1 and version=$3",
        source_id,
        summary[:2000],
        version,
    )


async def run_entities(
    conn: asyncpg.Connection,
    *,
    kind: str,
    source_id: str,
    payload: dict[str, Any],
    llm: LLM,
) -> None:
    version = int(payload.get("version", 1))
    row = await _load_source(conn, kind=kind, source_id=source_id)
    if not row or not row["text"] or int(row["version"]) != version:
        return

    workspace_id = str(row["workspace_id"])

    raw = await llm.complete(
        "You extract structured data. You reply with JSON only.",
        build_entity_prompt(row["text"]),
    )
    parsed = _parse_json(raw)
    if not parsed:
        log.info("entity extraction returned no parsable JSON for %s=%s", kind, source_id)
        return

    parent_col = "note_id" if kind == "note" else "document_id"
    chunk_rows = await conn.fetch(
        f"""select id, content from public.document_chunks
            where {parent_col}=$1 and version=$2""",
        source_id,
        version,
    )

    entity_ids: dict[str, str] = {}
    for entity in parsed.get("entities", []):
        name = str(entity.get("name", "")).strip()
        if not name or len(name) > 200:
            continue
        normalized = normalize_entity_name(name)
        entity_type = str(entity.get("type", "other")).lower()
        if entity_type not in VALID_TYPES:
            entity_type = "other"

        entity_id = await conn.fetchval(
            """insert into public.knowledge_entities
                 (workspace_id, name, normalized_name, entity_type)
               values ($1,$2,$3,$4)
               on conflict (workspace_id, normalized_name) do update
                 set last_seen_at = now()
               returning id""",
            workspace_id,
            name,
            normalized,
            entity_type,
        )
        entity_ids[normalized] = str(entity_id)

        # Link only to chunks that actually mention the name: cheap, honest, and
        # never invented.
        for chunk in chunk_rows:
            if name.lower() in chunk["content"].lower():
                await conn.execute(
                    """insert into public.chunk_entities
                         (chunk_id, entity_id, workspace_id, confidence)
                       values ($1,$2,$3,$4)
                       on conflict (chunk_id, entity_id) do nothing""",
                    chunk["id"],
                    entity_id,
                    workspace_id,
                    0.8,
                )

    for relation in parsed.get("relations", []):
        source_key = normalize_entity_name(str(relation.get("source", "")))
        target_key = normalize_entity_name(str(relation.get("target", "")))
        source_entity_id = entity_ids.get(source_key)
        target_entity_id = entity_ids.get(target_key)
        if not source_entity_id or not target_entity_id or source_entity_id == target_entity_id:
            continue
        label = str(relation.get("relation", "co_occurs"))[:80] or "co_occurs"
        await conn.execute(
            """insert into public.knowledge_links
                 (workspace_id, source_entity_id, target_entity_id, relation, weight)
               values ($1,$2,$3,$4,1)
               on conflict (workspace_id, source_entity_id, target_entity_id, relation)
               do update set weight = public.knowledge_links.weight + 1""",
            workspace_id,
            source_entity_id,
            target_entity_id,
            label,
        )

    for entity_id in entity_ids.values():
        await conn.execute("select public.refresh_entity_mentions($1)", entity_id)


def normalize_entity_name(name: str) -> str:
    cleaned = re.sub(r"^(the|a|an)\s+", "", name.strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:200]


def _parse_json(raw: str) -> dict[str, Any] | None:
    match = _JSON_BLOCK.search(raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None

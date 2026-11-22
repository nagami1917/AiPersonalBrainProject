"""Summary and entity extraction jobs.

Both are best-effort by design: a failure here degrades the graph and the
document list, but must never block retrieval (a document is already `ready`
with searchable chunks by the time these run).
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


async def run_summary(
    conn: asyncpg.Connection, *, document_id: str, payload: dict[str, Any], llm: LLM
) -> None:
    version = int(payload.get("version", 1))
    row = await conn.fetchrow(
        "select title, raw_text, version from public.documents where id=$1", document_id
    )
    if not row or not row["raw_text"]:
        return
    if row["version"] != version:
        log.info("summary superseded for doc=%s", document_id)
        return

    summary = (
        await llm.complete(
            "You summarize documents factually and tersely.",
            build_summary_prompt(row["raw_text"]),
        )
    ).strip()

    await conn.execute(
        "update public.documents set summary=$2 where id=$1 and version=$3",
        document_id,
        summary[:2000],
        version,
    )


async def run_entities(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    document_id: str,
    payload: dict[str, Any],
    llm: LLM,
) -> None:
    version = int(payload.get("version", 1))
    row = await conn.fetchrow(
        "select raw_text, version from public.documents where id=$1", document_id
    )
    if not row or not row["raw_text"] or row["version"] != version:
        return

    raw = await llm.complete(
        "You extract structured data. You reply with JSON only.",
        build_entity_prompt(row["raw_text"]),
    )
    parsed = _parse_json(raw)
    if not parsed:
        log.info("entity extraction returned no parsable JSON for doc=%s", document_id)
        return

    chunk_rows = await conn.fetch(
        "select id, content from public.chunks where document_id=$1 and version=$2",
        document_id,
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
            """insert into public.entities (user_id, name, normalized_name, entity_type)
               values ($1,$2,$3,$4)
               on conflict (user_id, normalized_name) do update
                 set last_seen_at = now()
               returning id""",
            user_id,
            name,
            normalized,
            entity_type,
        )
        entity_ids[normalized] = str(entity_id)

        # link to chunks that actually mention the name (cheap, honest, no guessing)
        for chunk in chunk_rows:
            if name.lower() in chunk["content"].lower():
                await conn.execute(
                    """insert into public.chunk_entities (chunk_id, entity_id, user_id, confidence)
                       values ($1,$2,$3,$4)
                       on conflict (chunk_id, entity_id) do nothing""",
                    chunk["id"],
                    entity_id,
                    user_id,
                    0.8,
                )

    for relation in parsed.get("relations", []):
        source_key = normalize_entity_name(str(relation.get("source", "")))
        target_key = normalize_entity_name(str(relation.get("target", "")))
        source_id = entity_ids.get(source_key)
        target_id = entity_ids.get(target_key)
        if not source_id or not target_id or source_id == target_id:
            continue
        label = str(relation.get("relation", "co_occurs"))[:80] or "co_occurs"
        await conn.execute(
            """insert into public.entity_edges
                 (user_id, source_entity_id, target_entity_id, relation, weight)
               values ($1,$2,$3,$4,1)
               on conflict (user_id, source_entity_id, target_entity_id, relation)
               do update set weight = public.entity_edges.weight + 1""",
            user_id,
            source_id,
            target_id,
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

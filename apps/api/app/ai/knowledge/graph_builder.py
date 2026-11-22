"""Writing an extraction into the knowledge graph, without inventing edges.

The graph is derived data: it must be reproducible from the sources and must
never assert something the text does not support. Three rules follow from that:

1. **Upsert by normalised name.** "Fly.io" in two documents is one entity, so
   `mention_count` means "how central is this to the workspace", not "how many
   spellings did the user use".
2. **Link only to chunks that mention it** (`entity_extractor.mentions`). A
   mention row is a claim about a specific passage; a claim the passage does not
   support is a fabricated citation in graph form.
3. **Recompute counts from `chunk_entities`**, never by incrementing. An
   incrementing counter drifts the moment a job is retried, and this job *is*
   retried.

Database writes live here rather than in the worker because this is the unit
worth testing against a real schema.
"""

from __future__ import annotations

import logging

import asyncpg

from app.ai.knowledge.entity_extractor import Entity, Extraction, mentions

log = logging.getLogger(__name__)


async def upsert_entity(
    conn: asyncpg.Connection, *, workspace_id: str, extraction_entity: Entity
) -> str:
    entity_id = await conn.fetchval(
        """insert into public.knowledge_entities
             (workspace_id, name, normalized_name, entity_type)
           values ($1,$2,$3,$4)
           on conflict (workspace_id, normalized_name) do update
             set last_seen_at = now()
           returning id""",
        workspace_id,
        extraction_entity.name,
        extraction_entity.normalized,
        extraction_entity.entity_type,
    )
    return str(entity_id)


async def link_mentions(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    entity_id: str,
    name: str,
    chunks: list[asyncpg.Record],
    confidence: float = 0.8,
) -> int:
    """Link an entity to the chunks that literally mention it. Returns link count."""
    linked = 0
    for chunk in chunks:
        if not mentions(name, chunk["content"]):
            continue
        await conn.execute(
            """insert into public.chunk_entities
                 (chunk_id, entity_id, workspace_id, confidence)
               values ($1,$2,$3,$4)
               on conflict (chunk_id, entity_id) do nothing""",
            chunk["id"],
            entity_id,
            workspace_id,
            confidence,
        )
        linked += 1
    return linked


async def upsert_relation(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    source_id: str,
    target_id: str,
    label: str,
) -> None:
    await conn.execute(
        """insert into public.knowledge_links
             (workspace_id, source_entity_id, target_entity_id, relation, weight)
           values ($1,$2,$3,$4,1)
           on conflict (workspace_id, source_entity_id, target_entity_id, relation)
           do update set weight = public.knowledge_links.weight + 1""",
        workspace_id,
        source_id,
        target_id,
        label,
    )


async def persist(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    extraction: Extraction,
    chunks: list[asyncpg.Record],
) -> tuple[int, int]:
    """Write a full extraction. Returns (entities, relations) written.

    Only entities that end up linked to at least one chunk are kept in the
    map used for relations: an edge between two entities the text never mentions
    would be invisible in the UI and misleading in the API.
    """
    entity_ids: dict[str, str] = {}
    for entity in extraction.entities:
        entity_id = await upsert_entity(conn, workspace_id=workspace_id, extraction_entity=entity)
        linked = await link_mentions(
            conn,
            workspace_id=workspace_id,
            entity_id=entity_id,
            name=entity.name,
            chunks=chunks,
        )
        if linked:
            entity_ids[entity.normalized] = entity_id

    written = 0
    for relation in extraction.relations:
        source_id = entity_ids.get(relation.source)
        target_id = entity_ids.get(relation.target)
        if not source_id or not target_id or source_id == target_id:
            continue
        await upsert_relation(
            conn,
            workspace_id=workspace_id,
            source_id=source_id,
            target_id=target_id,
            label=relation.label,
        )
        written += 1

    for entity_id in entity_ids.values():
        await conn.execute("select public.refresh_entity_mentions($1)", entity_id)

    return len(entity_ids), written

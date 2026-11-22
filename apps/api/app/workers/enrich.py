"""Summary and entity extraction jobs.

Both are best-effort by design: a failure here degrades the graph and the list
view, but must never block retrieval — the source is already `ready` with
searchable chunks by the time these run.

The AI work lives in `app.ai.summarization` and `app.ai.knowledge`; this module is
the job-shaped wrapper: load the source text, check the version, call the module,
write the result. Keeping the split means the extraction rules (what counts as a
mention, which relations survive) are testable against fixed strings, with no
database and no model.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from app.ai.knowledge import build_entity_prompt, extract_knowledge, persist
from app.ai.providers import LLM
from app.ai.summarization import (
    build_document_summary_prompt,
    build_note_summary_prompt,
    clean_summary,
)

log = logging.getLogger(__name__)


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
        # A newer version exists: summarising the stale text would attach an
        # accurate summary to the wrong content.
        log.info("summary superseded for %s=%s", kind, source_id)
        return

    build_prompt = build_note_summary_prompt if kind == "note" else build_document_summary_prompt
    summary = (
        await llm.complete(
            "You summarize documents factually and tersely.",
            build_prompt(row["text"]),
        )
    ).strip()

    await conn.execute(
        f"update public.{_table(kind)} set summary=$2 where id=$1 and version=$3",
        source_id,
        clean_summary(summary),
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

    raw = await llm.complete(
        "You extract structured data. You reply with JSON only.",
        build_entity_prompt(row["text"]),
    )
    extraction = extract_knowledge(raw)
    if extraction.is_empty:
        log.info("entity extraction produced nothing usable for %s=%s", kind, source_id)
        return

    parent_col = "note_id" if kind == "note" else "document_id"
    chunks = await conn.fetch(
        f"""select id, content from public.document_chunks
            where {parent_col}=$1 and version=$2""",
        source_id,
        version,
    )

    entities, relations = await persist(
        conn,
        workspace_id=str(row["workspace_id"]),
        extraction=extraction,
        chunks=list(chunks),
    )
    log.info(
        "knowledge graph updated %s=%s entities=%s relations=%s",
        kind,
        source_id,
        entities,
        relations,
    )

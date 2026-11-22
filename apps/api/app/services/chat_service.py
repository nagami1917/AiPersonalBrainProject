"""Chat orchestration and the grounded-answer contract in practice.

Two invariants live here, and both are asserted by tests:

  1. If retrieval is empty (or below the floor), the LLM is NEVER called. The
     refusal is deterministic and cheap.
  2. Persisted citations come from resolve_citations(), which only emits rows
     for markers that map to real retrieved chunks. A fabricated [9] produces
     no citation row.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import asyncpg

from app.ai.grounding import (
    REFUSAL_TEMPLATE,
    SYSTEM_PROMPT,
    Citation,
    GroundedAnswer,
    resolve_citations,
    should_refuse,
)
from app.ai.prompts import build_user_turn
from app.ai.providers import LLM, Embedder
from app.core.config import Settings
from app.schemas.chat import ChatRequest, CitationOut
from app.services.retrieval_service import retrieve

log = logging.getLogger(__name__)


async def _ensure_conversation(
    conn: asyncpg.Connection, user_id: str, conversation_id: str | None
) -> str:
    if conversation_id:
        exists = await conn.fetchval(
            "select id from public.conversations where id=$1 and user_id=$2",
            conversation_id,
            user_id,
        )
        if not exists:
            from app.core.errors import NotFound

            raise NotFound("Conversation not found")
        return str(exists)

    return str(
        await conn.fetchval(
            "insert into public.conversations (user_id) values ($1) returning id", user_id
        )
    )


async def _history(
    conn: asyncpg.Connection, conversation_id: str, limit: int = 8
) -> list[dict[str, str]]:
    rows = await conn.fetch(
        """select role, content from public.messages
           where conversation_id=$1 and role in ('user','assistant')
           order by created_at desc limit $2""",
        conversation_id,
        limit,
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def _persist_user_message(
    conn: asyncpg.Connection, user_id: str, conversation_id: str, content: str
) -> str:
    return str(
        await conn.fetchval(
            """insert into public.messages (user_id, conversation_id, role, content)
               values ($1,$2,'user',$3) returning id""",
            user_id,
            conversation_id,
            content,
        )
    )


async def _persist_assistant_message(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    conversation_id: str,
    answer: GroundedAnswer,
    model: str,
    latency_ms: int,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> str:
    message_id = await conn.fetchval(
        """insert into public.messages
             (user_id, conversation_id, role, content, grounded, model, latency_ms,
              tokens_in, tokens_out)
           values ($1,$2,'assistant',$3,$4,$5,$6,$7,$8) returning id""",
        user_id,
        conversation_id,
        answer.content,
        answer.grounded,
        model,
        latency_ms,
        tokens_in,
        tokens_out,
    )

    for citation in answer.citations:
        await conn.execute(
            """insert into public.message_citations
                 (user_id, message_id, chunk_id, document_id, document_title, marker,
                  quote, page, heading_path, score)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
            user_id,
            message_id,
            citation.chunk_id,
            citation.document_id,
            citation.document_title,
            citation.marker,
            citation.quote,
            citation.page,
            citation.heading_path,
            citation.score,
        )

    if answer.fabricated_markers:
        log.warning(
            "grounding_violation conversation=%s fabricated=%s",
            conversation_id,
            answer.fabricated_markers,
        )

    await conn.execute(
        "update public.conversations set updated_at=now() where id=$1", conversation_id
    )
    return str(message_id)


def _to_out(citation: Citation) -> CitationOut:
    return CitationOut(
        marker=citation.marker,
        chunk_id=citation.chunk_id,
        document_id=citation.document_id,
        document_title=citation.document_title,
        quote=citation.quote,
        page=citation.page,
        heading_path=citation.heading_path,
        score=citation.score,
    )


async def answer(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    request: ChatRequest,
    embedder: Embedder,
    llm: LLM,
    settings: Settings,
) -> tuple[str, str, GroundedAnswer]:
    started = time.perf_counter()
    conversation_id = await _ensure_conversation(conn, user_id, request.conversation_id)
    await _persist_user_message(conn, user_id, conversation_id, request.message)

    history = await _history(conn, conversation_id)
    # Drop the message we just stored from the "history" passed to the model.
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    result = await retrieve(
        conn,
        user_id=user_id,
        query=request.message,
        embedder=embedder,
        settings=settings,
        filters=request.filters,
        match_count=request.options.max_sources,
    )

    if should_refuse(result.sources, settings.score_floor):
        answer_obj = GroundedAnswer(content=REFUSAL_TEMPLATE, grounded=False)
        latency = int((time.perf_counter() - started) * 1000)
        message_id = await _persist_assistant_message(
            conn,
            user_id=user_id,
            conversation_id=conversation_id,
            answer=answer_obj,
            model="none",
            latency_ms=latency,
        )
        return message_id, conversation_id, answer_obj

    user_turn = build_user_turn(request.message, result.sources, history)
    raw_output = await llm.complete(SYSTEM_PROMPT, user_turn)
    answer_obj = resolve_citations(raw_output, result.sources)

    latency = int((time.perf_counter() - started) * 1000)
    message_id = await _persist_assistant_message(
        conn,
        user_id=user_id,
        conversation_id=conversation_id,
        answer=answer_obj,
        model=llm.model,
        latency_ms=latency,
    )
    return message_id, conversation_id, answer_obj


async def stream_answer(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    request: ChatRequest,
    embedder: Embedder,
    llm: LLM,
    settings: Settings,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """SSE event generator.

    Event order is deliberate: `sources` is emitted BEFORE generation so the UI
    can honestly show what is being read instead of reconstructing it after the
    fact. `citations` arrives after the text, resolving markers that streamed.
    """
    started = time.perf_counter()
    conversation_id = await _ensure_conversation(conn, user_id, request.conversation_id)
    await _persist_user_message(conn, user_id, conversation_id, request.message)
    history = await _history(conn, conversation_id)
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    result = await retrieve(
        conn,
        user_id=user_id,
        query=request.message,
        embedder=embedder,
        settings=settings,
        filters=request.filters,
        match_count=request.options.max_sources,
    )

    yield (
        "sources",
        {
            "sources": [
                {
                    "marker": s.marker,
                    "document_id": s.document_id,
                    "document_title": s.document_title,
                    "page": s.page_start,
                    "heading_path": s.heading_path,
                }
                for s in result.sources
            ]
        },
    )

    if should_refuse(result.sources, settings.score_floor):
        answer_obj = GroundedAnswer(content=REFUSAL_TEMPLATE, grounded=False)
        message_id = await _persist_assistant_message(
            conn,
            user_id=user_id,
            conversation_id=conversation_id,
            answer=answer_obj,
            model="none",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        yield ("delta", {"text": REFUSAL_TEMPLATE})
        yield ("citations", {"citations": [], "grounded": False})
        yield (
            "done",
            {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return

    user_turn = build_user_turn(request.message, result.sources, history)
    buffer: list[str] = []
    async for delta in llm.stream(SYSTEM_PROMPT, user_turn):
        buffer.append(delta)
        yield ("delta", {"text": delta})

    answer_obj = resolve_citations("".join(buffer), result.sources)
    latency = int((time.perf_counter() - started) * 1000)

    # Persist the RESOLVED content: fabricated markers never reach history.
    message_id = await _persist_assistant_message(
        conn,
        user_id=user_id,
        conversation_id=conversation_id,
        answer=answer_obj,
        model=llm.model,
        latency_ms=latency,
    )

    yield (
        "citations",
        {
            "citations": [json.loads(_to_out(c).model_dump_json()) for c in answer_obj.citations],
            "grounded": answer_obj.grounded,
        },
    )
    yield (
        "done",
        {"message_id": message_id, "conversation_id": conversation_id, "latency_ms": latency},
    )

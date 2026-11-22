from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.ai.grounding import GroundedAnswer
from app.ai.providers import LLM, Embedder
from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser, UserConn, WorkspaceCtx, get_embedder, get_llm
from app.core.errors import NotFound
from app.schemas.chat import ChatRequest, ChatResponse, CitationOut
from app.services import chat_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces/{workspace_id}/chat", tags=["chat"])


def _citations(answer: GroundedAnswer) -> list[CitationOut]:
    return [
        CitationOut(
            marker=c.marker,
            chunk_id=c.chunk_id,
            source_kind=c.source_kind,  # type: ignore[arg-type]
            source_id=c.document_id,
            source_title=c.document_title,
            quote=c.quote,
            page=c.page,
            heading_path=c.heading_path,
            score=c.score,
        )
        for c in answer.citations
    ]


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    ctx: WorkspaceCtx,
    conn: UserConn,
    user: CurrentUser,
    embedder: Embedder = Depends(get_embedder),
    llm: LLM = Depends(get_llm),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    message_id, conversation_id, answer = await chat_service.answer(
        conn,
        workspace_id=ctx.id,
        user_id=user.id,
        request=request,
        embedder=embedder,
        llm=llm,
        settings=settings,
    )
    return ChatResponse(
        message_id=message_id,
        conversation_id=conversation_id,
        content=answer.content,
        grounded=answer.grounded,
        citations=_citations(answer),
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/stream")
async def stream(
    request: ChatRequest,
    ctx: WorkspaceCtx,
    user: CurrentUser,
    embedder: Embedder = Depends(get_embedder),
    llm: LLM = Depends(get_llm),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Server-sent events.

    The transaction must stay open for the whole stream, so ownership of the
    connection moves into the generator rather than the route's dependency.
    """
    from app.core.db import get_pool
    from app.core.security import user_connection

    # The pool is fetched here rather than injected: the transaction must stay
    # open for the whole stream, so connection ownership moves into the generator.
    pool = await get_pool(settings)

    async def event_stream() -> AsyncIterator[str]:
        async with pool.acquire() as conn:
            tx = conn.transaction()
            await tx.start()
            try:
                await user_connection(conn, user)
                async for event, data in chat_service.stream_answer(
                    conn,
                    workspace_id=ctx.id,
                    user_id=user.id,
                    request=request,
                    embedder=embedder,
                    llm=llm,
                    settings=settings,
                ):
                    yield _sse(event, data)
                await tx.commit()
            except Exception as exc:  # noqa: BLE001 - surfaced to the client
                await tx.rollback()
                log.exception("chat stream failed")
                yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations", response_model=list[dict[str, Any]])
async def list_conversations(ctx: WorkspaceCtx, conn: UserConn) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select c.id, c.title, c.updated_at,
                  (select count(*) from public.chat_messages m
                    where m.conversation_id = c.id) as message_count
           from public.conversations c
           where c.workspace_id=$1
           order by c.updated_at desc limit 50""",
        ctx.id,
    )
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "message_count": int(r["message_count"]),
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=dict)
async def get_conversation(
    conversation_id: str, ctx: WorkspaceCtx, conn: UserConn
) -> dict[str, Any]:
    conversation = await conn.fetchrow(
        "select id, title, created_at, updated_at from public.conversations "
        "where id=$1 and workspace_id=$2",
        conversation_id,
        ctx.id,
    )
    if conversation is None:
        raise NotFound("Conversation not found")

    messages = await conn.fetch(
        """select id, role, content, grounded, model, latency_ms, feedback, created_at
           from public.chat_messages
           where conversation_id=$1 order by created_at""",
        conversation_id,
    )
    sources = await conn.fetch(
        """select message_id, marker, chunk_id, source_kind, source_title,
                  quote, page, heading_path, score
           from public.message_sources
           where workspace_id=$1
             and message_id = any($2::uuid[])
           order by marker""",
        ctx.id,
        [m["id"] for m in messages],
    )

    by_message: dict[str, list[dict[str, Any]]] = {}
    for src in sources:
        by_message.setdefault(str(src["message_id"]), []).append(
            {
                "marker": src["marker"],
                "chunk_id": str(src["chunk_id"]),
                "source_kind": src["source_kind"],
                "source_title": src["source_title"],
                "quote": src["quote"],
                "page": src["page"],
                "heading_path": src["heading_path"],
                "score": src["score"],
            }
        )

    return {
        "id": str(conversation["id"]),
        "title": conversation["title"],
        "created_at": conversation["created_at"],
        "updated_at": conversation["updated_at"],
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"],
                "grounded": m["grounded"],
                "model": m["model"],
                "latency_ms": m["latency_ms"],
                "feedback": m["feedback"],
                "created_at": m["created_at"],
                "citations": by_message.get(str(m["id"]), []),
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}", status_code=204, response_model=None)
async def delete_conversation(conversation_id: str, ctx: WorkspaceCtx, conn: UserConn) -> None:
    result = await conn.execute(
        "delete from public.conversations where id=$1 and workspace_id=$2",
        conversation_id,
        ctx.id,
    )
    if not str(result).endswith("1"):
        raise NotFound("Conversation not found")

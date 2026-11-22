from __future__ import annotations

import json
from collections.abc import AsyncIterator

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.ai.providers import LLM, Embedder
from app.core.config import Settings, get_settings
from app.core.deps import current_user, get_embedder, get_llm, user_db
from app.core.security import AuthUser
from app.schemas.chat import ChatRequest, ChatResponse, CitationOut
from app.services import chat_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
    llm: LLM = Depends(get_llm),
) -> ChatResponse:
    message_id, conversation_id, answer = await chat_service.answer(
        conn,
        user_id=user.id,
        request=payload,
        embedder=embedder,
        llm=llm,
        settings=settings,
    )
    return ChatResponse(
        message_id=message_id,
        conversation_id=conversation_id,
        content=answer.content,
        grounded=answer.grounded,
        citations=[
            CitationOut(
                marker=c.marker,
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                document_title=c.document_title,
                quote=c.quote,
                page=c.page,
                heading_path=c.heading_path,
                score=c.score,
            )
            for c in answer.citations
        ],
    )


@router.post("/stream")
async def chat_stream(
    payload: ChatRequest,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
    llm: LLM = Depends(get_llm),
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event, data in chat_service.stream_answer(
                conn,
                user_id=user.id,
                request=payload,
                embedder=embedder,
                llm=llm,
                settings=settings,
            ):
                yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
        except Exception as exc:  # surface, never swallow
            error_payload = json.dumps({"code": "internal_error", "message": str(exc)[:300]})
            yield f"event: error\ndata: {error_payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

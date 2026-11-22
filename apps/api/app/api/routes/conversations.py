from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.core.deps import current_user, user_db
from app.core.errors import NotFound
from app.core.security import AuthUser

router = APIRouter(prefix="/conversations", tags=["conversations"])
messages_router = APIRouter(prefix="/messages", tags=["conversations"])


class ConversationUpdate(BaseModel):
    title: str | None = None
    archived: bool | None = None


class FeedbackIn(BaseModel):
    helpful: bool
    reason: str | None = None


@router.get("", response_model=list[dict[str, Any]])
async def list_conversations(
    limit: int = 30,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select id, title, created_at, updated_at from public.conversations
           where user_id=$1 and archived_at is null
           order by updated_at desc limit $2""",
        user.id,
        limit,
    )
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/{conversation_id}", response_model=dict)
async def get_conversation(
    conversation_id: str,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> dict[str, Any]:
    conversation = await conn.fetchrow(
        """select id, title, created_at, updated_at from public.conversations
           where id=$1 and user_id=$2""",
        conversation_id,
        user.id,
    )
    if conversation is None:
        raise NotFound("Conversation not found")

    messages = await conn.fetch(
        """select id, role, content, grounded, created_at from public.messages
           where conversation_id=$1 order by created_at""",
        conversation_id,
    )

    citations = await conn.fetch(
        """select message_id, marker, chunk_id, document_id, document_title, quote, page,
                  heading_path, score
           from public.message_citations where message_id = any($1::uuid[])
           order by marker""",
        [m["id"] for m in messages],
    )
    by_message: dict[str, list[dict[str, Any]]] = {}
    for c in citations:
        by_message.setdefault(str(c["message_id"]), []).append(
            {
                "marker": c["marker"],
                "chunk_id": str(c["chunk_id"]),
                "document_id": str(c["document_id"]),
                "document_title": c["document_title"],
                "quote": c["quote"],
                "page": c["page"],
                "heading_path": c["heading_path"],
                "score": float(c["score"]) if c["score"] is not None else None,
            }
        )

    return {
        "id": str(conversation["id"]),
        "title": conversation["title"],
        "created_at": conversation["created_at"].isoformat(),
        "updated_at": conversation["updated_at"].isoformat(),
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"],
                "grounded": m["grounded"],
                "created_at": m["created_at"].isoformat(),
                "citations": by_message.get(str(m["id"]), []),
            }
            for m in messages
        ],
    }


@router.patch("/{conversation_id}", response_model=dict)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> dict[str, Any]:
    record = await conn.fetchrow(
        """update public.conversations
           set title = coalesce($3, title),
               archived_at = case when $4::boolean then now() else archived_at end
           where id=$1 and user_id=$2 returning id, title""",
        conversation_id,
        user.id,
        payload.title,
        payload.archived is True,
    )
    if record is None:
        raise NotFound("Conversation not found")
    return {"id": str(record["id"]), "title": record["title"]}


@router.delete("/{conversation_id}", status_code=204, response_class=Response, response_model=None)
async def delete_conversation(
    conversation_id: str,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> None:
    result = await conn.execute(
        "delete from public.conversations where id=$1 and user_id=$2", conversation_id, user.id
    )
    if not result.endswith("1"):
        raise NotFound("Conversation not found")


@messages_router.post(
    "/{message_id}/feedback", status_code=204, response_class=Response, response_model=None
)
async def message_feedback(
    message_id: str,
    payload: FeedbackIn,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> None:
    result = await conn.execute(
        """update public.messages set helpful=$3, feedback_reason=$4
           where id=$1 and user_id=$2""",
        message_id,
        user.id,
        payload.helpful,
        payload.reason,
    )
    if not result.endswith("1"):
        raise NotFound("Message not found")

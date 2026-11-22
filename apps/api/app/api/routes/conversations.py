from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.deps import UserConn, WorkspaceCtx
from app.core.errors import NotFound

router = APIRouter(prefix="/workspaces/{workspace_id}/conversations", tags=["conversations"])
messages_router = APIRouter(prefix="/workspaces/{workspace_id}/messages", tags=["conversations"])


class ConversationUpdate(BaseModel):
    title: str | None = None


class FeedbackIn(BaseModel):
    helpful: bool
    reason: str | None = None


@router.get("", response_model=list[dict[str, Any]])
async def list_conversations(
    ctx: WorkspaceCtx,
    conn: UserConn,
    limit: int = Query(default=30, ge=1, le=100),
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select c.id, c.title, c.created_at, c.updated_at,
                  (select count(*) from public.chat_messages m
                    where m.conversation_id = c.id) as message_count
           from public.conversations c
           where c.workspace_id=$1
           order by c.updated_at desc limit $2""",
        ctx.id,
        limit,
    )
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "message_count": int(r["message_count"]),
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


@router.patch("/{conversation_id}", response_model=dict)
async def rename_conversation(
    conversation_id: str, payload: ConversationUpdate, ctx: WorkspaceCtx, conn: UserConn
) -> dict[str, Any]:
    record = await conn.fetchrow(
        """update public.conversations set title=$3
           where id=$1 and workspace_id=$2 returning id, title, updated_at""",
        conversation_id,
        ctx.id,
        payload.title,
    )
    if record is None:
        raise NotFound("Conversation not found")
    return {
        "id": str(record["id"]),
        "title": record["title"],
        "updated_at": record["updated_at"].isoformat(),
    }


@router.delete("/{conversation_id}", status_code=204, response_model=None)
async def delete_conversation(conversation_id: str, ctx: WorkspaceCtx, conn: UserConn) -> None:
    result = await conn.execute(
        "delete from public.conversations where id=$1 and workspace_id=$2",
        conversation_id,
        ctx.id,
    )
    if not str(result).endswith("1"):
        raise NotFound("Conversation not found")


@messages_router.post("/{message_id}/feedback", response_model=dict)
async def set_feedback(
    message_id: str, payload: FeedbackIn, ctx: WorkspaceCtx, conn: UserConn
) -> dict[str, Any]:
    """Feedback is a rating on the answer, not a retraining signal.

    Stored so the *retrieval* can be inspected later — a thumbs-down on a
    grounded answer usually means the right passage was not retrieved.
    """
    value = "helpful" if payload.helpful else "unhelpful"
    record = await conn.fetchrow(
        """update public.chat_messages set feedback=$3
           where id=$1 and workspace_id=$2 and role='assistant'
           returning id, feedback""",
        message_id,
        ctx.id,
        value,
    )
    if record is None:
        raise NotFound("Message not found")
    return {"id": str(record["id"]), "feedback": record["feedback"]}

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.deps import UserConn, WorkspaceCtx

# Users may read the queue (it explains why their file is still processing) but
# never write to it. Enqueueing is a server-side decision, enforced by grants in
# migration 0011 and by the RLS policies in 0007.
router = APIRouter(prefix="/workspaces/{workspace_id}/jobs", tags=["jobs"])


@router.get("", response_model=list[dict[str, Any]])
async def list_jobs(
    ctx: WorkspaceCtx,
    conn: UserConn,
    status: str | None = None,
    kind: str | None = None,
    source_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select id, note_id, document_id, type, status, attempts, max_attempts,
                  error_message, completed_at, run_after, created_at, updated_at
           from public.processing_jobs
           where workspace_id=$1
             and ($2::text is null or status=$2)
             and ($3::text is null or type=$3)
             and ($4::uuid is null or note_id=$4 or document_id=$4)
           order by created_at desc limit $5""",
        ctx.id,
        status,
        kind,
        source_id,
        limit,
    )
    return [
        {
            "id": str(r["id"]),
            "note_id": str(r["note_id"]) if r["note_id"] else None,
            "document_id": str(r["document_id"]) if r["document_id"] else None,
            "target_kind": "note" if r["note_id"] else ("document" if r["document_id"] else None),
            "type": r["type"],
            "status": r["status"],
            "attempts": r["attempts"],
            "max_attempts": r["max_attempts"],
            "error_message": r["error_message"],
            "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            "run_after": r["run_after"].isoformat(),
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]

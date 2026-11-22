from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends

from app.core.deps import current_user, user_db
from app.core.security import AuthUser

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[dict[str, Any]])
async def list_jobs(
    status: str | None = None,
    document_id: str | None = None,
    limit: int = 50,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select id, document_id, job_type, status, attempts, max_attempts,
                  last_error, run_after, created_at, updated_at
           from public.jobs
           where user_id=$1
             and ($2::text is null or status=$2)
             and ($3::uuid is null or document_id=$3)
           order by created_at desc limit $4""",
        user.id,
        status,
        document_id,
        limit,
    )
    return [
        {
            "id": str(r["id"]),
            "document_id": str(r["document_id"]) if r["document_id"] else None,
            "job_type": r["job_type"],
            "status": r["status"],
            "attempts": r["attempts"],
            "max_attempts": r["max_attempts"],
            "last_error": r["last_error"],
            "run_after": r["run_after"].isoformat(),
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]

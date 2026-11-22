"""Workspace resolution and role checks.

Every tenant-scoped operation passes through here. Two rules:

  * A caller who is not a member gets `NotFound`, not `Forbidden`. Confirming
    that a workspace exists would leak its existence.
  * Role requirements are explicit (`require_write`, `require_manage`) so an
    endpoint's access level is stated in one line rather than implied by which
    SQL it happens to run.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from app.core.errors import Forbidden, NotFound

ROLE_RANK = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}


@dataclass(frozen=True)
class WorkspaceContext:
    id: str
    name: str
    slug: str
    role: str
    owner_id: str
    personal: bool

    @property
    def can_write(self) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK["member"]

    @property
    def can_manage(self) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK["admin"]

    @property
    def is_viewer(self) -> bool:
        return self.role == "viewer"


async def resolve_workspace(
    conn: asyncpg.Connection, *, user_id: str, workspace_id: str
) -> WorkspaceContext:
    """Load a workspace the caller belongs to, or raise NotFound."""
    row = await conn.fetchrow(
        """select w.id, w.name, w.slug, w.owner_id, w.personal, wm.role
           from public.workspaces w
           join public.workspace_members wm
             on wm.workspace_id = w.id and wm.user_id = $2
           where w.id = $1""",
        workspace_id,
        user_id,
    )
    if row is None:
        raise NotFound("Workspace not found")

    return WorkspaceContext(
        id=str(row["id"]),
        name=row["name"],
        slug=row["slug"],
        role=row["role"],
        owner_id=str(row["owner_id"]),
        personal=row["personal"],
    )


async def list_workspaces(conn: asyncpg.Connection, *, user_id: str) -> list[WorkspaceContext]:
    rows = await conn.fetch(
        """select w.id, w.name, w.slug, w.owner_id, w.personal, wm.role,
                  (select count(*) from public.workspace_members m
                    where m.workspace_id = w.id) as member_count
           from public.workspaces w
           join public.workspace_members wm
             on wm.workspace_id = w.id and wm.user_id = $1
           order by w.personal desc, w.created_at""",
        user_id,
    )
    return [
        WorkspaceContext(
            id=str(r["id"]),
            name=r["name"],
            slug=r["slug"],
            role=r["role"],
            owner_id=str(r["owner_id"]),
            personal=r["personal"],
        )
        for r in rows
    ]


async def default_workspace(conn: asyncpg.Connection, *, user_id: str) -> WorkspaceContext:
    """The caller's personal workspace, falling back to their first membership."""
    rows = await list_workspaces(conn, user_id=user_id)
    if not rows:
        raise NotFound("No workspace is available for this account")
    return next((w for w in rows if w.personal), rows[0])


def require_write(ctx: WorkspaceContext) -> None:
    if not ctx.can_write:
        raise Forbidden("Your role in this workspace is read-only", code="insufficient_role")


def require_manage(ctx: WorkspaceContext) -> None:
    if not ctx.can_manage:
        raise Forbidden("Only workspace owners and admins can do that", code="insufficient_role")

"""Workspace lifecycle and membership.

Tenancy is a first-class resource now, so it gets real endpoints rather than
being an implicit property of the token. Every route here except `list` and
`create` is scoped to a workspace the caller already belongs to.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse

from app.core import audit, tenancy
from app.core.deps import CurrentUser, UserConn, WorkspaceCtx
from app.core.errors import Conflict, NotFound, Unprocessable
from app.schemas.common import (
    MemberInvite,
    MemberOut,
    MemberRoleUpdate,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return (slug or "workspace")[:50] + "-" + uuid.uuid4().hex[:6]


@router.get("", response_model=list[WorkspaceOut])
async def list_my_workspaces(
    user: CurrentUser,
    conn: UserConn,
) -> list[WorkspaceOut]:
    rows = await conn.fetch(
        """select w.id, w.name, w.slug, w.owner_id, w.personal, wm.role,
                  (select count(*) from public.workspace_members m
                    where m.workspace_id = w.id) as member_count
           from public.workspaces w
           join public.workspace_members wm
             on wm.workspace_id = w.id and wm.user_id = $1
           order by w.personal desc, w.created_at""",
        user.id,
    )
    return [
        WorkspaceOut(
            id=str(r["id"]),
            name=r["name"],
            slug=r["slug"],
            role=r["role"],
            owner_id=str(r["owner_id"]),
            personal=r["personal"],
            member_count=int(r["member_count"]),
        )
        for r in rows
    ]


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    payload: WorkspaceCreate,
    user: CurrentUser,
    conn: UserConn,
) -> WorkspaceOut:
    workspace_id = str(uuid.uuid4())
    record = await conn.fetchrow(
        """insert into public.workspaces (id, name, slug, owner_id, personal)
           values ($1,$2,$3,$4,false) returning *""",
        workspace_id,
        payload.name.strip(),
        _slugify(payload.name),
        user.id,
    )
    await conn.execute(
        """insert into public.workspace_members (workspace_id, user_id, role)
           values ($1,$2,'owner')""",
        workspace_id,
        user.id,
    )
    return WorkspaceOut(
        id=str(record["id"]),
        name=record["name"],
        slug=record["slug"],
        role="owner",
        owner_id=str(record["owner_id"]),
        personal=record["personal"],
        member_count=1,
    )


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(ctx: WorkspaceCtx, conn: UserConn) -> WorkspaceOut:
    member_count = await conn.fetchval(
        "select count(*) from public.workspace_members where workspace_id=$1", ctx.id
    )
    return WorkspaceOut(
        id=ctx.id,
        name=ctx.name,
        slug=ctx.slug,
        role=ctx.role,  # type: ignore[arg-type]
        owner_id=ctx.owner_id,
        personal=ctx.personal,
        member_count=int(member_count or 1),
    )


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    payload: WorkspaceUpdate, ctx: WorkspaceCtx, conn: UserConn
) -> WorkspaceOut:
    tenancy.require_manage(ctx)
    if payload.name is None:
        member_count = await conn.fetchval(
            "select count(*) from public.workspace_members where workspace_id=$1", ctx.id
        )
        return WorkspaceOut(
            id=ctx.id,
            name=ctx.name,
            slug=ctx.slug,
            role=ctx.role,  # type: ignore[arg-type]
            owner_id=ctx.owner_id,
            personal=ctx.personal,
            member_count=int(member_count or 1),
        )

    record = await conn.fetchrow(
        "update public.workspaces set name=$2 where id=$1 returning name, slug",
        ctx.id,
        payload.name.strip(),
    )
    member_count = await conn.fetchval(
        "select count(*) from public.workspace_members where workspace_id=$1", ctx.id
    )
    return WorkspaceOut(
        id=ctx.id,
        name=record["name"],
        slug=record["slug"],
        role=ctx.role,  # type: ignore[arg-type]
        owner_id=ctx.owner_id,
        personal=ctx.personal,
        member_count=int(member_count or 1),
    )


@router.delete("/{workspace_id}", status_code=204, response_class=Response, response_model=None)
async def delete_workspace(ctx: WorkspaceCtx, conn: UserConn) -> Response:
    # Only the owner may delete, and never their personal workspace: that would
    # leave the account with nowhere to put knowledge.
    if ctx.role != "owner":
        from app.core.errors import Forbidden

        raise Forbidden("Only the workspace owner can delete it", code="insufficient_role")
    if ctx.personal:
        raise Unprocessable("Your personal workspace cannot be deleted", code="personal_workspace")
    await audit.record(
        conn,
        workspace_id=ctx.id,
        action="workspace.delete",
        target_type="workspace",
        target_id=ctx.id,
        target_label=ctx.name,
    )
    await conn.execute("delete from public.workspaces where id=$1", ctx.id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Audit trail (security §7, rule 10)
# ---------------------------------------------------------------------------


@router.get("/{workspace_id}/audit", response_model=dict)
async def list_audit_events(
    ctx: WorkspaceCtx,
    conn: UserConn,
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None, description="Keyset cursor: created_at"),
    action: str | None = Query(default=None, description="Filter to one action prefix"),
    target_id: str | None = None,
) -> dict[str, Any]:
    """Recent sensitive operations, newest first.

    Admin-only, enforced twice: `require_manage` here and
    `can_manage_workspace` in the RLS select policy. A member asking is a 403
    rather than an empty list, because "you cannot see this" and "nothing has
    happened" are different answers and returning the latter is a lie.

    Keyset pagination on `(created_at, id)` rather than OFFSET: an audit table
    only grows at the tail, so an offset would drift under concurrent writes and
    silently skip rows — the one failure mode an audit view cannot have.
    """
    tenancy.require_manage(ctx)

    rows = await conn.fetch(
        """select a.id, a.action, a.target_type, a.target_id, a.target_label,
                  a.detail, a.request_id, a.created_at, a.actor_id,
                  p.display_name as actor_name
           from public.audit_events a
           left join public.profiles p on p.id = a.actor_id
           where a.workspace_id = $1
             and ($2::timestamptz is null or a.created_at < $2)
             and ($3::text is null or a.action like $3 || '%')
             and ($4::uuid is null or a.target_id = $4)
           order by a.created_at desc, a.id desc
           limit $5""",
        ctx.id,
        before,
        action,
        target_id,
        limit,
    )
    items = [
        {
            "id": str(r["id"]),
            "action": r["action"],
            "target_type": r["target_type"],
            "target_id": str(r["target_id"]) if r["target_id"] else None,
            "target_label": r["target_label"],
            "detail": r["detail"],
            "request_id": r["request_id"],
            "actor_id": str(r["actor_id"]) if r["actor_id"] else None,
            # A deleted account leaves the event with no name — shown as such
            # rather than dropped, since the action still happened.
            "actor_name": r["actor_name"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    next_cursor = items[-1]["created_at"] if len(items) == limit else None
    return {"items": items, "next_cursor": next_cursor}


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/{workspace_id}/members", response_model=list[MemberOut])
async def list_members(ctx: WorkspaceCtx, conn: UserConn) -> list[MemberOut]:
    rows = await conn.fetch(
        """select wm.user_id, wm.role, wm.joined_at, p.display_name
           from public.workspace_members wm
           left join public.profiles p on p.id = wm.user_id
           where wm.workspace_id=$1
           order by case wm.role when 'owner' then 0 when 'admin' then 1
                                 when 'member' then 2 else 3 end,
                    wm.joined_at""",
        ctx.id,
    )
    return [
        MemberOut(
            user_id=str(r["user_id"]),
            display_name=r["display_name"],
            email=None,  # never expose other users' emails through this route
            role=r["role"],
            joined_at=r["joined_at"],
        )
        for r in rows
    ]


@router.post("/{workspace_id}/members", response_model=MemberOut, status_code=201)
async def add_member(
    payload: MemberInvite, ctx: WorkspaceCtx, conn: UserConn, user: CurrentUser
) -> MemberOut | JSONResponse:
    """Add an existing NEXUS user by email.

    Deliberately narrow: this adds a known account, it does not send email
    invitations. An invite flow (token table, expiry, email delivery) is a
    separate feature — pretending otherwise would produce a UI promise the
    backend cannot keep.
    """
    tenancy.require_manage(ctx)

    target = await conn.fetchrow(
        "select id from auth.users where lower(email) = lower($1) limit 1", payload.email
    )
    if target is None:
        raise NotFound(
            "No NEXUS account uses that email yet. Invitations are not available.",
            code="user_not_found",
        )

    try:
        await conn.execute(
            """insert into public.workspace_members (workspace_id, user_id, role, invited_by)
               values ($1,$2,$3,$4)""",
            ctx.id,
            target["id"],
            payload.role,
            user.id,
        )
    except asyncpg.UniqueViolationError as exc:
        raise Conflict("That person is already a member", code="already_member") from exc

    await audit.record(
        conn,
        workspace_id=ctx.id,
        action="workspace.member.add",
        target_type="member",
        target_id=str(target["id"]),
        detail={"role": payload.role, "email": payload.email},
    )
    display = await conn.fetchval(
        "select display_name from public.profiles where id=$1", target["id"]
    )
    return MemberOut(
        user_id=str(target["id"]),
        display_name=display,
        email=None,
        role=payload.role,
        joined_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


@router.patch("/{workspace_id}/members/{member_id}", response_model=dict)
async def update_member_role(
    member_id: str, payload: MemberRoleUpdate, ctx: WorkspaceCtx, conn: UserConn
) -> dict[str, Any]:
    tenancy.require_manage(ctx)
    if member_id == ctx.owner_id:
        raise Unprocessable(
            "The owner's role cannot be changed; transfer ownership instead",
            code="cannot_modify_owner",
        )
    previous = await conn.fetchval(
        "select role from public.workspace_members where workspace_id=$1 and user_id=$2",
        ctx.id,
        member_id,
    )
    result = await conn.execute(
        "update public.workspace_members set role=$3 where workspace_id=$1 and user_id=$2",
        ctx.id,
        member_id,
        payload.role,
    )
    await audit.record(
        conn,
        workspace_id=ctx.id,
        action="workspace.member.role_change",
        target_type="member",
        target_id=member_id,
        detail={"previous_role": previous, "new_role": payload.role},
    )
    if not str(result).endswith("1"):
        raise NotFound("Member not found")
    return {"user_id": member_id, "role": payload.role}


@router.delete(
    "/{workspace_id}/members/{member_id}",
    status_code=204,
    response_class=Response,
    response_model=None,
)
async def remove_member(
    member_id: str, ctx: WorkspaceCtx, conn: UserConn, self_user: CurrentUser
) -> Response:
    """Remove a member, or leave yourself. The owner cannot be removed."""
    if member_id != ctx.id and not ctx.can_manage:
        from app.core.errors import Forbidden

        raise Forbidden("Only owners and admins can remove members", code="insufficient_role")
    if member_id == ctx.owner_id:
        raise Unprocessable("The workspace owner cannot be removed", code="cannot_remove_owner")
    # A member removing themselves is "leave"; an admin removing someone is
    # "remove". Same SQL, different fact — and an audit trail that cannot tell
    # them apart answers the wrong question.
    action = "workspace.member.leave" if member_id == self_user.id else "workspace.member.remove"
    result = await conn.execute(
        "delete from public.workspace_members where workspace_id=$1 and user_id=$2",
        ctx.id,
        member_id,
    )
    if not str(result).endswith("1"):
        raise NotFound("Member not found")
    await audit.record(
        conn,
        workspace_id=ctx.id,
        action=action,
        target_type="member",
        target_id=member_id,
    )
    return Response(status_code=204)

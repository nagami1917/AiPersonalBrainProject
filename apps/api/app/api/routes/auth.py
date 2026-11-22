"""Who am I, and what can I reach?

Clients need this once per cold start: the frontend has a Supabase session and
needs the user's identity, their workspaces, and their quota state in one call
rather than three. Doing it server-side also means the frontend never has to
interpret a JWT claim itself.

Auth (signup, login, OAuth, password reset, token refresh) belongs to Supabase and
is not implemented here — this module only reports what the verified token means.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core import tenancy
from app.core.deps import CurrentUser, UserConn

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def me(user: CurrentUser, conn: UserConn) -> dict[str, Any]:
    """Current user, their memberships, and which workspace is the default.

    `default_workspace_id` is returned because almost every other endpoint needs a
    workspace in the path; making the client guess which one to land on is how you
    get a first screen that shows an empty workspace the user did not choose.
    """
    profile = await conn.fetchrow(
        """select id, display_name, avatar_url, default_workspace_id
             from public.profiles where id=$1""",
        user.id,
    )
    workspaces = await tenancy.list_workspaces(conn, user_id=user.id)

    # profiles.default_workspace_id is the stored choice; the personal workspace is
    # only a fallback for a profile that has not been set up yet. Inferring "always
    # the personal one" would ignore a user who deliberately switched.
    stored = profile["default_workspace_id"] if profile else None
    default = next((w for w in workspaces if str(w.id) == str(stored)), None)
    if default is None:
        # No stored choice (or it is no longer reachable): fall back to the
        # personal workspace, then to any membership.
        default = next((w for w in workspaces if w.personal), workspaces[0] if workspaces else None)

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": profile["display_name"] if profile else None,
            "avatar_url": profile["avatar_url"] if profile else None,
        },
        "workspaces": [
            {"id": w.id, "name": w.name, "slug": w.slug, "role": w.role, "personal": w.personal}
            for w in workspaces
        ],
        "default_workspace_id": default.id if default else None,
    }

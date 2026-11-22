"""Supabase JWT verification and the two database clients.

Deliberately two clients (ARCHITECTURE.md §3):

  db_user   builds a connection whose `role` and `request.jwt.claim.sub` are set
            from the caller's token, so Postgres RLS applies to everything the
            API does on the user's behalf. A forgotten `WHERE user_id` in Python
            cannot cross tenants — the database refuses.
  db_admin  service role, used only for job enqueueing and worker operations.

Ownership is therefore checked twice, by two independent mechanisms.
"""

from __future__ import annotations

from typing import Any

from jose import JWTError, jwt

from app.core.config import Settings
from app.core.errors import Unauthorized

# Supabase signs access tokens with HS256 using the project JWT secret.
_ALGORITHMS = ("HS256",)


class AuthUser:
    __slots__ = ("id", "email", "role", "claims", "token")

    def __init__(self, claims: dict[str, Any], token: str) -> None:
        self.claims = claims
        self.token = token
        self.id: str = claims["sub"]
        self.email: str | None = claims.get("email")
        self.role: str = claims.get("role", "authenticated")


def decode_token(token: str, settings: Settings) -> AuthUser:
    try:
        claims = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=list(_ALGORITHMS),
            audience="authenticated",
            options={"verify_aud": True},
        )
    except JWTError as exc:  # expired, wrong signature, malformed
        raise Unauthorized("Invalid or expired token", code="unauthorized") from exc

    if not claims.get("sub"):
        raise Unauthorized("Token has no subject", code="unauthorized")

    return AuthUser(claims=claims, token=token)


async def user_connection(conn: Any, user: AuthUser) -> None:
    """Bind an asyncpg connection to the caller's identity for RLS.

    `set local` requires a transaction; callers wrap their work in one.
    """
    await conn.execute("select set_config('role', $1, true)", "authenticated")
    await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", user.id)
    await conn.execute("select set_config('request.jwt.claim.role', $1, true)", "authenticated")

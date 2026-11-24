"""Supabase JWT verification and the two database clients.

Deliberately two clients (ARCHITECTURE.md §3):

  db_user   builds a connection whose `role` and `request.jwt.claim.sub` are set
            from the caller's token, so Postgres RLS applies to everything the
            API does on the user's behalf. A forgotten workspace filter in Python
            cannot cross tenants — the database refuses.
  db_admin  service role, used only for job enqueueing and worker operations.

Ownership is therefore checked twice, by two independent mechanisms.

Note that identity and tenancy are different things: the token establishes WHO
is calling, and workspace membership (resolved in Postgres) establishes WHAT
they may touch. The API never derives authorisation from the token alone.
"""

from __future__ import annotations

from typing import Any

from jose import JWTError, jwt
from jose.exceptions import JOSEError

from app.core.config import Settings
from app.core.errors import Unauthorized
from app.core.jwks import signing_key


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
        if settings.supabase_jwt_algorithm == "ES256":
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "ES256" or not isinstance(kid, str) or not kid:
                raise JWTError("unexpected signing algorithm or missing key id")
            key = signing_key(settings.supabase_url, kid)
            claims = jwt.decode(
                token,
                key,
                algorithms=["ES256"],
                audience="authenticated",
                issuer=f"{settings.supabase_url.rstrip('/')}/auth/v1",
                options={"require_exp": True, "require_iss": True, "require_aud": True},
            )
        else:
            claims = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
                options={"verify_aud": True},
            )
    except (JOSEError, ValueError, TypeError) as exc:
        raise Unauthorized("Invalid or expired token", code="unauthorized") from exc

    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise Unauthorized("Token has no subject", code="unauthorized")

    # `verify_aud` alone is not enough: python-jose skips the audience check
    # entirely when the claim is absent, so a token with no `aud` at all was
    # accepted. Verified against jose directly before this check was added.
    # Every Supabase access token carries `aud: authenticated`, and a token
    # without it is not a user session — it must not be treated as one.
    if not claims.get("aud"):
        raise Unauthorized("Token has no audience", code="unauthorized")

    if settings.supabase_jwt_algorithm == "ES256" and claims.get("role") != "authenticated":
        raise Unauthorized("Invalid or expired token", code="unauthorized")

    return AuthUser(claims=claims, token=token)


async def user_connection(conn: Any, user: AuthUser) -> None:
    """Bind an asyncpg connection to the caller's identity for RLS.

    `set_config(..., true)` is transaction-scoped; callers wrap their work in a
    transaction (see core/deps.user_db).
    """
    await conn.execute("select set_config('role', $1, true)", "authenticated")
    await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", user.id)
    await conn.execute("select set_config('request.jwt.claim.role', $1, true)", "authenticated")

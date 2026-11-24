"""Signed URL creation.

The API never proxies bytes: it signs a short-lived URL and the browser talks to
Supabase Storage directly. Bucket policies independently enforce that the first
path segment equals the caller's user id (migration 0009).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ProviderUnavailable
from app.core.supabase_headers import storage_admin_headers


def _sign(settings: Settings, path: str, expires_in: int) -> dict[str, Any]:
    """Ask Storage to sign a path.

    A transport failure is a 503, not a 500. `httpx` raises on connection errors
    by default, and letting that escape the handler produces an unhandled
    exception: the client learns "something broke" with no retry guidance, and
    the error envelope the rest of the API guarantees is not the one it gets.
    Storage being unreachable is exactly `ProviderUnavailable`.
    """
    try:
        response = httpx.post(
            f"{settings.supabase_url}/storage/v1/object/sign/{settings.storage_bucket}/{path}",
            headers=storage_admin_headers(settings),
            json={"expiresIn": expires_in},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise ProviderUnavailable("Storage is unreachable") from exc

    if response.status_code >= 400:
        # The response body can contain a signed fragment or bucket internals;
        # the status is enough to debug and cannot leak.
        raise ProviderUnavailable("Could not create a signed URL for storage")
    payload: dict[str, Any] = response.json()
    return payload


def create_signed_upload_url(settings: Settings, storage_path: str) -> tuple[str, datetime]:
    """Returns (url, expiry).

    Note: Supabase's signed URL endpoint issues download URLs; uploads use the
    authenticated object endpoint with the user's token. We return the signed
    URL for the path and let the client PUT with its session token, which the
    storage policies validate against the path prefix.
    """
    data = _sign(settings, storage_path, settings.signed_upload_ttl_seconds)
    signed_path = data.get("signedURL") or data.get("signedUrl") or ""
    url = (
        signed_path
        if signed_path.startswith("http")
        else f"{settings.supabase_url}/storage/v1{signed_path}"
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.signed_upload_ttl_seconds)
    return url, expires_at


def create_signed_download_url(settings: Settings, storage_path: str) -> str:
    data = _sign(settings, storage_path, settings.signed_download_ttl_seconds)
    signed_path = data.get("signedURL") or data.get("signedUrl") or ""
    return (
        signed_path
        if signed_path.startswith("http")
        else f"{settings.supabase_url}/storage/v1{signed_path}"
    )

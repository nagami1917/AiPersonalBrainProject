"""Fetch Supabase's public signing keys, never from an untrusted JWT header.

A short cache bounds revocation delay. Unknown key IDs trigger a rate-limited
refresh to support rotation without allowing one network request per bad token.
Network or malformed-JWKS failures never fall back to an old or symmetric key.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx

from app.core.errors import ProviderUnavailable, Unauthorized

_CACHE_TTL = 60.0
_UNKNOWN_REFRESH_INTERVAL = 10.0
_MAX_RESPONSE_BYTES = 65536
_FAILURE_RETRY_INTERVAL = 5.0
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
_failure_until: dict[str, float] = {}


def _fetch(url: str) -> dict[str, dict[str, Any]]:
    try:
        with httpx.stream("GET", url, timeout=5.0, follow_redirects=False) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise ValueError("JWKS exceeds size limit")
        payload = json.loads(body)
        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list):
            raise ValueError("invalid JWKS")
        result: dict[str, dict[str, Any]] = {}
        for key in keys:
            if not isinstance(key, dict):
                continue
            kid = key.get("kid")
            if (
                isinstance(kid, str)
                and kid
                and key.get("kty") == "EC"
                and key.get("crv") == "P-256"
                and key.get("alg") in (None, "ES256")
                and key.get("use") in (None, "sig")
            ):
                if kid in result:
                    raise ValueError("duplicate JWKS key id")
                result[kid] = key
        return result
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ProviderUnavailable("Authentication signing keys are unavailable") from exc


def signing_key(supabase_url: str, kid: str) -> dict[str, Any]:
    url = f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    with _lock:
        now = time.monotonic()
        cached = _cache.get(url)
        if (
            cached is None
            or now - cached[0] >= _CACHE_TTL
            or (kid not in cached[1] and now - cached[0] >= _UNKNOWN_REFRESH_INTERVAL)
        ):
            if now < _failure_until.get(url, 0):
                raise ProviderUnavailable("Authentication signing keys are unavailable")
            try:
                keys = _fetch(url)
            except ProviderUnavailable:
                _failure_until.clear()
                _failure_until[url] = time.monotonic() + _FAILURE_RETRY_INTERVAL
                raise
            _failure_until.clear()
            _cache.clear()  # one configured project per process
            _cache[url] = (now, keys)
        else:
            keys = cached[1]
        key = keys.get(kid)
        if key is None:
            raise Unauthorized("Invalid or expired token", code="unauthorized")
        return key

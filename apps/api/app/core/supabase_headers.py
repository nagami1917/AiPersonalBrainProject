"""Headers for privileged Supabase Storage requests.

The new sb_secret keys are opaque, not JWTs: put them only in apikey. The
legacy service_role JWT needs its Bearer header for existing deployments.
"""

from app.core.config import Settings


def storage_admin_headers(settings: Settings) -> dict[str, str]:
    key = settings.supabase_service_role_key
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers

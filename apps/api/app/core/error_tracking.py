"""Optional Sentry error tracking.

`sentry_dsn` was previously read from configuration and never used: setting it
silenced nothing and reported nothing, which is worse than not having the
setting at all. A deployment that configured a DSN believed it had error
tracking; unhandled exceptions went to stdout and were lost when the machine
was replaced.

## Why optional, and why it must stay optional

Sentry is the one integration whose absence must never break the app. The DSN is
the only configuration here that talks to a third party on the error path, so:

  * no DSN set → this module does nothing at all, and does not import the SDK;
  * SDK not installed → logged once and ignored, so an image built without the
    extra dependency still boots;
  * `sentry_sdk.init` raising → caught, so a vendor problem cannot take the API
    down before it serves a single request.

The alternative — importing at module scope — turns an optional observability
tool into a hard startup dependency, and a bad DSN into an outage.

## What is deliberately filtered

Paths under `/health` and `/ready` are Fly's probes, once every 15 and 30
seconds. A transient database blip would otherwise generate thousands of
identical events per hour, which is how a project gets rate-limited by Sentry
and stops receiving the errors that matter. Probes already have their own signal
(the machine is restarted and `fly status` shows it).
"""

from __future__ import annotations

import logging

from sentry_sdk.types import Event, Hint  # type: ignore[import-not-found]

from app.core.config import Settings

log = logging.getLogger("nexus.sentry")

# Probe endpoints are high-frequency and self-reporting; keep them out of the
# error budget. Matched as a substring because FastAPI may append a query string
# and the deployed URL includes a scheme and host.
_IGNORED_PATH_SUBSTRINGS = ("/health", "/ready")


def init_error_tracking(settings: Settings) -> bool:
    """Initialise Sentry if a DSN is configured.

    Returns whether error tracking is active, so a caller (and a test) can assert
    on the outcome without reaching into the SDK's global state.
    """
    if not settings.sentry_dsn:
        log.info("error tracking disabled (SENTRY_DSN is empty)")
        return False

    try:
        import sentry_sdk
    except ImportError:
        # Deliberately a warning, not an exception: an image without the extra
        # dependency must still serve traffic.
        log.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; "
            "error tracking is disabled. Install sentry-sdk."
        )
        return False

    def _drop_probes(event: Event, hint: Hint) -> Event | None:
        del hint  # required by the SDK signature, unused here
        request = event.get("request")
        url = request.get("url") if isinstance(request, dict) else None
        if isinstance(url, str) and any(probe in url for probe in _IGNORED_PATH_SUBSTRINGS):
            return None
        return event

    try:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            release=settings.api_version,
            # There is no FastAPI/Starlette integration in this project's
            # dependency set, and adding one pulls in a second instrumentation
            # layer for a single-process API. The default integrations are
            # enough for unhandled exceptions, which is the actual gap.
            traces_sample_rate=0.0,
            before_send=_drop_probes,
        )
    except Exception as exc:  # noqa: BLE001 - observability must never block boot
        log.warning("Sentry initialisation failed (%s); error tracking is disabled", exc)
        return False

    log.info("error tracking enabled (environment=%s)", settings.environment)
    return True

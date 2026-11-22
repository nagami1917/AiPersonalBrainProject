"""Structured JSON logging + request correlation.

One line per request with method, path, status, duration, and request_id.
That plus /health is the MVP observability baseline; Prometheus/Grafana are
deliberately deferred (ADR-007).

## Redaction (security §7, rule 7)

Never log secrets or full private document contents. Two mechanisms, because
"do not log secrets" fails in two different ways:

  * **Structural** — a whitelist of `extra` fields. The request logger already
    does this; anything not on the list is dropped rather than serialised. A
    handler cannot accidentally log a whole request object.
  * **Textual** — `redact` scrubs the message string. This catches the case the
    whitelist cannot: `log.info(f"auth failed for {token}")`, where the secret is
    already inside the message. Patterns cover JWTs, bearer headers, Supabase
    and OpenAI key shapes, and anything after a `password=`.

Redaction is applied in the formatter, so it runs on every record from every
logger — including third-party ones that would otherwise write a URL with a
signed token in it. The signed-URL pattern matters specifically: storage URLs
carry `?token=`, and a framework logging a failed request URL would leak a
working download link into logs.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid

from fastapi import FastAPI, Request

REQUEST_ID_HEADER = "X-Request-Id"

REDACTED = "[redacted]"

# Ordered: the most specific shapes first, so a JWT is not partially matched by
# the generic bearer pattern and left half-intact.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # JWTs: header.payload.signature, all base64url.
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), REDACTED),
    # Authorization headers in any spelling.
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1" + REDACTED),
    # Supabase service-role and anon keys: 'sb_' prefixes plus long base64.
    (re.compile(r"\bsb_(?:secret|publishable)_[A-Za-z0-9_\-]{10,}"), REDACTED),
    # OpenAI-style keys.
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), REDACTED),
    # Signed storage URLs carry a token in the query string.
    (re.compile(r"(?i)([?&]token=)[^&\s\"']+"), r"\1" + REDACTED),
    # Explicit assignments.
    (
        re.compile(
            r"(?i)((?:password|passwd|secret|api[_-]?key|access[_-]?token|"
            r"service[_-]?role[_-]?key)\s*[=:]\s*)[^\s,;&\"']+"
        ),
        r"\1" + REDACTED,
    ),
)


def redact(text: str) -> str:
    """Scrub secret-shaped substrings from a log message."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            # Redaction runs on the rendered message, not the format string, so
            # values interpolated by any logger are covered.
            "msg": redact(record.getMessage()),
        }
        for key in ("request_id", "method", "path", "status", "duration_ms", "user_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            # Tracebacks quote local values and request URLs; redact them too.
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def install_request_logging(app: FastAPI) -> None:
    log = logging.getLogger("nexus.request")

    @app.middleware("http")
    async def _middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex[:16]}"
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

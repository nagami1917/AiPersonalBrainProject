"""Structured JSON logging + request correlation.

One line per request with method, path, status, duration, and request_id.
That plus /health is the MVP observability baseline; Prometheus/Grafana are
deliberately deferred (ADR-007).
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid

from fastapi import FastAPI, Request

REQUEST_ID_HEADER = "X-Request-Id"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status", "duration_ms", "user_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
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

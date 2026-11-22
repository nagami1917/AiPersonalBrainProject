"""Error envelope shared by every endpoint (API.md §1).

Every non-2xx response has the same shape, so the frontend has exactly one
error-rendering path. Ownership failures return 404, never 403: we do not
disclose that a resource exists to someone who does not own it.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class NexusError(Exception):
    """Base class: a domain error with an HTTP mapping."""

    status_code: int = 400
    code: str = "invalid_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}

    def payload(self, request_id: str) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "request_id": request_id,
            }
        }


class NotFound(NexusError):
    status_code = 404
    code = "not_found"


class Unauthorized(NexusError):
    status_code = 401
    code = "unauthorized"


class Forbidden(NexusError):
    status_code = 403
    code = "forbidden"


class Conflict(NexusError):
    status_code = 409
    code = "conflict"


class Unprocessable(NexusError):
    status_code = 422
    code = "unprocessable"


class PayloadTooLarge(NexusError):
    status_code = 413
    code = "file_too_large"


class RateLimited(NexusError):
    status_code = 429
    code = "rate_limited"


class ProviderUnavailable(NexusError):
    status_code = 503
    code = "provider_unavailable"


def error_response(request: Request, exc: NexusError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(status_code=exc.status_code, content=exc.payload(request_id))


def register_error_handlers(app) -> None:  # type: ignore[no-untyped-def]
    app.add_exception_handler(NexusError, error_response)  # type: ignore[arg-type]

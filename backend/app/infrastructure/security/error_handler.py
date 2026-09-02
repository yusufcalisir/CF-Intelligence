"""Production Error Sanitization & Exception Defense Handler.

Guarantees zero leakage of stack traces, internal file paths, database schemas,
or server runtime internals to client-side API responses in production environments.
Full error diagnostics and tracebacks are logged server-side with unique correlation
incident IDs (and dispatched to Sentry / error monitoring platforms).
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from app.config import get_settings

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

# Sentry integration helper (graceful optional import)
_sentry_available = False
try:
    import sentry_sdk  # type: ignore[import-untyped]

    _sentry_available = True
except ImportError:
    _sentry_available = False


def is_production_mode() -> bool:
    """Return True if running in a non-development or debug-disabled environment."""
    settings = get_settings()
    env = (getattr(settings, "app_env", "") or os.environ.get("APP_ENV", "development")).lower()
    debug = getattr(settings, "app_debug", True)
    return env in ("production", "prod", "staging") or not debug


def format_safe_error_response(
    request: Request,
    exc: Exception,
    status_code: int = 500,
) -> JSONResponse:
    """Format and return a sanitized error response conforming to RFC 7807 problem details.

    In production:
      - Strips stack traces, local paths, SQL table names, and internal exception details.
      - Returns a generic user-friendly message ("Something went wrong.").
      - Returns a unique incident ID for customer support correlation.

    Server-side:
      - Emits complete stack trace and diagnostic metadata to structured logging.
      - Captures exception in Sentry if configured.
    """
    incident_id = f"inc_{uuid.uuid4().hex[:12]}"
    client_ip = request.client.host if request.client else "127.0.0.1"
    method = request.method
    path = str(request.url.path)

    # 1. Server-side comprehensive error logging (NEVER suppressed)
    logger.error(
        "INTERNAL SERVER ERROR [Incident: %s] %s %s (Client IP: %s): %s",
        incident_id,
        method,
        path,
        client_ip,
        exc,
        exc_info=True,
        extra={
            "incident_id": incident_id,
            "path": path,
            "method": method,
            "client_ip": client_ip,
            "exception_type": type(exc).__name__,
        },
    )

    # 2. Sentry error monitoring integration (if active)
    if _sentry_available:
        try:
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("incident_id", incident_id)
                scope.set_tag("path", path)
                scope.set_tag("method", method)
                sentry_sdk.capture_exception(exc)
        except Exception as sentry_err:
            logger.warning("Failed to send exception to Sentry: %s", sentry_err)

    # 3. Content negotiation
    accept = request.headers.get("accept", "")
    media_type = (
        "application/problem+json"
        if "application/problem+json" in accept
        else "application/json"
    )

    # 4. Production vs Development response construction
    in_prod = is_production_mode()

    if in_prod:
        # Production: Strictly sanitize — generic message only
        problem_details: dict[str, Any] = {
            "type": "https://cfi-platform.org/errors/InternalServerError",
            "title": "Internal Server Error",
            "status": status_code,
            "detail": "Something went wrong. An unexpected internal error occurred. Please contact support with the incident ID.",
            "incident_id": incident_id,
            "instance": path,
        }
    else:
        # Development: Provide exception string for local developer productivity
        problem_details = {
            "type": f"https://cfi-platform.org/errors/{type(exc).__name__}",
            "title": "Internal Server Error (Development Mode)",
            "status": status_code,
            "detail": str(exc) or "An unhandled internal server error occurred.",
            "exception_type": type(exc).__name__,
            "incident_id": incident_id,
            "instance": path,
        }

    return JSONResponse(
        status_code=status_code,
        content=problem_details,
        media_type=media_type,
        headers={"X-Incident-ID": incident_id},
    )

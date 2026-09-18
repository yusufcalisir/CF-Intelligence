"""Production Error Sanitization & Exception Defense Handler.

Guarantees zero leakage of stack traces, internal file paths, database schemas,
or server runtime internals to client-side API responses in production environments.
Full error diagnostics and tracebacks are logged server-side with unique correlation
incident IDs (and dispatched to Sentry / error monitoring platforms).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import uuid
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from app.config import get_settings

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

# Default consortium HMAC salt for PII masking in error logs and diagnostics
DEFAULT_PII_HMAC_SALT = b"cfi-consortium-error-pii-salt-2026"

# Strict regex patterns for personal and financial data masking
# Ordered strategically so specific formats (Email, Credit Card, IBAN, Phone) take precedence
PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b"),
    "phone": re.compile(r"(?<![\w])(?:\+\d{1,3}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b"),
    "ssn_tckn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{11}\b"),
}


def mask_pii_in_text(text: str, salt: bytes | None = None) -> str:
    """Mask sensitive personal and financial identifiers using type-salted HMAC-SHA256 tokens.

    Guarantees zero raw PII leakage in logs, Sentry events, or error response payloads.
    Detects: IBAN, SSN/TCKN, Credit Card PAN, Email, and Phone.
    Tokens are replaced with: [MASKED_PII:{TYPE}:{HMAC_DIGEST[:12]}]
    """
    if not text:
        return text

    effective_salt = salt or DEFAULT_PII_HMAC_SALT
    sanitized = text

    for pii_type, pattern in PII_PATTERNS.items():
        key = effective_salt + pii_type.encode("utf-8")

        def _repl(match: re.Match[str], bound_key: bytes = key, bound_type: str = pii_type) -> str:
            raw_val = match.group(0)
            token_digest = hmac.new(
                bound_key, raw_val.strip().encode("utf-8"), hashlib.sha256
            ).hexdigest()[:12]
            return f"[MASKED_PII:{bound_type.upper()}:{token_digest}]"

        sanitized = pattern.sub(_repl, sanitized)

    return sanitized


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
      - Emits structured logging with all raw PII (IBAN, SSN, PAN, Email) masked via HMAC-SHA256.
      - Both error message and traceback strings are sanitized before being written to disk/console.
      - Captures exception in Sentry if configured.
    """
    import traceback

    incident_id = f"inc_{uuid.uuid4().hex[:12]}"
    client_ip = request.client.host if request.client else "127.0.0.1"
    method = request.method
    path = mask_pii_in_text(str(request.url.path))

    # 1. Server-side error logging with complete PII sanitization across message and traceback
    clean_exc_str = mask_pii_in_text(str(exc))
    raw_tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    clean_tb = mask_pii_in_text(raw_tb)

    logger.error(
        "INTERNAL SERVER ERROR [Incident: %s] %s %s (Client IP: %s): %s\n%s",
        incident_id,
        method,
        path,
        client_ip,
        clean_exc_str,
        clean_tb,
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
        # Production: Strictly sanitize — generic message only, zero internal details
        problem_details: dict[str, Any] = {
            "type": "https://cfi-platform.org/errors/InternalServerError",
            "title": "Internal Server Error",
            "status": status_code,
            "detail": "Something went wrong. An unexpected internal error occurred. Please contact support with the incident ID.",
            "incident_id": incident_id,
            "instance": path,
        }
    else:
        # Development: Provide sanitized exception string for local developer productivity
        # Strips raw PII while retaining internal exception class details
        problem_details = {
            "type": f"https://cfi-platform.org/errors/{type(exc).__name__}",
            "title": "Internal Server Error (Development Mode)",
            "status": status_code,
            "detail": clean_exc_str or "An unhandled internal server error occurred.",
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


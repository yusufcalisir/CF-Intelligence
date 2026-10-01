"""Granular Endpoint Rate Limiter based on slowapi and limits.

Provides real-client IP resolution compatible with Cloudflare (CF-Connecting-IP),
Vercel Edge (X-Forwarded-For, X-Real-IP), and local development.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any

from slowapi import Limiter
from starlette.responses import Response

if TYPE_CHECKING:
    from fastapi import Request, WebSocket


def get_real_client_ip(request: Request | WebSocket) -> str:
    """Extract real client IP considering trusted reverse proxy headers."""
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-real-ip")
        or (forwarded.split(",")[0].strip() if forwarded else None)
        or (request.client.host if request.client else "127.0.0.1")
    )
    return client_ip or "127.0.0.1"


# Patch Limiter._inject_headers so endpoints returning Pydantic models without
# an explicit Response parameter don't crash when headers_enabled=True.
_orig_inject_headers = Limiter._inject_headers


def _safe_inject_headers(self: Limiter, response: Any, current_limit: Any) -> Any:
    if response is None or not isinstance(response, Response):
        return response
    return _orig_inject_headers(self, response, current_limit)


Limiter._inject_headers = _safe_inject_headers  # type: ignore[assignment]


def is_benchmark_mode() -> bool:
    """Returns True ONLY when explicitly enabled via CFI_BENCHMARK_MODE environment variable.

    Default is strictly False.
    """
    return os.getenv("CFI_BENCHMARK_MODE", "").strip().lower() in ("1", "true", "yes")


def is_rate_limiter_enabled() -> bool:
    """Determines whether rate limiting is active.

    Active by default across production, development, and testing.
    Only disabled when explicitly launched in isolated benchmark inference capacity mode
    (Class B1) via CFI_BENCHMARK_MODE=1.
    """
    return not is_benchmark_mode()


def reset_rate_limiter() -> None:
    """Deterministic state reset for rate limiter storage between benchmark tiers/repetitions."""
    with contextlib.suppress(Exception):
        limiter.reset()


# Default rate limiter singleton: 120 reqs/minute global default.
# Enabled by default across all environments; disabled ONLY when CFI_BENCHMARK_MODE=1 is set.
limiter = Limiter(
    key_func=get_real_client_ip,
    default_limits=["120/minute"],
    headers_enabled=True,
    enabled=is_rate_limiter_enabled(),
)



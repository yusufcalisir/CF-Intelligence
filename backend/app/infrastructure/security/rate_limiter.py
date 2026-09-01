"""Granular Endpoint Rate Limiter based on slowapi and limits.

Provides real-client IP resolution compatible with Cloudflare (CF-Connecting-IP),
Vercel Edge (X-Forwarded-For, X-Real-IP), and local development.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from slowapi import Limiter

if TYPE_CHECKING:
    from fastapi import Request


def get_real_client_ip(request: Request) -> str:
    """Extract real client IP considering trusted reverse proxy headers."""
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-real-ip")
        or (forwarded.split(",")[0].strip() if forwarded else None)
        or (request.client.host if request.client else "127.0.0.1")
    )
    return client_ip or "127.0.0.1"


# Default rate limiter singleton: 120 reqs/minute global default
limiter = Limiter(
    key_func=get_real_client_ip,
    default_limits=["120/minute"],
    headers_enabled=False,
)



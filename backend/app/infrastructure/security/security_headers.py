"""HTTP Security Headers Middleware.

Injects industry-standard defensive HTTP response headers on every outgoing
response to prevent a broad class of client-side attacks:

  - Content-Security-Policy  : restricts permitted script/style/connect sources
  - Strict-Transport-Security: enforces HTTPS for 1 year + subdomains + preload
  - X-Content-Type-Options   : prevents MIME-type sniffing (nosniff)
  - X-Frame-Options          : blocks clickjacking via iframe embedding (DENY)
  - Referrer-Policy          : limits referrer leakage to same-origin only
  - Permissions-Policy       : disables powerful browser features not in use
  - X-XSS-Protection         : legacy IE/Edge XSS filter (belt-and-suspenders)

FastAPI / Starlette variant — wraps every response via BaseHTTPMiddleware.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

# ---------------------------------------------------------------------------
# Content-Security-Policy directive values
# ---------------------------------------------------------------------------
# API-only backend: no inline scripts or styles are served.
# WebSocket connections are allowed via wss:// (same-origin or explicit host).
# Keep it strict — the frontend is a separate Vite/Vercel SPA that sets its
# own CSP either via Vercel config or a meta tag.
_CSP_DIRECTIVES = "; ".join(
    [
        "default-src 'none'",
        "script-src 'none'",
        "style-src 'none'",
        "img-src 'none'",
        "font-src 'none'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ]
)

_DOCS_CSP_DIRECTIVES = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com https://unpkg.com",
        "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.jsdelivr.net https://scalar.com",
        "font-src 'self' https://fonts.gstatic.com data:",
        "connect-src 'self' https://cdn.jsdelivr.net https://* wss://*",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "base-uri 'self'",
    ]
)

_SECURITY_HEADERS: dict[str, str] = {
    # Prevents all external resource loading; this is an API backend
    "Content-Security-Policy": _CSP_DIRECTIVES,
    # HTTPS mandatory for 1 year; add subdomain coverage + signal preload-list readiness
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    # Block MIME-type sniffing attacks
    "X-Content-Type-Options": "nosniff",
    # Deny all iframe embedding — eliminates clickjacking surface
    "X-Frame-Options": "DENY",
    # No referrer information is sent cross-origin — minimises PII leakage
    "Referrer-Policy": "no-referrer",
    # Disable potentially sensitive browser features not required by this API
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    # Legacy IE/Edge XSS filter (belt-and-suspenders; modern browsers ignore it)
    "X-XSS-Protection": "1; mode=block",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware that appends security headers to every response.

    Existing headers set by individual routes are preserved; docs routes (/docs,
    /redoc, /scalar) receive tailored CSP headers allowing Swagger and Scalar CDN assets.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response: Response = await call_next(request)
        path = request.url.path
        is_docs_route = path in ("/openapi.json",) or any(
            path.startswith(p) for p in ("/docs", "/redoc", "/scalar")
        )

        headers_to_apply = dict(_SECURITY_HEADERS)
        if is_docs_route:
            headers_to_apply["Content-Security-Policy"] = _DOCS_CSP_DIRECTIVES

        for header, value in headers_to_apply.items():
            if header not in response.headers or (is_docs_route and header == "Content-Security-Policy"):
                response.headers[header] = value
        return response



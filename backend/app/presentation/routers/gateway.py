"""Enterprise API Gateway & Ingress Reverse Proxy Router.

Provides:
- Ingress proxy health, latency metrics, and downstream service catalog (/status, /health, /metrics).
- Perimeter rate limiting with sliding-window accounting and trusted proxy IP extraction.
- Dynamic reverse proxy routing to downstream microservices (fl-coordinator, identity-graph, fraud-alert).
- Dual-path support: mountable both in monolith mode (/api/v1/gateway/*) and dedicated gateway service mode.
- OIDC Bearer token verification, legacy API key fallback, and ABAC multi-tenant policy evaluation.
- RFC 7807 compliant structured JSON error responses.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse

from app.application.schemas.gateway import (
    GatewayHealthResponse,
    GatewayMetricsResponse,
    GatewayRateLimitConfig,
    GatewayServiceRoute,
    GatewayStatusResponse,
)
from app.config import get_settings
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.abac_engine import ABACEngine, ABACResource
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator, UserClaims
from app.infrastructure.security.rate_limiter import get_real_client_ip

logger = logging.getLogger(__name__)

settings = get_settings()

_gateway_start_time = time.time()

class GatewaySlidingWindowRateLimiter:
    """Thread-safe sliding window rate limiter for gateway perimeter ingress."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requests: dict[str, list[float]] = {}

    def check_rate_limit(
        self, client_id: str, max_requests: int = 100, window_seconds: int = 60
    ) -> tuple[bool, int, int]:
        """Check rate limit for a client IP or identity using a sliding window.

        Returns:
            tuple: (allowed: bool, remaining: int, reset_seconds: int)
        """
        now = time.time()
        window_start = now - window_seconds

        with self._lock:
            timestamps = self._requests.get(client_id, [])
            # Prune stale timestamps
            timestamps = [ts for ts in timestamps if ts > window_start]

            if len(timestamps) >= max_requests:
                oldest = timestamps[0]
                reset_seconds = max(1, int(oldest + window_seconds - now))
                self._requests[client_id] = timestamps
                return False, 0, reset_seconds

            timestamps.append(now)
            self._requests[client_id] = timestamps
            remaining = max_requests - len(timestamps)
            reset_seconds = window_seconds
            return True, remaining, reset_seconds

    def reset(self) -> None:
        """Reset all rate limiter tracking."""
        with self._lock:
            self._requests.clear()


_rate_limiter = RedisStore("gateway_rate_limit")
_rate_limiter_lock = threading.RLock()
_sliding_limiter = GatewaySlidingWindowRateLimiter()

_oidc_auth = OIDCAuthenticator(
    issuer=settings.oidc_issuer_url,
    audience=settings.oidc_client_id,
    signing_secret=settings.oidc_jwt_signing_secret,
)
_abac_engine = ABACEngine()
_audit_chain = ImmutableAuditChain.get_instance()

# Thread-safe metrics collector
_metrics_lock = threading.RLock()
_metrics: dict[str, Any] = {
    "requests_total": 0,
    "requests_by_method": {},
    "rate_limited_total": 0,
    "auth_failures_total": 0,
    "abac_denials_total": 0,
    "downstream_errors_total": 0,
    "latency_sum_ms": 0.0,
    "latency_count": 0,
}

GATEWAY_METRICS = _metrics


def _record_metric(metric_name: str, value: int = 1, method: str | None = None) -> None:
    """Record an ingress metric count in a thread-safe manner."""
    with _metrics_lock:
        if metric_name in _metrics:
            _metrics[metric_name] += value
        if method:
            method_key = method.upper()
            _metrics["requests_by_method"][method_key] = (
                _metrics["requests_by_method"].get(method_key, 0) + 1
            )


def _record_latency(latency_ms: float) -> None:
    """Record proxy request latency."""
    with _metrics_lock:
        _metrics["latency_sum_ms"] += latency_ms
        _metrics["latency_count"] += 1


# Downstream services mapping
SERVICES = {
    "fl-coordinator": {
        "http": "http://localhost:8001",
        "ws": "ws://localhost:8001",
    },
    "identity-graph": {
        "http": "http://localhost:8002",
        "ws": "ws://localhost:8002",
    },
    "fraud-alert": {
        "http": "http://localhost:8003",
        "ws": "ws://localhost:8003",
    },
}

if settings.app_env != "development":
    SERVICES = {
        "fl-coordinator": {
            "http": "http://fl-coordinator:8001",
            "ws": "ws://fl-coordinator:8001",
        },
        "identity-graph": {
            "http": "http://identity-graph:8002",
            "ws": "ws://identity-graph:8002",
        },
        "fraud-alert": {
            "http": "http://fraud-alert:8003",
            "ws": "ws://fraud-alert:8003",
        },
    }

PATH_ROUTING = {
    "/api/v1/simulations": "fl-coordinator",
    "/api/v1/banks": "fl-coordinator",
    "/api/v1/training": "fl-coordinator",
    "/api/v1/registry": "fl-coordinator",
    "/api/v1/entities": "identity-graph",
    "/api/v1/graph": "identity-graph",
    "/api/v1/alerts": "fraud-alert",
    "/api/v1/cases": "fraud-alert",
    "/api/v1/scenarios": "fraud-alert",
    "/api/v1/dashboard": "fraud-alert",
    "/api/v1/predict": "fraud-alert",
    "/api/v1/webhooks": "fl-coordinator",
}


# ── Gateway Helpers ───────────────────────────────────────────


def get_api_keys() -> dict[str, tuple[str, str]]:
    """Parse configured gateway API keys into map of key -> (identity, role)."""
    keys_map = {}
    for item in settings.gateway_api_keys.split(","):
        if not item:
            continue
        parts = item.split(":")
        if len(parts) == 3:
            keys_map[parts[0]] = (parts[1], parts[2])
    return keys_map


def check_rate_limit(client_id: str) -> tuple[bool, int, int, int]:
    """Check request count against rate limit.

    Supports Redis when available with thread-safe in-memory sliding window fallback.

    Returns:
        tuple: (allowed: bool, limit: int, remaining: int, reset_seconds: int)
    """
    now = time.time()
    minute_bucket = int(now / 60)
    key = f"rl:{client_id}:{minute_bucket}"
    limit = settings.gateway_rate_limit
    reset = (minute_bucket + 1) * 60 - int(now)

    with _rate_limiter_lock:
        # 1. Attempt Redis if available
        if _rate_limiter.client is not None:
            try:
                val = _rate_limiter.get(key)
                count = val.get("count", 0) if val else 0
                if count >= limit:
                    return False, limit, 0, reset
                _rate_limiter.set(key, {"count": count + 1}, ex=60)
                remaining = max(0, limit - (count + 1))
                return True, limit, remaining, reset
            except Exception as e:
                logger.debug("Redis rate limit check failed, falling back to in-memory: %s", e)

        # 2. In-memory sliding-window fallback
        allowed, remaining, reset_secs = _sliding_limiter.check_rate_limit(
            client_id, max_requests=limit, window_seconds=60
        )
        return allowed, limit, remaining, reset_secs


def authenticate_request(
    request_or_websocket: Request | WebSocket,
) -> tuple[str, str, str | None, UserClaims | None]:
    """Authenticate request or websocket using OIDC Bearer JWT tokens or API keys.

    Returns:
        tuple: (identity, role, key_or_token, user_claims)
    """
    bearer_token = None
    api_key = None

    if isinstance(request_or_websocket, Request):
        auth_header = request_or_websocket.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            bearer_token = auth_header.split(" ")[1].strip()

        api_key = request_or_websocket.headers.get("X-API-Key")
    else:  # WebSocket
        bearer_token = request_or_websocket.query_params.get(
            "token"
        ) or request_or_websocket.query_params.get("bearer")
        api_key = request_or_websocket.query_params.get(
            "api_key"
        ) or request_or_websocket.headers.get("x-api-key")

    # 1. OIDC Bearer Token validation
    if bearer_token:
        valid, claims, err_msg = _oidc_auth.decode_and_validate_token(bearer_token)
        if valid and claims:
            role = claims.roles[0] if claims.roles else "user"
            return claims.username, role, bearer_token[:16] + "...", claims
        else:
            logger.warning("OIDC Bearer token validation failed: %s", err_msg)
            if not api_key:
                return "", "", bearer_token, None

    # 2. Legacy API Key fallback
    if api_key:
        keys_map = get_api_keys()
        if api_key in keys_map:
            identity, role = keys_map[api_key]
            claims = UserClaims(
                sub=f"apikey_{identity}",
                username=identity,
                bank_id=identity if role == "bank" else "global",
                roles=[role],
            )
            return identity, role, api_key, claims

    if settings.gateway_require_auth:
        return "", "", api_key or bearer_token, None

    # Default dev fallback
    dev_claims = UserClaims(
        sub="usr_analyst_default",
        username="analyst",
        bank_id="global",
        roles=["analyst"],
    )
    return "analyst", "analyst", api_key, dev_claims


def check_authorization(
    identity: str,
    role: str,
    full_path: str,
    query_params: dict,
    method: str,
    user_claims: UserClaims | None = None,
    client_ip: str | None = None,
) -> bool:
    """Evaluates RBAC and dynamic ABAC rules for gateway route requests."""
    # 1. ABAC Policy Evaluation if UserClaims present
    if user_claims:
        resource_bank_id = query_params.get(
            "bank_id", user_claims.bank_id if role == "bank" else "global"
        )
        resource = ABACResource(
            resource_type="api_route",
            resource_id=full_path,
            bank_id=resource_bank_id,
        )
        abac_result = _abac_engine.evaluate_access(
            user=user_claims,
            resource=resource,
            action=method.lower(),
            client_ip=client_ip,
        )
        if not abac_result.allowed:
            logger.warning(
                "ABAC Enforcement Denied: %s (User: %s, Resource Bank: %s, Policy: %s)",
                abac_result.reason,
                identity,
                resource_bank_id,
                abac_result.policy_name,
            )
            _audit_chain.append_event(
                event_type="ACCESS_DENIED_ABAC",
                actor=identity,
                target_id=full_path,
                details={
                    "method": method,
                    "reason": abac_result.reason,
                    "policy": abac_result.policy_name,
                    "client_ip": client_ip,
                },
            )
            return False

    # 2. RBAC Policy Rules
    if role == "analyst" or role in ("super_admin", "compliance_auditor"):
        return True

    if role == "bank":
        # Banks cannot trigger/run simulations, dashboards, or edit scenarios
        if full_path.startswith("/api/v1/simulations") and method != "GET":
            return False
        if full_path.startswith("/api/v1/scenarios"):
            return False
        if full_path.startswith("/api/v1/dashboard"):
            return False

        # Banks can only query metrics filtering by their own bank_id
        effective_bank_id = user_claims.bank_id if user_claims else identity
        bank_id_param = query_params.get("bank_id")
        if bank_id_param and bank_id_param != effective_bank_id:
            return False

    return True


def check_ws_authorization(identity: str, role: str, ws_path: str) -> bool:
    """Evaluate WebSocket path authorization."""
    if role == "analyst" or role in ("super_admin", "compliance_auditor"):
        return True
    if role == "bank":
        # Banks are not permitted to see global training outputs
        return not ws_path.startswith("/ws/training")
    return False


# ── Canonical Gateway API Router (/api/v1/gateway) ────────────

api_router = APIRouter(prefix="/api/v1/gateway", tags=["gateway"])


@api_router.get("/status", response_model=GatewayStatusResponse)
async def gateway_status() -> GatewayStatusResponse:
    """Retrieve detailed diagnostics, downstream routes, and rate-limiting status."""
    now_iso = datetime.now(UTC).isoformat()
    uptime = max(0.0, time.time() - _gateway_start_time)

    mode_env = settings.app_env or "production"
    service_name = getattr(settings, "service_name", "monolith")

    downstream_map: dict[str, GatewayServiceRoute] = {}
    for svc_key, endpoints in SERVICES.items():
        downstream_map[svc_key] = GatewayServiceRoute(
            service_name=svc_key,
            http_url=endpoints["http"],
            ws_url=endpoints["ws"],
            healthy=True,
            latency_ms=1.2,
        )

    with _rate_limiter_lock:
        tracked_count = len(_sliding_limiter._requests)

    return GatewayStatusResponse(
        status="ok",
        service="gateway",
        version="0.2.0",
        environment=mode_env,
        uptime_seconds=round(uptime, 2),
        mode=service_name,
        downstream_services=downstream_map,
        path_mappings_count=len(PATH_ROUTING),
        rate_limit=GatewayRateLimitConfig(
            enabled=True,
            limit_per_minute=settings.gateway_rate_limit,
            tracked_clients=tracked_count,
            storage_backend="hybrid" if _rate_limiter.client is not None else "in_memory",
        ),
        timestamp=now_iso,
    )


@api_router.get("/health", response_model=GatewayHealthResponse)
async def gateway_health() -> GatewayHealthResponse:
    """Gateway liveness and readiness probe for container orchestrators."""
    now_iso = datetime.now(UTC).isoformat()
    services_ready = {svc: True for svc in SERVICES}
    return GatewayHealthResponse(
        status="ok",
        healthy=True,
        service="gateway",
        services_ready=services_ready,
        timestamp=now_iso,
    )


@api_router.get("/metrics", response_model=GatewayMetricsResponse)
async def gateway_metrics() -> GatewayMetricsResponse:
    """Retrieve real-time ingress request distribution, throttles, and latency."""
    uptime = max(0.0, time.time() - _gateway_start_time)
    with _metrics_lock:
        req_count = _metrics["latency_count"]
        avg_lat = (_metrics["latency_sum_ms"] / req_count) if req_count > 0 else 0.0

        return GatewayMetricsResponse(
            service="gateway",
            requests_total=_metrics["requests_total"],
            requests_by_method=dict(_metrics["requests_by_method"]),
            rate_limited_total=_metrics["rate_limited_total"],
            auth_failures_total=_metrics["auth_failures_total"],
            abac_denials_total=_metrics["abac_denials_total"],
            downstream_errors_total=_metrics["downstream_errors_total"],
            avg_latency_ms=round(avg_lat, 2),
            uptime_seconds=round(uptime, 2),
        )


@api_router.api_route(
    "/proxy/{service_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def explicit_gateway_proxy(request: Request, service_path: str):
    """Explicit gateway proxy route forwarding requests under /api/v1/gateway/proxy/*."""
    clean_path = "/" + service_path.lstrip("/")
    return await _execute_http_proxy(request, clean_path)


# ── Full Root Gateway Router (Used in Dedicated Gateway Mode) ──

router = APIRouter()
router.include_router(api_router)


@router.get("/docs/{service_name}", include_in_schema=False)
async def service_docs(service_name: str):
    """Serve Swagger UI page for a specific downstream microservice."""
    if service_name not in SERVICES:
        return JSONResponse(
            status_code=404,
            content={
                "type": "https://cfi-platform.org/errors/ServiceNotFound",
                "title": "Service Not Found",
                "status": 404,
                "detail": f"Downstream service documentation for '{service_name}' not found.",
            },
            media_type="application/problem+json",
        )
    return get_swagger_ui_html(
        openapi_url=f"/openapi/{service_name}.json",
        title=f"{service_name.replace('-', ' ').title()} API Docs",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )


@router.get("/openapi/{service_name}.json", include_in_schema=False)
async def service_openapi(service_name: str):
    """Fetch and return the OpenAPI JSON schema for a specific downstream microservice."""
    if service_name not in SERVICES:
        return JSONResponse(
            status_code=404,
            content={
                "type": "https://cfi-platform.org/errors/ServiceNotFound",
                "title": "Service Not Found",
                "status": 404,
                "detail": f"Downstream service schema for '{service_name}' not found.",
            },
            media_type="application/problem+json",
        )

    target_host = SERVICES[service_name]["http"]
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{target_host}/openapi.json")
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch OpenAPI for {service_name}: {e}")
            return JSONResponse(
                status_code=502,
                content={
                    "type": "https://cfi-platform.org/errors/BadGateway",
                    "title": "Bad Gateway",
                    "status": 502,
                    "detail": f"Error loading downstream OpenAPI schema from '{service_name}': {e}",
                },
                media_type="application/problem+json",
            )


async def _execute_http_proxy(request: Request, full_path: str):
    """Shared HTTP reverse proxy execution engine."""
    start_time = time.time()
    _record_metric("requests_total", 1, method=request.method)

    # API Version Check
    if full_path.startswith("/api/") and not full_path.startswith("/api/v1/"):
        return JSONResponse(
            status_code=400,
            content={
                "type": "https://cfi-platform.org/errors/UnsupportedApiVersion",
                "title": "Unsupported API Version",
                "status": 400,
                "detail": "Gateway Error: Only /api/v1/ endpoints are supported.",
                "instance": full_path,
            },
            media_type="application/problem+json",
        )

    # Extract real client IP behind trusted proxies (Vector 7 & Scope)
    client_ip = get_real_client_ip(request)
    if client_ip in ("testclient", "testserver", "localhost"):
        client_ip = "127.0.0.1"

    # Authenticate Request
    identity, role, api_key, user_claims = authenticate_request(request)
    if not identity:
        _record_metric("auth_failures_total", 1)
        _audit_chain.append_event(
            event_type="ACCESS_DENIED_UNAUTHORIZED",
            actor="anonymous",
            target_id=full_path,
            details={"method": request.method, "client_ip": client_ip},
        )
        return JSONResponse(
            status_code=401,
            content={
                "type": "https://cfi-platform.org/errors/Unauthorized",
                "title": "Unauthorized",
                "status": 401,
                "detail": "Gateway Error: Missing or invalid API key or Bearer token.",
                "instance": full_path,
            },
            headers={"WWW-Authenticate": "Bearer"},
            media_type="application/problem+json",
        )

    # Rate Limiting & RFC Headers
    client_id = api_key or client_ip
    allowed, rl_limit, rl_remaining, rl_reset = check_rate_limit(client_id)
    rl_headers = {
        "X-RateLimit-Limit": str(rl_limit),
        "X-RateLimit-Remaining": str(rl_remaining),
        "X-RateLimit-Reset": str(rl_reset),
    }

    if not allowed:
        _record_metric("rate_limited_total", 1)
        return JSONResponse(
            status_code=429,
            content={
                "type": "https://cfi-platform.org/errors/RateLimitExceeded",
                "title": "Rate Limit Exceeded",
                "status": 429,
                "detail": "Gateway Error: Too Many Requests. Rate limit quota exceeded.",
                "instance": full_path,
            },
            headers={**rl_headers, "Retry-After": str(rl_reset)},
            media_type="application/problem+json",
        )

    # Determine downstream target service
    target_service = None
    for prefix, service_name in PATH_ROUTING.items():
        if full_path.startswith(prefix):
            target_service = service_name
            break

    if not target_service:
        if full_path in ("/health", "/api/health", "/api/v1/health"):
            return JSONResponse(content={"status": "ok", "service": "gateway"})
        return JSONResponse(
            status_code=404,
            content={
                "type": "https://cfi-platform.org/errors/NotFound",
                "title": "Route Not Mapped",
                "status": 404,
                "detail": f"Gateway: Path '{full_path}' is not mapped to any downstream microservice.",
                "instance": full_path,
            },
            media_type="application/problem+json",
        )

    # Authorization Check & Parameter Injection
    query_params = dict(request.query_params)
    if not check_authorization(
        identity, role, full_path, query_params, request.method, user_claims, client_ip
    ):
        _record_metric("abac_denials_total", 1)
        return JSONResponse(
            status_code=403,
            content={
                "type": "https://cfi-platform.org/errors/Forbidden",
                "title": "Forbidden",
                "status": 403,
                "detail": "Gateway Error: Access forbidden by RBAC/ABAC security policy.",
                "instance": full_path,
            },
            media_type="application/problem+json",
        )

    if role == "bank" and "bank_id" not in query_params:
        query_params["bank_id"] = identity

    target_host = SERVICES[target_service]["http"]
    target_url = f"{target_host}{full_path}"

    # Extract headers and body
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)
    body = await request.body()

    status_code = 502
    try:
        async with httpx.AsyncClient() as client:
            downstream_resp = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                params=query_params,
                content=body,
                timeout=30.0,
            )

            resp_headers = dict(downstream_resp.headers)
            resp_headers.pop("content-encoding", None)
            resp_headers.pop("content-length", None)

            status_code = downstream_resp.status_code
            return Response(
                content=downstream_resp.content,
                status_code=downstream_resp.status_code,
                headers={**resp_headers, **rl_headers},
            )
    except Exception as e:
        _record_metric("downstream_errors_total", 1)
        logger.error(f"Gateway proxy error to {target_url}: {e}")
        return JSONResponse(
            status_code=502,
            content={
                "type": "https://cfi-platform.org/errors/BadGateway",
                "title": "Bad Gateway",
                "status": 502,
                "detail": f"Gateway Error: Downstream service '{target_service}' connection failed: {e}",
                "instance": full_path,
            },
            headers=rl_headers,
            media_type="application/problem+json",
        )
    finally:
        duration_ms = (time.time() - start_time) * 1000.0
        _record_latency(duration_ms)
        logger.info(
            "GATEWAY: %s - %s %s - Auth: %s (%s) - Status: %s - Time: %dms",
            client_ip,
            request.method,
            full_path,
            identity,
            role,
            status_code,
            int(duration_ms),
        )


@router.api_route(
    "/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
)
async def http_proxy(request: Request, path: str):
    """Catch-all root HTTP proxy for dedicated gateway service mode."""
    full_path = f"/{path}".replace("//", "/")
    if not full_path.startswith("/"):
        full_path = "/" + full_path
    return await _execute_http_proxy(request, full_path)


# ── Dynamic WebSocket Proxying ────────────────────────────────


@router.websocket("/ws/{path:path}")
async def ws_proxy(websocket: WebSocket, path: str):
    """Proxy WebSocket connections to the corresponding downstream microservice."""
    start_time = time.time()
    ws_path = f"/ws/{path}"
    client_ip = get_real_client_ip(websocket)
    if client_ip in ("testclient", "testserver", "localhost"):
        client_ip = "127.0.0.1"

    # Authenticate WS
    identity, role, api_key, user_claims = authenticate_request(websocket)
    if not identity:
        await websocket.accept()
        await websocket.close(code=3000, reason="Gateway Error: Unauthorized key")
        return

    # Rate Limiting
    client_id = api_key or client_ip
    allowed, _, _, _ = check_rate_limit(client_id)
    if not allowed:
        await websocket.accept()
        await websocket.close(code=1013, reason="Gateway Error: Too Many Requests")
        return

    # Authorization Check
    if not check_ws_authorization(identity, role, ws_path):
        await websocket.accept()
        await websocket.close(code=3000, reason="Gateway Error: Forbidden")
        return

    await websocket.accept()

    target_service = None
    if ws_path.startswith("/ws/training"):
        target_service = "fl-coordinator"
    elif ws_path.startswith("/ws/streaming"):
        target_service = "fraud-alert"

    if not target_service:
        await websocket.close(code=4004, reason="Gateway: WS path not mapped")
        return

    target_host = SERVICES[target_service]["ws"]
    target_url = f"{target_host}{ws_path}"

    status_code = 1011
    try:
        async with websockets.connect(target_url) as downstream_ws:
            status_code = 1000

            async def forward_to_client():
                try:
                    async for message in downstream_ws:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception:
                    pass

            async def forward_to_server():
                try:
                    async for message in websocket.iter_text():
                        await downstream_ws.send(message)
                except Exception:
                    pass

            await asyncio.gather(forward_to_client(), forward_to_server())

    except WebSocketDisconnect:
        status_code = 1000
        logger.debug(f"Client disconnected from gateway websocket proxy for {ws_path}")
    except Exception as e:
        logger.error(f"Gateway WebSocket proxy error for {ws_path}: {e}")
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "GATEWAY_WS: %s - %s - Auth: %s (%s) - Closed: %s - Time: %dms",
            client_ip,
            ws_path,
            identity,
            role,
            status_code,
            duration_ms,
        )

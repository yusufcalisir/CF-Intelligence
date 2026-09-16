"""Unit tests for Gateway routing, ingress health, telemetry metrics, and proxy behavior.

Covers:
- GET /api/v1/gateway/status (schema completeness, routing metadata, rate limiting config)
- GET /api/v1/gateway/health (liveness and component readiness)
- GET /api/v1/gateway/metrics (telemetry counters, method breakdowns, latency metrics)
- Reverse proxy edge cases: invalid API version (400), unmapped route (404), unauthenticated request (401)
- Rate limiting enforcement with RFC 7807 problem details and RFC headers
- Real client IP resolution through trusted reverse proxy headers (Cloudflare / X-Forwarded-For)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.presentation.routers.gateway import (
    GatewaySlidingWindowRateLimiter,
    get_real_client_ip,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_gateway_status_endpoint(client: TestClient) -> None:
    """Verify /api/v1/gateway/status returns complete schema with route table and rate limit config."""
    response = client.get("/api/v1/gateway/status")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "ok"
    assert data["service"] == "gateway"
    assert "version" in data
    assert "environment" in data
    assert "uptime_seconds" in data
    assert "mode" in data
    assert "timestamp" in data

    # Validate rate limit configuration
    rate_limit = data["rate_limit"]
    assert rate_limit["enabled"] is True
    assert rate_limit["limit_per_minute"] > 0
    assert "storage_backend" in rate_limit

    # Validate downstream services
    services = data["downstream_services"]
    assert isinstance(services, dict)
    assert len(services) > 0
    assert data["path_mappings_count"] > 0

    first_key = next(iter(services))
    first_svc = services[first_key]
    assert "service_name" in first_svc
    assert "http_url" in first_svc
    assert "ws_url" in first_svc
    assert "healthy" in first_svc


def test_gateway_health_endpoint(client: TestClient) -> None:
    """Verify /api/v1/gateway/health returns 200 with component statuses."""
    response = client.get("/api/v1/gateway/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "ok"
    assert data["healthy"] is True
    assert data["service"] == "gateway"
    assert "services_ready" in data
    assert isinstance(data["services_ready"], dict)


def test_gateway_metrics_endpoint(client: TestClient) -> None:
    """Verify /api/v1/gateway/metrics exposes real telemetry counters."""
    response = client.get("/api/v1/gateway/metrics")
    assert response.status_code == 200
    data = response.json()

    assert data["service"] == "gateway"
    assert "uptime_seconds" in data
    assert "requests_total" in data
    assert "requests_by_method" in data
    assert "rate_limited_total" in data
    assert "auth_failures_total" in data
    assert "abac_denials_total" in data
    assert "downstream_errors_total" in data
    assert "avg_latency_ms" in data
    assert isinstance(data["requests_by_method"], dict)


def test_proxy_invalid_api_version(client: TestClient) -> None:
    """Verify proxy returns RFC 7807 400 Bad Request when non-v1 API version requested."""
    response = client.get("/api/v1/gateway/proxy/api/v2/coordinator/status")
    assert response.status_code == 400
    assert response.headers.get("content-type") == "application/problem+json"
    data = response.json()
    assert data["type"] == "https://cfi-platform.org/errors/UnsupportedApiVersion"
    assert data["title"] == "Unsupported API Version"
    assert data["status"] == 400
    assert "v1" in data["detail"]


def test_proxy_unmapped_service_returns_404(client: TestClient) -> None:
    """Verify proxy returns RFC 7807 404 Not Found for unmapped endpoints."""
    response = client.get("/api/v1/gateway/proxy/api/v1/nonexistent_microservice/endpoint")
    assert response.status_code == 404
    assert response.headers.get("content-type") == "application/problem+json"
    data = response.json()
    assert data["type"] == "https://cfi-platform.org/errors/NotFound"
    assert data["title"] == "Route Not Mapped"
    assert data["status"] == 404


def test_proxy_missing_auth_returns_401(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify proxy returns RFC 7807 401 Unauthorized for protected endpoints without Bearer token."""
    settings = get_settings()
    monkeypatch.setattr(settings, "gateway_require_auth", True)

    response = client.get("/api/v1/gateway/proxy/api/v1/simulations")
    assert response.status_code == 401
    assert response.headers.get("content-type") == "application/problem+json"
    data = response.json()
    assert data["type"] == "https://cfi-platform.org/errors/Unauthorized"
    assert data["title"] == "Unauthorized"
    assert data["status"] == 401


def test_trusted_proxy_client_ip_extraction() -> None:
    """Verify real client IP resolution from trusted proxy headers."""
    # Test CF-Connecting-IP
    req_cf = MagicMock(spec=Request)
    req_cf.headers = {"cf-connecting-ip": "203.0.113.195"}
    req_cf.client = MagicMock(host="10.0.0.1")
    assert get_real_client_ip(req_cf) == "203.0.113.195"

    # Test X-Forwarded-For multi-hop (first IP is real client)
    req_xff = MagicMock(spec=Request)
    req_xff.headers = {"x-forwarded-for": "198.51.100.42, 10.0.0.2, 10.0.0.3"}
    req_xff.client = MagicMock(host="10.0.0.1")
    assert get_real_client_ip(req_xff) == "198.51.100.42"

    # Test X-Real-IP fallback
    req_xreal = MagicMock(spec=Request)
    req_xreal.headers = {"x-real-ip": "192.0.2.77"}
    req_xreal.client = MagicMock(host="10.0.0.1")
    assert get_real_client_ip(req_xreal) == "192.0.2.77"

    # Test direct client fallback
    req_direct = MagicMock(spec=Request)
    req_direct.headers = {}
    req_direct.client = MagicMock(host="172.16.0.5")
    assert get_real_client_ip(req_direct) == "172.16.0.5"


def test_sliding_window_rate_limiter_enforcement() -> None:
    """Verify in-memory sliding window rate limiter blocks excessive requests."""
    limiter = GatewaySlidingWindowRateLimiter()
    test_ip = "192.0.2.99"

    # First request should be allowed with max limit
    allowed, remaining, reset = limiter.check_rate_limit(test_ip, max_requests=3, window_seconds=60)
    assert allowed is True
    assert remaining == 2

    # Second request allowed
    allowed, remaining, _ = limiter.check_rate_limit(test_ip, max_requests=3, window_seconds=60)
    assert allowed is True
    assert remaining == 1

    # Third request allowed
    allowed, remaining, _ = limiter.check_rate_limit(test_ip, max_requests=3, window_seconds=60)
    assert allowed is True
    assert remaining == 0

    # Fourth request blocked
    allowed, remaining, _ = limiter.check_rate_limit(test_ip, max_requests=3, window_seconds=60)
    assert allowed is False
    assert remaining == 0
    assert reset > 0

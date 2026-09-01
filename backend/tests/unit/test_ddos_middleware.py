"""Unit tests for DDoSProtectionMiddleware memory pruning and rate limiting."""

import time
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from app.main import DDoSProtectionMiddleware


@pytest.mark.asyncio
async def test_ddos_middleware_memory_pruning():
    """Test that DDoSProtectionMiddleware automatically prunes expired IP records when size exceeds threshold."""
    app_mock = MagicMock()
    middleware = DDoSProtectionMiddleware(app_mock)

    # Artificially populate the internal tracking dictionary with expired IPs (>1000)
    now = time.time()
    expired_time = now - 20.0  # 20 seconds ago, beyond 10s window

    middleware._requests = {
        f"192.168.1.{i}": [expired_time] for i in range(1050)
    }
    # Add one active IP with fresh timestamp
    middleware._requests["10.0.0.1"] = [now - 1.0]

    assert len(middleware._requests) == 1051

    # Simulate an incoming request
    request = Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [],
            "client": ("10.0.0.2", 12345),
        }
    )

    async def call_next_mock(req):
        return MagicMock(status_code=200)

    await middleware.dispatch(request, call_next_mock)

    # After dispatch, expired IPs should have been pruned
    assert len(middleware._requests) <= 2  # Only active IP (10.0.0.1) and new IP (10.0.0.2)
    assert "10.0.0.1" in middleware._requests
    assert "10.0.0.2" in middleware._requests
    assert "192.168.1.0" not in middleware._requests


@pytest.mark.asyncio
async def test_ddos_middleware_rate_limiting():
    """Test that exceeding the rate limit returns HTTP 429."""
    app_mock = MagicMock()
    middleware = DDoSProtectionMiddleware(app_mock)

    client_ip = "192.168.99.1"
    now = time.time()
    # Populate with 100 requests in the current window
    middleware._requests = {
        client_ip: [now - 1.0] * 100
    }

    request = Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/api/v1/predict",
            "headers": [],
            "client": (client_ip, 54321),
        }
    )

    async def call_next_mock(req):
        return MagicMock(status_code=200)

    response = await middleware.dispatch(request, call_next_mock)
    assert response.status_code == 429

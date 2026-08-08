# ruff: noqa: E402
import importlib
import os

from fastapi.testclient import TestClient

# Force SERVICE_NAME to gateway so the app loads gateway router
os.environ["SERVICE_NAME"] = "gateway"

import app.main

importlib.reload(app.main)

gateway_app = app.main.app
from app.config import get_settings

client = TestClient(gateway_app)
settings = get_settings()


def test_gateway_versioning():
    # Unsupported version should return 400
    response = client.get("/api/v2/simulations")
    assert response.status_code == 400
    assert "Unsupported API Version" in response.text


def test_gateway_authentication():
    # Enable auth requirement for this test
    original_require_auth = settings.gateway_require_auth
    settings.gateway_require_auth = True
    try:
        # Request without key -> 401
        response = client.get("/api/v1/simulations")
        assert response.status_code == 401

        # Request with invalid key -> 401
        response = client.get("/api/v1/simulations", headers={"X-API-Key": "invalid_key"})
        assert response.status_code == 401

        # Request with valid key -> Success/Proxy error (not 401/403)
        response = client.get("/api/v1/simulations", headers={"X-API-Key": "key_analyst"})
        assert response.status_code not in (401, 403)
    finally:
        settings.gateway_require_auth = original_require_auth


def test_gateway_authorization():
    # A bank client tries to start a simulation -> 403 Forbidden
    response = client.post("/api/v1/simulations", json={}, headers={"X-API-Key": "key_bank_a"})
    assert response.status_code == 403

    # Bank A tries to request Bank B's alerts -> 403 Forbidden
    response = client.get("/api/v1/alerts?bank_id=bank_b", headers={"X-API-Key": "key_bank_a"})
    assert response.status_code == 403

    # Bank A requests Bank A's alerts -> Not 403 (could be 502/200 depending on downstream)
    response = client.get("/api/v1/alerts?bank_id=bank_a", headers={"X-API-Key": "key_bank_a"})
    assert response.status_code != 403


def test_gateway_rate_limiting():
    from unittest.mock import patch

    from app.infrastructure.redis_store import RedisStore
    from app.presentation.routers.gateway import _rate_limiter

    # Force in-memory fallback mode for test suite isolation
    RedisStore._global_redis_unavailable = True
    _rate_limiter.clear()

    original_rate_limit = settings.gateway_rate_limit
    original_require_auth = settings.gateway_require_auth
    settings.gateway_rate_limit = 2
    settings.gateway_require_auth = False
    try:
        with patch("time.time", return_value=1700000000.0):
            headers = {"X-API-Key": "rate_limit_test_key"}
            # First request -> OK (502 downstream or 200, not 429)
            response = client.get("/api/v1/simulations", headers=headers)
            assert response.status_code != 429

            # Second request -> OK (not 429)
            response = client.get("/api/v1/simulations", headers=headers)
            assert response.status_code != 429

            # Third request -> 429 Too Many Requests
            response = client.get("/api/v1/simulations", headers=headers)
            assert response.status_code == 429
            assert "Too Many Requests" in response.text
    finally:
        settings.gateway_rate_limit = original_rate_limit
        settings.gateway_require_auth = original_require_auth


# Clean up environment variable after tests run
if "SERVICE_NAME" in os.environ:
    del os.environ["SERVICE_NAME"]
importlib.reload(app.main)

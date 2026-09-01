"""Unit tests for slowapi granular rate limiting and real client IP resolution."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.security.rate_limiter import get_real_client_ip, limiter
from app.main import app, seed_mock_data


@pytest.fixture(scope="module", autouse=True)
def setup_seed():
    seed_mock_data()


@pytest.fixture
def client():
    # Reset limiter storage between tests to ensure clean state
    limiter.reset()
    return TestClient(app)


def test_real_ip_resolution_with_cloudflare_header():
    """Test that get_real_client_ip prioritizes CF-Connecting-IP over other headers."""
    from unittest.mock import MagicMock
    from fastapi import Request

    req = MagicMock(spec=Request)
    req.headers = {
        "cf-connecting-ip": "203.0.113.195",
        "x-real-ip": "198.51.100.2",
        "x-forwarded-for": "192.0.2.1, 10.0.0.1",
    }
    req.client = MagicMock(host="10.0.0.1")

    resolved_ip = get_real_client_ip(req)
    assert resolved_ip == "203.0.113.195"


def test_real_ip_resolution_with_x_forwarded_for():
    """Test that get_real_client_ip extracts first client IP from X-Forwarded-For when CF header absent."""
    from unittest.mock import MagicMock
    from fastapi import Request

    req = MagicMock(spec=Request)
    req.headers = {
        "x-forwarded-for": "198.51.100.55, 10.0.0.1",
    }
    req.client = MagicMock(host="10.0.0.1")

    resolved_ip = get_real_client_ip(req)
    assert resolved_ip == "198.51.100.55"


def test_predict_endpoint_rate_limiting_headers(client: TestClient):
    """Test that predict endpoint responds with standard rate limiting headers."""
    payload = {
        "transaction_amount": 150.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.0,
        "hour_of_day": 14,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
    }
    resp = client.post(
        "/api/v1/predict",
        json=payload,
        headers={"cf-connecting-ip": "198.51.100.77"},
    )
    assert resp.status_code == 200
    assert "X-RateLimit-Limit" in resp.headers or "x-ratelimit-limit" in resp.headers

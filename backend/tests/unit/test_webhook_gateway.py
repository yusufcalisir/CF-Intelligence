# ruff: noqa: E402
"""Automated Unit Test Suite for Public Product API & Developer Webhooks Gateway."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.webhook_service import (
    WebhookEventType,
    WebhookService,
)
from app.presentation.routers.webhook_gateway import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_webhook_subscription_registration() -> None:
    """Test registering a new developer webhook subscription via REST API."""
    payload = {
        "tenant_id": "bank_alpha",
        "target_url": "https://api.bank-alpha.com/webhooks/cfi",
        "events": ["ALERT_CREATED", "CASE_RESOLVED"],
    }

    response = client.post("/v1/webhooks/subscriptions", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["tenant_id"] == "bank_alpha"
    assert data["target_url"] == "https://api.bank-alpha.com/webhooks/cfi"
    assert data["subscription_id"].startswith("sub_")
    assert data["secret_key"].startswith("whsec_")
    assert len(data["events"]) == 2


def test_hmac_sha256_payload_signature_generation() -> None:
    """Test HMAC-SHA256 signature computation and payload verification."""
    service = WebhookService()
    secret = "whsec_test_secret_key_123"
    body = b'{"event":"ALERT_CREATED","id":"123"}'

    signature = service.compute_hmac_signature(secret, body)
    assert signature.startswith("sha256=")
    assert len(signature) == 7 + 64  # sha256= + 64 hex characters


def test_webhook_event_dispatching_and_signing() -> None:
    """Test dispatching signed webhook events to registered tenant subscribers."""
    # 1. Register subscription via API endpoint
    reg_resp = client.post(
        "/v1/webhooks/subscriptions",
        json={
            "tenant_id": "bank_beta",
            "target_url": "https://api.bank-beta.com/webhooks",
            "events": ["MODEL_PROMOTED"],
        },
    )
    assert reg_resp.status_code == 200

    # 2. Test API test-dispatch endpoint
    resp = client.post(
        "/v1/webhooks/test-dispatch",
        params={"tenant_id": "bank_beta", "event_type": "MODEL_PROMOTED"},
    )
    assert resp.status_code == 200
    assert resp.json()["dispatched_count"] >= 1
    assert resp.json()["sample_signature"].startswith("sha256=")


def test_webhook_url_validation() -> None:
    """Test validation of webhook URLs to reject invalid schemes."""
    assert WebhookService.validate_target_url("https://api.bank.com/webhook") is True
    assert WebhookService.validate_target_url("http://api.bank.com/webhook") is True
    assert WebhookService.validate_target_url("ftp://api.bank.com/webhook") is False
    assert WebhookService.validate_target_url("file:///etc/passwd") is False
    assert WebhookService.validate_target_url("") is False


@pytest.mark.asyncio
async def test_webhook_async_delivery_timeout_and_error_handling() -> None:
    """Test deliver_payload_async handles network errors and invalid URLs gracefully without crashing."""
    service = WebhookService()
    sub = service.register_subscription(
        tenant_id="bank_test",
        target_url="https://invalid-non-existent-webhook.example.com",
        events=[WebhookEventType.ALERT_CREATED],
    )
    deliveries = service.dispatch_event(
        tenant_id="bank_test",
        event_type=WebhookEventType.ALERT_CREATED,
        payload={"alert_id": "alt_123", "amount": 5000.0},
    )
    assert len(deliveries) == 1

    # Attempt delivery with 0.1s timeout to non-existent host -> must return False, no crash
    success = await service.deliver_payload_async(
        target_url=sub.target_url,
        delivery=deliveries[0],
        timeout=0.1,
    )
    assert success is False

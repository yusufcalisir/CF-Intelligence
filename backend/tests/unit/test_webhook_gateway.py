# ruff: noqa: E402
"""Automated Unit Test Suite for Public Product API & Developer Webhooks Gateway."""

from __future__ import annotations

import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.webhook_service import (
    WebhookEventType,
    WebhookService,
)
from app.presentation.routers.webhook_gateway import api_router, router

app = FastAPI()
app.include_router(router)
app.include_router(api_router)
client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_dns_for_test_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock DNS resolution for synthetic test domains to resolve to a public IP.

    Ensures that legitimate synthetic domains (e.g. api.bank.com) pass fail-closed
    DNS validation in test environments without real network DNS lookups.
    """
    real_getaddrinfo = socket.getaddrinfo

    def patched_getaddrinfo(host, port, *args, **kwargs):
        if host in (
            "api.bank.com",
            "api.bank-alpha.com",
            "api.bank-beta.com",
            "api.externalbank.com",
            "invalid-non-existent-webhook.example.com",
        ):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", patched_getaddrinfo)


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
    """Test validation of webhook URLs to reject invalid schemes, private subnets, and SSRF targets."""
    # Legitimate external URLs
    assert WebhookService.validate_target_url("https://api.bank.com/webhook") is True
    assert WebhookService.validate_target_url("http://api.bank.com/webhook") is True
    assert WebhookService.validate_target_url("https://api.externalbank.com/webhook") is True

    # Scheme traversal / invalid schemes
    assert WebhookService.validate_target_url("ftp://api.bank.com/webhook") is False
    assert WebhookService.validate_target_url("file:///etc/passwd") is False
    assert WebhookService.validate_target_url("") is False

    # Cloud metadata & link-local IP targets (SSRF)
    assert WebhookService.validate_target_url("http://169.254.169.254/") is False
    assert WebhookService.validate_target_url("http://169.254.169.254/latest/meta-data") is False

    # Loopback targets (SSRF)
    assert WebhookService.validate_target_url("http://localhost:8000/internal") is False
    assert WebhookService.validate_target_url("http://127.0.0.1:8000/admin") is False
    assert WebhookService.validate_target_url("http://[::1]:8000/admin") is False

    # Private network RFC 1918 subnets (SSRF)
    assert WebhookService.validate_target_url("http://10.0.0.1/internal") is False
    assert WebhookService.validate_target_url("http://172.16.0.1/admin") is False
    assert WebhookService.validate_target_url("http://192.168.1.1/setup") is False


def test_webhook_signature_verification() -> None:
    """Test receiver-side webhook signature verification with constant-time HMAC-SHA256."""
    secret = "whsec_super_secret_test_key_999"
    payload = b'{"event":"ALERT_CREATED","alert_id":"alt_9001","risk_score":0.89}'
    tampered_payload = b'{"event":"ALERT_CREATED","alert_id":"alt_9002","risk_score":0.89}'

    signature = WebhookService().compute_hmac_signature(secret, payload)

    # 1. Valid signature verifies True (both with sha256= prefix and raw hex)
    assert WebhookService.verify_signature(payload, signature, secret) is True
    raw_hex = signature.replace("sha256=", "")
    assert WebhookService.verify_signature(payload, raw_hex, secret) is True

    # 2. Tampered payload verifies False
    assert WebhookService.verify_signature(tampered_payload, signature, secret) is False

    # 3. Wrong secret verifies False
    assert WebhookService.verify_signature(payload, signature, "whsec_wrong_key") is False

    # 4. Empty or invalid input verifies False
    assert WebhookService.verify_signature(payload, "", secret) is False
    assert WebhookService.verify_signature(payload, "invalid_format", secret) is False


def test_webhook_ssrf_registration_blocked() -> None:
    """Test that attempting to register an SSRF URL via API returns HTTP 400 Bad Request."""
    ssrf_payloads = [
        "http://169.254.169.254/latest/meta-data",
        "http://localhost:8000/internal",
        "http://127.0.0.1:8000/keys",
        "file:///etc/passwd",
    ]
    for url in ssrf_payloads:
        resp = client.post(
            "/v1/webhooks/subscriptions",
            json={
                "tenant_id": "bank_attacker",
                "target_url": url,
                "events": ["ALERT_CREATED"],
            },
        )
        assert resp.status_code == 400
        assert "SSRF protection rejected" in resp.json()["detail"]


def test_webhook_verify_api_endpoint() -> None:
    """Test /v1/webhooks/verify API endpoint."""
    secret = "whsec_receiver_key_abc"
    payload = {"alert_id": "alt_123", "status": "CONFIRMED"}
    import json
    body_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    sig = WebhookService().compute_hmac_signature(secret, body_bytes)

    # Valid payload verification
    resp_valid = client.post(
        "/v1/webhooks/verify",
        json={"payload": payload, "signature": sig, "secret_key": secret},
    )
    assert resp_valid.status_code == 200
    assert resp_valid.json()["valid"] is True

    # Tampered payload verification
    resp_invalid = client.post(
        "/v1/webhooks/verify",
        json={"payload": {"alert_id": "alt_999"}, "signature": sig, "secret_key": secret},
    )
    assert resp_invalid.status_code == 200
    assert resp_invalid.json()["valid"] is False


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


def test_webhook_ssrf_dns_resolution_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that DNS resolution failure fails closed (returns False) rather than allowing the URL.

    Simulates socket.gaierror to verify that unresolvable hostnames are strictly rejected,
    eliminating DNS-evasion SSRF bypasses.
    """
    def mock_getaddrinfo_error(*args, **kwargs):
        raise socket.gaierror(11001, "getaddrinfo failed: Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo_error)

    # Legitimate-looking URL and arbitrary hostnames must both be rejected when DNS fails
    assert WebhookService.validate_target_url("https://api.bank.com/webhook") is False
    assert WebhookService.validate_target_url("https://unresolvable-external-service.org/hook") is False
    assert WebhookService.validate_target_url("http://failing-dns.example.com") is False


def test_webhook_ssrf_dns_rebinding_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that public hostnames resolving to private/loopback IP ranges are rejected (DNS rebinding defense)."""
    def mock_getaddrinfo_private(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo_private)
    assert WebhookService.validate_target_url("https://rebind.attacker.com/webhook") is False


def test_webhook_subscriptions_listing() -> None:
    """Test listing subscriptions and verifying secret keys are not exposed."""
    # Register subscription
    client.post(
        "/v1/webhooks/subscriptions",
        json={
            "tenant_id": "bank_listing_test",
            "target_url": "https://api.bank.com/hooks/notify",
            "events": ["ALERT_CREATED"],
        },
    )

    # Query list endpoint
    resp = client.get("/v1/webhooks/subscriptions?tenant_id=bank_listing_test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1
    assert "subscriptions" in data

    first_item = data["subscriptions"][0]
    assert first_item["tenant_id"] == "bank_listing_test"
    assert "target_url" in first_item
    assert "secret_key" not in first_item  # Masked for security


def test_webhook_subscription_deletion() -> None:
    """Test deleting a webhook subscription and confirming 404 on non-existent deletion."""
    reg_resp = client.post(
        "/v1/webhooks/subscriptions",
        json={
            "tenant_id": "bank_del_test",
            "target_url": "https://api.bank.com/hooks/delete_me",
            "events": ["CASE_RESOLVED"],
        },
    )
    sub_id = reg_resp.json()["subscription_id"]

    # Delete subscription
    del_resp = client.delete(f"/v1/webhooks/subscriptions/{sub_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    # Re-deleting must return RFC 7807 404
    re_del_resp = client.delete(f"/v1/webhooks/subscriptions/{sub_id}")
    assert re_del_resp.status_code == 404
    assert re_del_resp.json()["type"] == "https://cfi-platform.org/errors/NotFound"


def test_webhook_tenant_isolation_boundary() -> None:
    """Test that X-Tenant-ID header prevents unauthorized registration for another tenant."""
    resp = client.post(
        "/v1/webhooks/subscriptions",
        json={
            "tenant_id": "bank_victim",
            "target_url": "https://api.bank.com/hooks/leak",
            "events": ["ALERT_CREATED"],
        },
        headers={"X-Tenant-ID": "bank_attacker"},
    )
    assert resp.status_code == 403
    assert "cannot register webhooks for tenant" in resp.json()["detail"]


def test_webhook_health_and_delivery_logs_query() -> None:
    """Test /health and /deliveries endpoints."""
    health_resp = client.get("/v1/webhooks/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()
    assert health_data["status"] == "ok"
    assert health_data["service"] == "webhook_gateway"
    assert "active_subscriptions" in health_data

    deliveries_resp = client.get("/v1/webhooks/deliveries?limit=10")
    assert deliveries_resp.status_code == 200
    assert "deliveries" in deliveries_resp.json()
    assert "total_count" in deliveries_resp.json()


@pytest.mark.asyncio
async def test_webhook_exponential_backoff_and_retry() -> None:
    """Test WebhookDispatcher retry mechanism with exponential backoff on connection failure."""
    from datetime import UTC, datetime

    from app.application.services.webhook_service import WebhookDeliveryPayload
    from app.infrastructure.webhook_dispatcher import WebhookDispatcher

    dispatcher = WebhookDispatcher()
    payload = WebhookDeliveryPayload(
        event_id="evt_retry_test_1",
        event_type=WebhookEventType.ALERT_CREATED,
        payload={"alert_id": "alt_retry"},
        signature="sha256=test_sig",
        timestamp=datetime.now(UTC),
    )

    # Deliver to non-routable synthetic domain with 2 retries and 0.05s initial delay
    success = await dispatcher.deliver_with_retry(
        target_url="https://invalid-non-existent-webhook.example.com",
        delivery=payload,
        max_retries=2,
        initial_delay=0.05,
        backoff_factor=1.5,
        timeout=0.1,
    )
    assert success is False

    # Verify history recorded the failure attempt
    history = dispatcher.get_delivery_history(limit=5)
    assert len(history) >= 1
    assert history[0].delivery_id == "evt_retry_test_1"
    assert history[0].success is False
    assert history[0].attempt_count == 2


def test_webhook_canonical_api_v1_route_parity() -> None:
    """Verify canonical /api/v1/webhooks route prefix functions identically to /v1/webhooks."""
    resp = client.post(
        "/api/v1/webhooks/subscriptions",
        json={
            "tenant_id": "bank_canonical_test",
            "target_url": "https://api.bank.com/hooks/canonical",
            "events": ["ALERT_CREATED"],
        },
    )
    assert resp.status_code == 200
    sub_id = resp.json()["subscription_id"]

    # Verify retrieval via /api/v1
    list_resp = client.get("/api/v1/webhooks/subscriptions?tenant_id=bank_canonical_test")
    assert list_resp.status_code == 200
    assert any(s["subscription_id"] == sub_id for s in list_resp.json()["subscriptions"])




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
from app.presentation.routers.webhook_gateway import router

app = FastAPI()
app.include_router(router)
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



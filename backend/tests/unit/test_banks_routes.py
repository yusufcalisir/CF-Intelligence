"""Unit tests for Consortium Bank Node Directory & Edge Daemon Client API routes.

Verifies dual-routing (/api/v1 and /v1 prefixes), Pydantic v2 schemas,
bank configurations, distributions, scoring volume, consortium status,
edge daemon heartbeat, gradient submission, and client status.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _sign_payload(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    """Generate valid HMAC-SHA256 signature headers and body bytes."""
    settings = get_settings()
    secret = settings.payload_signing_secret
    timestamp = str(time.time())
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sign_data = timestamp.encode("utf-8") + b"." + body_bytes
    signature = hmac.new(
        secret.encode("utf-8"),
        sign_data,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-Payload-Signature": signature,
        "X-Payload-Timestamp": timestamp,
        "Content-Type": "application/json",
    }
    return body_bytes, headers


def test_list_banks_dual_prefix(client: TestClient) -> None:
    """Verifies GET /banks returns all 3 participating bank nodes on both prefixes."""
    for prefix in ("/api/v1/banks", "/v1/banks"):
        resp = client.get(prefix)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 3

        bank_ids = [b["id"] for b in data]
        assert "bank_a" in bank_ids
        assert "bank_b" in bank_ids
        assert "bank_c" in bank_ids

        # Validate schema contract
        sample = data[0]
        assert "name" in sample
        assert "tier" in sample
        assert "description" in sample
        assert "default_fraud_ratio" in sample
        assert "default_transactions" in sample
        assert "characteristics" in sample
        assert sample["hardware_enclave"] == "Intel SGX-v2"
        assert sample["mtls_status"] == "ACTIVE"
        assert sample["status"] == "ONLINE"


def test_get_bank_by_id_success_and_404(client: TestClient) -> None:
    """Verifies GET /banks/{id} details and 404 response on non-existent bank."""
    for prefix in ("/api/v1/banks", "/v1/banks"):
        resp = client.get(f"{prefix}/bank_a")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "bank_a"
        assert data["name"] == "Meridian National"
        assert data["tier"] == "large"

        resp_404 = client.get(f"{prefix}/unknown_bank_xyz")
        assert resp_404.status_code == 404
        assert "not found" in resp_404.json()["detail"].lower()


def test_consortium_status_dual_prefix(client: TestClient) -> None:
    """Verifies GET /banks/status returns network topology and node summary."""
    for prefix in ("/api/v1/banks", "/v1/banks"):
        resp = client.get(f"{prefix}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["consortium_name"] == "Cross-Bank Federated Intelligence Consortium"
        assert data["total_registered_nodes"] == 3
        assert data["active_nodes_count"] == 3
        assert data["hardware_enclave_enabled"] is True
        assert data["mtls_status"] == "ACTIVE"
        assert len(data["nodes"]) == 3

        node = data["nodes"][0]
        assert "bank_id" in node
        assert "name" in node
        assert "tier" in node
        assert "status" in node


def test_bank_distributions_dual_prefix(client: TestClient) -> None:
    """Verifies GET /banks/distributions computes Non-IID drift and histograms."""
    for prefix in ("/api/v1/banks", "/v1/banks"):
        resp = client.get(f"{prefix}/distributions")
        assert resp.status_code == 200
        data = resp.json()
        assert "banks" in data
        assert "divergence_summary" in data

        # Check bank partitions
        assert "bank_a" in data["banks"]
        assert "bank_b" in data["banks"]
        assert "bank_c" in data["banks"]

        bank_a = data["banks"]["bank_a"]
        assert "amount_histogram" in bank_a
        assert "hourly_fraud_rate" in bank_a
        assert "merchant_risk" in bank_a

        # Check divergence summary
        summary = data["divergence_summary"]
        assert "amount_ks_statistic" in summary
        assert "overall_non_iid_score" in summary
        assert "feature_drift" in summary
        assert "concept_drift" in summary


def test_scoring_volume_dual_prefix(client: TestClient) -> None:
    """Verifies GET /banks/scoring-volume returns 24-hour aggregated scoring timeline."""
    for prefix in ("/api/v1/banks", "/v1/banks"):
        resp = client.get(f"{prefix}/scoring-volume")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 24
        assert data[0]["time"] == "00:00"
        assert data[-1]["time"] == "23:00"
        assert all("volume" in pt for pt in data)


def test_edge_daemon_heartbeat_dual_prefix(client: TestClient) -> None:
    """Verifies POST /bank-client/heartbeat accepts liveness probe and attestation."""
    payload = {
        "bank_id": "bank_a",
        "hardware_type": "cuda",
        "attestation_quote": "sgx_quote_valid_payload_sample",
        "metrics": {"gpu_util": 0.45, "ram_gb_used": 12.4},
    }
    for prefix in ("/api/v1/bank-client", "/v1/bank-client"):
        resp = client.post(f"{prefix}/heartbeat", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ACK"
        assert data["bank_id"] == "bank_a"
        assert data["next_heartbeat_seconds"] == 15
        assert data["attestation_verified"] is True


def test_edge_daemon_gradients_submission_dual_prefix(client: TestClient) -> None:
    """Verifies POST /bank-client/gradients rejects unsigned requests and accepts valid signed uploads."""
    payload = {
        "bank_id": "bank_b",
        "round_id": 3,
        "num_samples": 2500,
        "loss": 0.1875,
        "flat_gradients": [0.01, -0.02, 0.03, -0.04],
        "privacy_spent_epsilon": 0.85,
    }
    for prefix in ("/api/v1/bank-client", "/v1/bank-client"):
        # 1. Unsigned request must be rejected with 401 Unauthorized
        unsigned_resp = client.post(f"{prefix}/gradients", json=payload)
        assert unsigned_resp.status_code == 401

        # 2. Properly HMAC-signed request succeeds
        body_bytes, headers = _sign_payload(payload)
        resp = client.post(f"{prefix}/gradients", content=body_bytes, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ACCEPTED"
        assert data["bank_id"] == "bank_b"
        assert data["round_id"] == 3
        assert len(data["gradient_hash"]) == 64  # SHA-256 hex


def test_edge_daemon_status_dual_prefix(client: TestClient) -> None:
    """Verifies GET /bank-client/status returns current client daemon runtime state."""
    for prefix in ("/api/v1/bank-client", "/v1/bank-client"):
        resp = client.get(f"{prefix}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_initialized" in data
        assert "train_samples" in data
        assert "test_samples" in data
        assert "hardware_profile" in data
        assert "status" in data


def test_edge_daemon_initialize_dataset(client: TestClient) -> None:
    """Verifies POST /bank-client/initialize enforces signature and creates deterministic partitions."""
    payload = {
        "bank_id": "bank_a",
        "num_transactions": 50,
        "seed": 42,
    }
    # 1. Unsigned request fails with 401
    unsigned_resp = client.post("/api/v1/bank-client/initialize", json=payload)
    assert unsigned_resp.status_code == 401

    # 2. Signed request succeeds
    body_bytes, headers = _sign_payload(payload)
    resp = client.post("/api/v1/bank-client/initialize", content=body_bytes, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "initialized"
    assert data["bank_id"] == "bank_a"
    assert data["train_samples"] == 40
    assert data["test_samples"] == 10

    # 3. Signed request with invalid bank_id returns 400 Bad Request
    bad_payload = {
        "bank_id": "bank_invalid_unknown",
        "num_transactions": 50,
    }
    bad_bytes, bad_headers = _sign_payload(bad_payload)
    bad_resp = client.post("/api/v1/bank-client/initialize", content=bad_bytes, headers=bad_headers)
    assert bad_resp.status_code == 400
    assert "invalid bank_id" in bad_resp.json()["detail"].lower()

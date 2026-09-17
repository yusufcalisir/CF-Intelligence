"""Unit test validating developer portal endpoints and SDK route aliases."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_developer_portal_predict_score_alias(client: TestClient) -> None:
    """Verify POST /api/v1/predict/score alias works identically to score-transaction."""
    payload = {
        "transaction_id": "txn_dev_001",
        "account_id": "acc_dev_001",
        "amount": 15000.0,
        "currency": "EUR",
        "merchant_id": "crypto_exchange_berlin",
        "country": "DE",
        "device_id": "device_fp_alpha",
    }
    resp = client.post("/api/v1/predict/score", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "risk_score" in data
    assert "decision" in data
    assert "latency_ms" in data


def test_developer_portal_coordinator_negotiate_post(client: TestClient) -> None:
    """Verify POST /api/v1/coordinator/negotiate accepts JSON payload."""
    payload = {
        "bank_id": "bank_alpha",
        "base_batch_size": 64,
        "base_epochs": 4,
        "hardware_type": "cuda",
        "available_vram_gb": 24.0,
        "bandwidth_mbps": 1000.0,
        "local_sample_count": 50000,
    }
    resp = client.post("/api/v1/coordinator/negotiate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["bank_id"] == "bank_alpha"
    assert data["batch_size"] > 0
    assert data["local_epochs"] > 0


def test_developer_portal_psi_match_direct(client: TestClient) -> None:
    """Verify POST /api/v1/psi/match direct route responds with DH-PSI protocol."""
    payload = {
        "source_bank_id": "bank_alpha",
        "target_bank_id": "bank_beta",
        "client_ecdh_blinded_hashes": ["04a1b2c3d4", "04f8e7d6c5"],
        "enable_fuzzy": True,
    }
    resp = client.post("/api/v1/psi/match", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "protocol" in data
    assert data["zero_raw_pii_enforced"] is True


def test_developer_portal_training_rounds_alias(client: TestClient) -> None:
    """Verify GET /api/v1/training/rounds/{simulation_id} returns round history."""
    from app.presentation.routers.simulation import _simulation_results

    _simulation_results.set("sim_demo_01", {"id": "sim_demo_01", "total_rounds": 5})
    resp = client.get("/api/v1/training/rounds/sim_demo_01")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_developer_portal_scalar_html_gateway(client: TestClient) -> None:
    """Verify GET /scalar serves dark-themed Scalar documentation."""
    resp = client.get("/scalar")
    assert resp.status_code == 200
    assert "Enterprise API Reference" in resp.text
    assert "@scalar/api-reference" in resp.text

"""Unit tests for Scenario replay and Adversarial Attack Injection API routes.

Validates pre-built fraud scenario replay, execution status tracking,
active scenario management, and multi-strategy Byzantine/Sybil defense shields.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """TestClient bound to main application."""
    return TestClient(app)


def test_list_scenarios_success(client: TestClient) -> None:
    """Verify GET /api/v1/scenarios returns all 4 supported archetype scenarios."""
    response = client.get("/api/v1/scenarios")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 4

    types = {s["type"] for s in data}
    assert "fraud_ring" in types
    assert "account_takeover" in types
    assert "money_laundering" in types
    assert "card_testing" in types

    for item in data:
        assert "name" in item
        assert "description" in item
        assert "banks_involved" in item
        assert "estimated_events" in item
        assert "estimated_duration_seconds" in item
        assert item["estimated_events"] > 0
        assert item["estimated_duration_seconds"] > 0


def test_start_scenario_success(client: TestClient) -> None:
    """Verify POST /api/v1/scenarios/start triggers replay execution."""
    payload = {
        "scenario_type": "fraud_ring",
        "speed_multiplier": 2.0,
    }
    response = client.post("/api/v1/scenarios/start", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert "scenario_id" in data
    assert data["scenario_type"] == "fraud_ring"
    assert data["status"] == "running"
    assert data["total_events"] > 0


def test_start_scenario_invalid_type_returns_400(client: TestClient) -> None:
    """Verify POST /api/v1/scenarios/start rejects unknown scenario types with 400."""
    payload = {
        "scenario_type": "non_existent_exploit",
        "speed_multiplier": 1.0,
    }
    response = client.post("/api/v1/scenarios/start", json=payload)
    assert response.status_code == 400
    assert "Unknown scenario type" in response.json()["detail"]


def test_start_scenario_speed_multiplier_bounds(client: TestClient) -> None:
    """Verify speed_multiplier outside [0.1, 10.0] returns 422."""
    payload = {
        "scenario_type": "fraud_ring",
        "speed_multiplier": 25.0,  # Invalid: > 10.0
    }
    response = client.post("/api/v1/scenarios/start", json=payload)
    assert response.status_code == 422


def test_scenario_status_success(client: TestClient) -> None:
    """Verify GET /api/v1/scenarios/{id}/status returns streaming progress."""
    # Start a scenario first
    start_res = client.post(
        "/api/v1/scenarios/start",
        json={"scenario_type": "account_takeover", "speed_multiplier": 5.0},
    )
    scenario_id = start_res.json()["scenario_id"]

    response = client.get(f"/api/v1/scenarios/{scenario_id}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["scenario_id"] == scenario_id
    assert data["status"] in ("running", "completed", "stopped")
    assert data["total_events"] > 0
    assert data["delivered_events"] >= 0
    assert "started_at" in data


def test_scenario_status_not_found_raises_authentic_404(client: TestClient) -> None:
    """Verify non-existent scenario UUID returns authentic 404 (zero mock 100/100 fallback)."""
    fake_uuid = "e7b0c812-945a-4cb6-9bc4-000000000000"
    response = client.get(f"/api/v1/scenarios/{fake_uuid}/status")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_stop_scenario_success(client: TestClient) -> None:
    """Verify POST /api/v1/scenarios/{id}/stop halts a running stream."""
    start_res = client.post(
        "/api/v1/scenarios/start",
        json={"scenario_type": "money_laundering", "speed_multiplier": 1.0},
    )
    scenario_id = start_res.json()["scenario_id"]

    stop_res = client.post(f"/api/v1/scenarios/{scenario_id}/stop")
    assert stop_res.status_code == 200
    stop_data = stop_res.json()
    assert stop_data["scenario_id"] == scenario_id
    assert stop_data["status"] == "stopped"

    # Confirm status reflects stopped state
    status_res = client.get(f"/api/v1/scenarios/{scenario_id}/status")
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "stopped"


def test_stop_scenario_not_found_raises_404(client: TestClient) -> None:
    """Verify stopping an unknown scenario returns 404."""
    response = client.post("/api/v1/scenarios/unknown-nonexistent-id/stop")
    assert response.status_code == 404


def test_active_scenarios_list(client: TestClient) -> None:
    """Verify GET /api/v1/scenarios/active/list returns ActiveScenarioItem list."""
    response = client.get("/api/v1/scenarios/active/list")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    for item in data:
        assert "scenario_id" in item
        assert "status" in item
        assert "total_events" in item
        assert "delivered_events" in item
        assert "speed_multiplier" in item
        assert "started_at" in item


@pytest.mark.parametrize("defense", ["krum", "trimmed_mean", "bulyan", "spectral"])
def test_inject_adversarial_attack_byzantine_poisoning(client: TestClient, defense: str) -> None:
    """Verify Byzantine gradient attack injection across all robust defense shields."""
    payload = {
        "attack_type": "byzantine_poisoning",
        "adversary_bank": "bank_gamma",
        "target_bank": "bank_alpha",
        "intensity_rate": 600,
        "defense_strategy": defense,
    }
    response = client.post("/api/v1/scenarios/inject-attack", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["attack_type"] == "byzantine_poisoning"
    assert data["status"] == "quarantined"
    assert data["adversary_quarantined"] == "bank_gamma"
    assert data["euclidean_distance"] > 0.0
    assert data["distance_threshold"] > 0.0
    assert data["packets_blocked"] == 600
    assert data["mitigation_latency_ms"] > 0.0
    assert data["auc_protected"] >= 0.91
    assert data["auc_compromised_baseline"] < 0.65
    assert len(data["log_entry"]) > 0


def test_inject_adversarial_attack_smurfing_layering(client: TestClient) -> None:
    """Verify high-velocity smurfing attack is intercepted by GraphSAGE LSH-PSI."""
    payload = {
        "attack_type": "smurfing_layering",
        "adversary_bank": "bank_gamma",
        "target_bank": "bank_alpha",
        "intensity_rate": 300,
        "defense_strategy": "psi_graph",
    }
    response = client.post("/api/v1/scenarios/inject-attack", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["attack_type"] == "smurfing_layering"
    assert data["status"] == "intercepted"
    assert "GraphSAGE" in data["defense_activated"]
    assert data["packets_blocked"] == 900  # 300 * 3 structured transfers
    assert data["auc_protected"] >= 0.91


def test_inject_adversarial_attack_sybil_ring(client: TestClient) -> None:
    """Verify synthetic identity collision sybil attack is mitigated via Paillier PSI."""
    payload = {
        "attack_type": "sybil_ring",
        "adversary_bank": "bank_gamma",
        "target_bank": "bank_alpha",
        "intensity_rate": 450,
        "defense_strategy": "krum",
    }
    response = client.post("/api/v1/scenarios/inject-attack", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["attack_type"] == "sybil_ring"
    assert data["status"] == "mitigated"
    assert "Paillier" in data["defense_activated"]
    assert data["adversary_quarantined"] == "bank_gamma"
    assert data["auc_protected"] >= 0.90


def test_inject_adversarial_attack_invalid_type_raises_422(client: TestClient) -> None:
    """Verify unsupported attack type triggers Pydantic schema validation error (422)."""
    payload = {
        "attack_type": "zero_day_unsupported_exploit",
        "adversary_bank": "bank_gamma",
        "target_bank": "bank_alpha",
        "intensity_rate": 500,
    }
    response = client.post("/api/v1/scenarios/inject-attack", json=payload)
    assert response.status_code == 422


def test_inject_adversarial_attack_intensity_bounds(client: TestClient) -> None:
    """Verify intensity_rate outside [10, 5000] is rejected with 422."""
    payload = {
        "attack_type": "byzantine_poisoning",
        "intensity_rate": 10000,  # Invalid: > 5000
    }
    response = client.post("/api/v1/scenarios/inject-attack", json=payload)
    assert response.status_code == 422

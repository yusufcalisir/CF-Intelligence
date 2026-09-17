"""Unit tests for Federated Learning Simulation & Training Orchestration API routes.

Verifies dual-routing (/api/v1 and /v1 prefixes), singular/plural path parity,
Pydantic v2 schemas, status and stop controls, training round telemetry,
metrics aggregation, and EU AI Act report generation.
"""

from __future__ import annotations

from typing import Any
import uuid

import pytest
from fastapi.testclient import TestClient

from app.domain.enums import SimulationStatus
from app.main import app
from app.presentation.routers.simulation import _simulation_events, _simulation_results


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def sample_sim_id() -> str:
    sim_id = f"sim-{uuid.uuid4().hex[:8]}"
    _simulation_results.set(
        sim_id,
        {
            "id": sim_id,
            "status": SimulationStatus.TRAINING_FEDERATED.value,
            "current_round": 2,
            "total_rounds": 5,
            "created_at": "2026-03-01T10:00:00Z",
            "progress_pct": 45.0,
            "banks": [],
            "config": {
                "num_rounds": 5,
                "local_epochs": 2,
                "learning_rate": 0.001,
                "batch_size": 32,
            },
        },
    )
    # Seed training round events
    _simulation_events.push_list(
        sim_id,
        {
            "event_type": "round_complete",
            "data": {
                "round": 1,
                "total": 5,
                "loss": 0.354,
                "auc": 0.885,
                "per_bank_auc": {"bank_a": 0.89, "bank_b": 0.88},
                "per_bank_loss": {"bank_a": 0.35, "bank_b": 0.36},
                "participants": ["bank_a", "bank_b"],
                "dropped": [],
                "duration_ms": 1200.0,
                "privacy_budget": 1.2,
                "feature_importance": {"amount": 0.45, "velocity": 0.35},
                "canary_info": {"status": "clean"},
            },
        },
    )
    _simulation_events.push_list(
        sim_id,
        {
            "event_type": "round_complete",
            "data": {
                "round": 2,
                "total": 5,
                "loss": 0.281,
                "auc": 0.912,
                "per_bank_auc": {"bank_a": 0.92, "bank_b": 0.90},
                "per_bank_loss": {"bank_a": 0.28, "bank_b": 0.29},
                "participants": ["bank_a", "bank_b"],
                "dropped": [],
                "duration_ms": 1150.0,
                "privacy_budget": 2.4,
                "feature_importance": {"amount": 0.48, "velocity": 0.32},
                "canary_info": {"status": "clean"},
            },
        },
    )
    return sim_id


def test_simulation_dual_and_singular_routing_parity(client: TestClient) -> None:
    """Verify listing simulations across all 4 prefixes returns 200 OK."""
    prefixes = [
        "/api/v1/simulations",
        "/v1/simulations",
        "/api/v1/simulation",
        "/v1/simulation",
    ]
    for prefix in prefixes:
        response = client.get(prefix)
        assert response.status_code == 200, f"Failed at {prefix}: {response.text}"
        data = response.json()
        assert isinstance(data, list)


def test_simulation_start_alias_and_validation(client: TestClient) -> None:
    """Verify POST /start initiates simulation and validation bounds are enforced."""
    # 1. Valid configuration via /api/v1/simulation/start
    valid_payload: dict[str, Any] = {
        "num_rounds": 3,
        "local_epochs": 1,
        "learning_rate": 0.005,
        "batch_size": 32,
        "bank_a_transactions": 5000,
        "bank_b_transactions": 3000,
        "bank_c_transactions": 2000,
        "privacy_mechanism": "none",
    }
    resp = client.post("/api/v1/simulation/start", json=valid_payload)
    assert resp.status_code == 202
    body = resp.json()
    assert "id" in body
    assert body["status"] == SimulationStatus.PENDING.value
    assert "Simulation started" in body["message"]

    # 2. Invalid configuration (num_rounds < 1, batch_size < 8)
    invalid_payload = {
        "num_rounds": 0,
        "batch_size": 2,
    }
    bad_resp = client.post("/v1/simulations/start", json=invalid_payload)
    assert bad_resp.status_code == 422


def test_simulation_status_endpoint(client: TestClient, sample_sim_id: str) -> None:
    """Verify GET /{simulation_id}/status returns lightweight progress telemetry."""
    # 1. Existing simulation status
    resp = client.get(f"/api/v1/simulation/{sample_sim_id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == sample_sim_id
    assert data["status"] == SimulationStatus.TRAINING_FEDERATED.value
    assert data["current_round"] == 2
    assert data["total_rounds"] == 5
    assert data["progress_pct"] > 0

    # 2. Non-existent simulation
    notFound = client.get("/api/v1/simulation/sim-non-existent-9999/status")
    assert notFound.status_code == 404


def test_simulation_stop_lifecycle(client: TestClient, sample_sim_id: str) -> None:
    """Verify stopping an active simulation and subsequent state protection."""
    # 1. Gracefully stop active simulation via path parameter
    stop_resp = client.post(
        f"/api/v1/simulation/{sample_sim_id}/stop?reason=operator_scheduled_pause"
    )
    assert stop_resp.status_code == 200
    stop_data = stop_resp.json()
    assert stop_data["simulation_id"] == sample_sim_id
    assert stop_data["status"] == SimulationStatus.STOPPED.value
    assert "successfully stopped" in stop_data["message"]
    assert "stopped_at" in stop_data

    # 2. Verify state updated in store
    stored = _simulation_results.get(sample_sim_id)
    assert stored is not None
    assert stored["status"] == SimulationStatus.STOPPED.value

    # 3. Subsequent stop attempt on already stopped simulation returns 400 Bad Request
    dup_resp = client.post(f"/v1/simulations/{sample_sim_id}/stop")
    assert dup_resp.status_code == 400
    assert "already in terminal state" in dup_resp.json()["detail"].lower()

    # 4. Stop via JSON body POST /api/v1/simulation/stop
    new_sim_id = f"sim-body-{uuid.uuid4().hex[:6]}"
    _simulation_results.set(
        new_sim_id,
        {
            "id": new_sim_id,
            "status": SimulationStatus.TRAINING_LOCAL.value,
            "total_rounds": 10,
        },
    )
    body_resp = client.post(
        "/api/v1/simulation/stop",
        json={"simulation_id": new_sim_id, "reason": "batch cancelled"},
    )
    assert body_resp.status_code == 200
    assert body_resp.json()["status"] == SimulationStatus.STOPPED.value


def test_simulation_comparison_endpoint(client: TestClient) -> None:
    """Verify local vs federated comparison endpoint rules."""
    # 1. 404 for unknown simulation
    r_404 = client.get("/api/v1/simulations/unknown-sim-id/comparison")
    assert r_404.status_code == 404

    # 2. 400 when simulation is not completed
    pending_id = f"sim-pending-{uuid.uuid4().hex[:6]}"
    _simulation_results.set(
        pending_id,
        {
            "id": pending_id,
            "status": SimulationStatus.PENDING.value,
        },
    )
    r_bad = client.get(f"/api/v1/simulations/{pending_id}/comparison")
    assert r_bad.status_code == 400
    assert "not yet completed" in r_bad.json()["detail"].lower()


def test_simulation_ai_act_report(client: TestClient, sample_sim_id: str) -> None:
    """Verify EU AI Act Article 10-15 compliance audit report schema."""
    resp = client.get(f"/api/v1/simulation/{sample_sim_id}/ai-act-report")
    assert resp.status_code == 200
    report = resp.json()
    assert report["simulation_id"] == sample_sim_id
    assert report["report_type"] == "EU_AI_ACT_COMPLIANCE"
    assert "regulation_version" in report
    assert "article_compliance" in report
    assert "training_summary" in report
    assert "bias_audit" in report


def test_training_rounds_telemetry(client: TestClient, sample_sim_id: str) -> None:
    """Verify round-by-round training telemetry retrieval and pagination."""
    # 1. Get all rounds for simulation
    resp = client.get(f"/api/v1/training/{sample_sim_id}/rounds")
    assert resp.status_code == 200
    rounds = resp.json()
    assert len(rounds) == 2
    assert rounds[0]["round_number"] == 1
    assert rounds[0]["global_loss"] == pytest.approx(0.354, rel=1e-3)
    assert rounds[0]["auc"] == pytest.approx(0.885, rel=1e-3)
    assert rounds[1]["round_number"] == 2

    # 2. Get specific round details
    single_resp = client.get(f"/v1/training/{sample_sim_id}/rounds/1")
    assert single_resp.status_code == 200
    assert single_resp.json()["round_number"] == 1

    # 3. Round not found -> 404
    missing_round = client.get(f"/api/v1/training/{sample_sim_id}/rounds/99")
    assert missing_round.status_code == 404

    # 4. Alias on simulation router returns parity
    sim_rounds_resp = client.get(f"/api/v1/simulation/{sample_sim_id}/rounds")
    assert sim_rounds_resp.status_code == 200
    assert len(sim_rounds_resp.json()) == 2


def test_training_progress_and_metrics(client: TestClient, sample_sim_id: str) -> None:
    """Verify real-time training progress envelope and aggregated loss/AUC metrics."""
    # 1. Specific simulation progress
    prog_resp = client.get(f"/api/v1/training/{sample_sim_id}/progress")
    assert prog_resp.status_code == 200
    prog_data = prog_resp.json()
    assert prog_data["simulation_id"] == sample_sim_id
    assert prog_data["event_type"] == "round_complete"
    assert prog_data["current_round"] == 2

    # 2. General /progress endpoint
    latest_resp = client.get("/api/v1/training/progress")
    assert latest_resp.status_code == 200
    assert "event_type" in latest_resp.json()

    # 3. Aggregated training metrics
    metrics_resp = client.get(f"/api/v1/training/metrics?simulation_id={sample_sim_id}")
    assert metrics_resp.status_code == 200
    metrics_data = metrics_resp.json()
    assert metrics_data["simulation_id"] == sample_sim_id
    assert len(metrics_data["global_losses"]) == 2
    assert len(metrics_data["auc_history"]) == 2
    assert "bank_a" in metrics_data["per_bank_auc"]


def test_training_history_endpoint(client: TestClient, sample_sim_id: str) -> None:
    """Verify GET /api/v1/training/history lists past simulations with pagination."""
    resp = client.get("/api/v1/training/history?limit=5")
    assert resp.status_code == 200
    history = resp.json()
    assert "total_count" in history
    assert "runs" in history
    assert isinstance(history["runs"], list)
    assert any(run["simulation_id"] == sample_sim_id for run in history["runs"])

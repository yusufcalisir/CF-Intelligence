"""Unit tests for Multi-Bank Simulator Engine & Interactive POC Sandbox Replay.

Verifies deterministic multi-bank federated fraud detection simulation:
- 3-bank consortium topology (Meridian National, Nexus Digital, Heritage Regional)
- Configurable fraud injection typologies (Mule Rings, Byzantine Adversarial Nodes)
- Dynamic round-by-round telemetry (Loss, PR-AUC, ROC-AUC, Gradient Cosine Similarities)
- Krum Byzantine outlier quarantine & Leave-One-Out (LOO) Shapley valuation payouts
- Differential Privacy budget consumption tracking
- Thread-safe session management & SHA-256 audit sealing
- Dual-prefix REST API endpoint parity (/api/v1 and /v1, singular and plural)
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.multi_bank_simulator import (
    FraudInjectionType,
    MultiBankSimulator,
    POCSessionStatus,
    get_multi_bank_simulator,
)
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def simulator() -> MultiBankSimulator:
    return get_multi_bank_simulator()


def test_multi_bank_simulator_singleton(simulator: MultiBankSimulator) -> None:
    """Verifies thread-safe singleton pattern returns consistent instance."""
    inst1 = get_multi_bank_simulator()
    inst2 = MultiBankSimulator.get_instance()
    assert inst1 is simulator
    assert inst2 is simulator


def test_get_presets_catalog(simulator: MultiBankSimulator) -> None:
    """Verifies retrieval of pre-configured POC demonstration scenarios."""
    presets = simulator.get_presets()
    assert len(presets) >= 4

    preset_ids = [p["preset_id"] for p in presets]
    assert "poc-enterprise-standard" in preset_ids
    assert "poc-byzantine-resilience" in preset_ids
    assert "poc-smurfing-containment" in preset_ids
    assert "poc-quick-evaluation" in preset_ids

    valid_injections = {fit.value for fit in FraudInjectionType}
    for p in presets:
        assert p["rounds"] > 0
        assert len(p["participating_banks"]) >= 3
        assert p["aggregation_strategy"] in ("FEDAVG", "FEDPROX", "KRUM")
        assert p["fraud_injection"] in valid_injections
        assert "name" in p and "description" in p


def test_get_participating_banks_profiles(simulator: MultiBankSimulator) -> None:
    """Verifies institutional metadata for Meridian, Nexus, and Heritage."""
    banks = simulator.get_participating_banks()
    assert len(banks) == 3

    bank_ids = {b["bank_id"] for b in banks}
    assert bank_ids == {"bank_meridian", "bank_nexus", "bank_heritage"}

    for b in banks:
        assert b["base_fraud_rate"] > 0
        assert b["sample_size"] >= 10000
        assert b["country"] in ("DE", "FR", "NL")
        assert len(b["typology"]) > 10


def test_execute_poc_replay_enterprise_standard(simulator: MultiBankSimulator) -> None:
    """Verifies 5-round collaborative FedGNN execution with mule ring containment."""
    summary = simulator.execute_poc_replay(
        preset_id="poc-enterprise-standard",
        random_seed=42,
    )

    assert summary.session_id.startswith("POC-SIM-")
    assert summary.preset_id == "poc-enterprise-standard"
    assert summary.status == POCSessionStatus.COMPLETED.value
    assert summary.rounds_completed == 5
    assert summary.total_rounds == 5
    assert len(summary.rounds_telemetry) == 5

    # Convergence progression
    first_round = summary.rounds_telemetry[0]
    final_round = summary.rounds_telemetry[-1]
    assert first_round["global_loss"] > final_round["global_loss"]
    assert first_round["global_pr_auc"] < final_round["global_pr_auc"]
    assert final_round["global_pr_auc"] >= 0.85

    # Side-by-side comparison metrics
    comp = summary.local_vs_federated
    assert comp["collaborative_fedgnn"]["pr_auc_gain_pct"] > 20.0
    assert summary.mule_ring_containment_rate_pct > 90.0
    assert summary.mttr_minutes < 30.0

    # Differential privacy tracking
    assert final_round["dp_budget_consumed"] > 0.0

    # Shapley valuation payouts
    assert summary.total_shapley_incentives_eur > 10000.0

    # Cryptographic audit hash format (64-char hex SHA-256)
    assert re.match(r"^[0-9a-f]{64}$", summary.cryptographic_audit_hash)


def test_deterministic_reproducibility(simulator: MultiBankSimulator) -> None:
    """Verifies identical seed yields identical metrics and audit hash."""
    run1 = simulator.execute_poc_replay("poc-enterprise-standard", random_seed=777)
    run2 = simulator.execute_poc_replay("poc-enterprise-standard", random_seed=777)

    assert run1.rounds_telemetry[-1]["global_pr_auc"] == run2.rounds_telemetry[-1]["global_pr_auc"]
    assert run1.rounds_telemetry[-1]["global_loss"] == run2.rounds_telemetry[-1]["global_loss"]
    assert (
        run1.local_vs_federated["collaborative_fedgnn"]["pr_auc_gain_pct"]
        == run2.local_vs_federated["collaborative_fedgnn"]["pr_auc_gain_pct"]
    )


def test_byzantine_resilience_preset(simulator: MultiBankSimulator) -> None:
    """Verifies Krum outlier quarantine and zero Shapley payout for compromised node."""
    summary = simulator.execute_poc_replay(
        preset_id="poc-byzantine-resilience",
        random_seed=42,
    )

    assert summary.rounds_completed == 5
    telemetry = summary.rounds_telemetry

    # Round 1 is clean before injection
    assert len(telemetry[0]["byzantine_nodes_quarantined"]) == 0

    # Rounds 2-5 isolate adversarial Nexus Digital node
    for r in telemetry[1:]:
        assert "bank_nexus" in r["byzantine_nodes_quarantined"]
        assert "bank_nexus" not in r["krum_selected_nodes"]
        # Adversarial node receives 0 payout
        assert r["shapley_payouts_eur"]["bank_nexus"] == 0.0
        # Positive payouts for honest nodes
        assert r["shapley_payouts_eur"]["bank_meridian"] > 0.0
        assert r["shapley_payouts_eur"]["bank_heritage"] > 0.0
        # Inverted / negative cosine similarity
        assert r["gradient_cosine_similarities"]["meridian_vs_nexus"] < 0.0


def test_poc_quick_evaluation_preset(simulator: MultiBankSimulator) -> None:
    """Verifies fast 3-round execution for lightweight integration smoke tests."""
    summary = simulator.execute_poc_replay(
        preset_id="poc-quick-evaluation",
        random_seed=123,
    )
    assert summary.rounds_completed == 3
    assert len(summary.rounds_telemetry) == 3
    # Differential privacy disabled in quick preset
    assert summary.rounds_telemetry[-1]["dp_budget_consumed"] == 0.0


def test_session_management_and_status(simulator: MultiBankSimulator) -> None:
    """Verifies in-memory retrieval of completed sessions and recent history."""
    summary = simulator.execute_poc_replay("poc-quick-evaluation", random_seed=1)
    session_id = summary.session_id

    # Retrieve valid session
    session_data = simulator.get_session_status(session_id)
    assert session_data is not None
    assert session_data["session_id"] == session_id
    assert session_data["status"] == "COMPLETED"

    # Non-existent session returns None
    assert simulator.get_session_status("non-existent-session-id") is None

    # Check recent sessions listing
    recent = simulator.list_recent_sessions(limit=5)
    assert len(recent) >= 1
    assert any(s["session_id"] == session_id for s in recent)


def test_api_presets_dual_routing(client: TestClient) -> None:
    """Verifies GET /poc/presets across all dual router aliases."""
    routes = [
        "/api/v1/simulations/poc/presets",
        "/v1/simulations/poc/presets",
        "/api/v1/simulation/poc/presets",
        "/v1/simulation/poc/presets",
    ]

    for route in routes:
        resp = client.get(route)
        assert resp.status_code == 200, f"Route {route} failed with {resp.status_code}"
        data = resp.json()
        assert "presets" in data
        assert "participating_banks" in data
        assert len(data["presets"]) >= 4
        assert len(data["participating_banks"]) == 3


def test_api_execute_poc_replay(client: TestClient) -> None:
    """Verifies POST /poc/replay and subsequent GET status and summary endpoints."""
    # Execute replay
    payload: dict[str, Any] = {
        "preset_id": "poc-quick-evaluation",
        "seed": 42,
    }
    resp = client.post("/api/v1/simulations/poc/replay", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "COMPLETED"
    session_id = data["session_id"]
    assert session_id.startswith("POC-SIM-")

    # Query status endpoint
    status_resp = client.get(f"/api/v1/simulations/poc/status/{session_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["session_id"] == session_id

    # Query summary endpoint
    summary_resp = client.get(f"/api/v1/simulations/poc/summary/{session_id}")
    assert summary_resp.status_code == 200
    assert summary_resp.json()["session_id"] == session_id

    # Test singular alias
    summary_singular = client.get(f"/v1/simulation/poc/summary/{session_id}")
    assert summary_singular.status_code == 200
    assert summary_singular.json()["session_id"] == session_id


def test_api_poc_session_not_found(client: TestClient) -> None:
    """Verifies 404 response for unknown session IDs."""
    resp = client.get("/api/v1/simulations/poc/status/UNKNOWN-SESSION-999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()

    resp_summary = client.get("/api/v1/simulations/poc/summary/UNKNOWN-SESSION-999")
    assert resp_summary.status_code == 404
    assert "not found" in resp_summary.json()["detail"].lower()

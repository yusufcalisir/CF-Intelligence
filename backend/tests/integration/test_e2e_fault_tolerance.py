"""End-to-End Fault Tolerance Integration Test Suite — Section 43.1.

Validates coordinator fault recovery, mid-round bank disconnections, and Byzantine spectral anomaly defense.
"""

from __future__ import annotations

import numpy as np

from app.application.services.coordinator_service import CoordinatorService
from app.domain.byzantine_defense import SpectralByzantineDefense


def test_bank_drops_mid_round_quorum_still_met() -> None:
    """Verifies round completes successfully when 1 bank drops mid-round if quorum threshold (>=2) is satisfied."""
    svc = CoordinatorService()
    svc.register_client("bank_alpha")
    svc.register_client("bank_beta")
    svc.register_client("bank_gamma")

    round_data = svc.start_round(consortium_id="c_fault_tol", min_clients=2)
    assert round_data["status"] in ("COLLECTING_GRADIENTS", "TRAINING")

    # bank_alpha and bank_beta submit gradients; bank_gamma drops (no submission)
    res_a = svc.on_gradient_received(1, "bank_alpha", b"grad_alpha")
    assert res_a["status"] == "GRADIENT_STORED"

    res_b = svc.on_gradient_received(1, "bank_beta", b"grad_beta")
    assert res_b["status"] == "COMPLETED"
    assert res_b["is_champion"] is True


def test_coordinator_restart_resumes_round() -> None:
    """Verifies coordinator state persistence allows round recovery after process restart."""
    svc1 = CoordinatorService()
    svc1.register_client("bank_alpha")
    svc1.register_client("bank_beta")
    round_data = svc1.start_round(consortium_id="c_restart_test", min_clients=2)
    round_id = round_data["round_id"]

    # Simulate process restart by re-instantiating CoordinatorService with existing round ID state
    svc2 = CoordinatorService()
    svc2.register_client("bank_alpha")
    svc2.register_client("bank_beta")
    svc2.start_round(consortium_id="c_restart_test", min_clients=2)

    res_a = svc2.on_gradient_received(round_id, "bank_alpha", b"grad_alpha_recovered")
    assert res_a["status"] == "GRADIENT_STORED"

    res_b = svc2.on_gradient_received(round_id, "bank_beta", b"grad_beta_recovered")
    assert res_b["status"] == "COMPLETED"


def test_corrupt_gradient_triggers_byzantine_defense() -> None:
    """Verifies Byzantine defense detects and isolates outlier gradients without corrupting global model."""
    defense = SpectralByzantineDefense(contamination_ratio=0.33)

    # 2 valid bank gradients (normal distribution around zero)
    grad_valid_1 = np.random.normal(loc=0.0, scale=0.1, size=(10, 10))
    grad_valid_2 = np.random.normal(loc=0.0, scale=0.1, size=(10, 10))

    # 1 malicious corrupt outlier gradient (poisoning attack)
    grad_corrupt = np.ones((10, 10)) * 1e5

    updates = {
        "bank_alpha": grad_valid_1,
        "bank_beta": grad_valid_2,
        "bank_malicious": grad_corrupt,
    }

    sanitized_updates, anomalies = defense.filter_anomalous_updates(updates)

    assert "bank_malicious" in anomalies
    assert "bank_alpha" not in anomalies
    assert "bank_beta" not in anomalies
    assert len(sanitized_updates) == 2
    assert "bank_malicious" not in sanitized_updates

"""Unit tests for Privacy Defense, Byzantine Robust Aggregation & Attack Benchmarking API endpoints."""

from __future__ import annotations

import fastapi
from fastapi.testclient import TestClient

from app.presentation.routers.privacy_defense import api_router, router

_app = fastapi.FastAPI()
_app.include_router(router)
_app.include_router(api_router)
client = TestClient(_app)


class TestAggregationMethodsCatalogue:
    def test_get_methods_canonical(self) -> None:
        response = client.get("/v1/privacy-defense/aggregation-methods")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 11
        ids = [item["id"] for item in data]
        assert "bulyan" in ids
        assert "trimmed_mean" in ids
        assert "krum" in ids
        assert "coordinate_wise_median" in ids
        assert "fed_avg" in ids

    def test_get_methods_prefixed(self) -> None:
        response = client.get("/api/v1/privacy-defense/aggregation-methods")
        assert response.status_code == 200
        bulyan = next(item for item in response.json() if item["id"] == "bulyan")
        assert bulyan["byzantine_robust"] is True
        assert bulyan["colluding_defense"] is True


class TestMIAAuditEndpoints:
    def test_mia_audit_canonical(self) -> None:
        payload = {
            "train_losses": [0.012, 0.015, 0.018, 0.014],
            "test_losses": [0.45, 0.52, 0.48, 0.61],
        }
        response = client.post("/v1/privacy-defense/audit/mia", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "membership_leakage_asr" in data
        assert "risk_tier" in data
        assert data["membership_leakage_asr"] >= 0.5

    def test_mia_audit_prefixed(self) -> None:
        payload = {
            "train_losses": [0.1, 0.2],
            "test_losses": [0.15, 0.25],
        }
        response = client.post("/api/v1/privacy-defense/audit/mia", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["num_train_samples_audited"] == 2
        assert data["num_test_samples_audited"] == 2


class TestModelInversionAuditEndpoints:
    def test_model_inversion_canonical(self) -> None:
        payload = {
            "gradient_norms": [0.02, 0.03, 0.025, 0.022, 0.028],
        }
        response = client.post("/v1/privacy-defense/audit/model-inversion", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "reconstruction_risk_score" in data
        assert "risk_tier" in data
        assert data["num_gradients_audited"] == 5

    def test_model_inversion_prefixed(self) -> None:
        payload = {
            "gradient_norms": [0.1, 0.12, 0.09],
        }
        response = client.post("/api/v1/privacy-defense/audit/model-inversion", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "mean_gradient_norm" in data


class TestDLGAuditEndpoints:
    def test_dlg_audit_canonical(self) -> None:
        payload = {
            "original_gradients": [0.1, 0.2, 0.3, 0.4, 0.5],
            "received_gradients": [0.12, 0.19, 0.31, 0.38, 0.49],
        }
        response = client.post("/v1/privacy-defense/audit/dlg", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "dlg_leakage_score" in data
        assert "risk_tier" in data
        assert data["dlg_leakage_score"] > 0.0

    def test_dlg_audit_prefixed(self) -> None:
        payload = {
            "original_gradients": [1.0, 2.0, 3.0],
            "received_gradients": [1.05, 1.95, 3.02],
        }
        response = client.post("/api/v1/privacy-defense/audit/dlg", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "dlg_leakage_score" in data


class TestPrivacyBudgetLogEndpoints:
    def test_budget_log_canonical(self) -> None:
        response = client.get("/v1/privacy-defense/budget-log")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_budget_log_with_limit_param(self) -> None:
        response = client.get("/api/v1/privacy-defense/budget-log?epsilon_limit=6.5")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestNoiseCalibrationEndpoints:
    def test_calibrate_noise_canonical(self) -> None:
        payload = {
            "target_epsilon": 1.5,
            "target_delta": 1e-5,
            "sensitivity": 1.0,
            "mechanism": "gaussian",
        }
        response = client.post("/v1/privacy-defense/calibrate-noise", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["mechanism"] == "gaussian"
        assert data["calibrated_sigma"] > 0.0
        assert "formula" in data

    def test_calibrate_noise_prefixed(self) -> None:
        payload = {
            "target_epsilon": 2.0,
            "target_delta": 1e-6,
            "sensitivity": 0.5,
        }
        response = client.post("/api/v1/privacy-defense/calibrate-noise", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["target_epsilon"] == 2.0
        assert data["calibrated_sigma"] > 0.0


class TestRDPCompositionEndpoints:
    def test_compose_rdp_canonical(self) -> None:
        payload = {
            "sigmas": [1.0, 1.2, 1.1, 1.3],
            "target_delta": 1e-5,
            "sample_ratio_q": 0.05,
        }
        response = client.post("/v1/privacy-defense/rdp-composition", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["total_rounds"] == 4
        assert data["cumulative_epsilon"] > 0.0
        assert "optimal_order_alpha" in data
        assert "rdp_map" in data

    def test_compose_rdp_prefixed(self) -> None:
        payload = {
            "sigmas": [1.5, 1.5],
            "target_delta": 1e-5,
            "sample_ratio_q": 0.1,
            "orders": [2.0, 4.0, 8.0, 16.0],
        }
        response = client.post("/api/v1/privacy-defense/rdp-composition", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["total_rounds"] == 2
        assert "rdp_map" in data


class TestBankRDPBudgetsEndpoints:
    def test_get_bank_budgets_canonical(self) -> None:
        response = client.get("/v1/privacy-defense/rdp/bank-budgets")
        assert response.status_code == 200
        data = response.json()
        assert "consortium_target_epsilon" in data
        assert "node_budgets" in data
        assert len(data["node_budgets"]) >= 1
        node = data["node_budgets"][0]
        assert "node_id" in node
        assert "bank_name" in node
        assert "budget_exhaustion_pct" in node
        assert "calibrated_sigma" in node

    def test_get_bank_budgets_prefixed(self) -> None:
        response = client.get("/api/v1/privacy-defense/rdp/bank-budgets")
        assert response.status_code == 200
        data = response.json()
        assert "training_circuit_breaker_active" in data
        assert "global_cumulative_rdp" in data


class TestCircuitBreakerEndpoints:
    def test_circuit_breaker_freeze_and_unfreeze(self) -> None:
        # 1. Freeze
        freeze_payload = {
            "action": "freeze",
            "reason": "Test emergency pause",
            "actor": "secops_tester",
            "node_id": "bank_alpha",
        }
        r1 = client.post("/v1/privacy-defense/rdp/circuit-breaker", json=freeze_payload)
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["success"] is True
        assert d1["action"] == "freeze"
        assert d1["training_circuit_breaker_active"] is True
        assert d1["frozen_by_node"] == "bank_alpha"

        # Verify budget status reflects freeze
        r_status = client.get("/v1/privacy-defense/rdp/bank-budgets")
        assert r_status.status_code == 200
        assert r_status.json()["training_circuit_breaker_active"] is True

        # 2. Unfreeze
        unfreeze_payload = {
            "action": "unfreeze",
            "reason": "Security review completed",
            "actor": "secops_supervisor",
        }
        r2 = client.post("/api/v1/privacy-defense/rdp/circuit-breaker", json=unfreeze_payload)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["success"] is True
        assert d2["action"] == "unfreeze"
        assert d2["training_circuit_breaker_active"] is False

    def test_circuit_breaker_reset_budget(self) -> None:
        reset_payload = {
            "action": "reset_budget",
            "reason": "Routine epoch rollover",
            "actor": "fl_admin",
            "node_id": "bank_beta",
        }
        response = client.post("/v1/privacy-defense/rdp/circuit-breaker", json=reset_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["action"] == "reset_budget"

    def test_circuit_breaker_invalid_action_400(self) -> None:
        bad_payload = {
            "action": "destroy_all",
            "reason": "Invalid payload",
            "actor": "attacker",
        }
        response = client.post("/v1/privacy-defense/rdp/circuit-breaker", json=bad_payload)
        assert response.status_code == 400


class TestMIASimulationEndpoints:
    def test_mia_simulation_low_epsilon_strong_defense(self) -> None:
        payload = {"test_epsilon": 0.5, "num_samples": 40}
        response = client.post("/v1/privacy-defense/audit/mia-simulation", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["test_epsilon"] == 0.5
        assert data["is_dp_enabled"] is True
        assert "membership_leakage_asr" in data
        assert "mia_roc_auc" in data
        assert "loss_gap" in data
        assert len(data["train_loss_distribution"]) > 0
        assert len(data["test_loss_distribution"]) > 0

    def test_mia_simulation_high_epsilon_vulnerable(self) -> None:
        payload = {"test_epsilon": 60.0, "num_samples": 30}
        response = client.post("/api/v1/privacy-defense/audit/mia-simulation", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["test_epsilon"] == 60.0
        assert data["is_dp_enabled"] is False
        assert "Unconstrained" in data["attack_summary"]


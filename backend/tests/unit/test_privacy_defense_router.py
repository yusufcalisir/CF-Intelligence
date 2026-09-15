"""Unit tests for the Privacy Defense & Attack Benchmarking API router."""

from __future__ import annotations

import fastapi
from fastapi.testclient import TestClient

from app.presentation.routers.privacy_defense import router

_app = fastapi.FastAPI()
_app.include_router(router)
client = TestClient(_app)


class TestAggregationMethodsEndpoint:
    def test_returns_200(self) -> None:
        response = client.get("/api/v1/privacy-defense/aggregation-methods")
        assert response.status_code == 200

    def test_contains_bulyan_and_trimmed_mean(self) -> None:
        response = client.get("/api/v1/privacy-defense/aggregation-methods")
        ids = [m["id"] for m in response.json()]
        assert "bulyan" in ids
        assert "trimmed_mean" in ids

    def test_bulyan_marked_colluding_defense(self) -> None:
        response = client.get("/api/v1/privacy-defense/aggregation-methods")
        bulyan = next(m for m in response.json() if m["id"] == "bulyan")
        assert bulyan["byzantine_robust"] is True
        assert bulyan["colluding_defense"] is True

    def test_fed_avg_not_byzantine_robust(self) -> None:
        response = client.get("/api/v1/privacy-defense/aggregation-methods")
        fed_avg = next(m for m in response.json() if m["id"] == "fed_avg")
        assert fed_avg["byzantine_robust"] is False


class TestMIAAuditEndpoint:
    def test_mia_audit_returns_200(self) -> None:
        payload = {
            "train_losses": [0.01, 0.02, 0.015],
            "test_losses": [0.5, 0.6, 0.55],
        }
        response = client.post("/api/v1/privacy-defense/audit/mia", json=payload)
        assert response.status_code == 200

    def test_mia_audit_has_required_fields(self) -> None:
        payload = {
            "train_losses": [0.01, 0.02],
            "test_losses": [0.5, 0.6],
        }
        response = client.post("/api/v1/privacy-defense/audit/mia", json=payload)
        data = response.json()
        assert "membership_leakage_asr" in data
        assert "risk_tier" in data

    def test_mia_audit_empty_lists(self) -> None:
        payload = {"train_losses": [], "test_losses": []}
        response = client.post("/api/v1/privacy-defense/audit/mia", json=payload)
        assert response.status_code == 200
        assert response.json()["risk_tier"] == "safe"


class TestModelInversionEndpoint:
    def test_model_inversion_returns_200(self) -> None:
        payload = {"gradient_norms": [1.0, 1.5, 0.8, 1.2, 0.9]}
        response = client.post("/api/v1/privacy-defense/audit/model-inversion", json=payload)
        assert response.status_code == 200

    def test_model_inversion_has_required_fields(self) -> None:
        payload = {"gradient_norms": [1.0, 2.0, 1.5]}
        response = client.post("/api/v1/privacy-defense/audit/model-inversion", json=payload)
        data = response.json()
        assert "reconstruction_risk_score" in data
        assert "risk_tier" in data
        assert "num_gradients_audited" in data

    def test_model_inversion_empty_norms(self) -> None:
        payload = {"gradient_norms": []}
        response = client.post("/api/v1/privacy-defense/audit/model-inversion", json=payload)
        assert response.status_code == 200
        assert response.json()["risk_tier"] == "safe"


class TestDLGAuditEndpoint:
    def test_dlg_returns_200(self) -> None:
        payload = {
            "original_gradients": [1.0, 2.0, 3.0],
            "received_gradients": [1.0, 2.0, 3.0],
        }
        response = client.post("/api/v1/privacy-defense/audit/dlg", json=payload)
        assert response.status_code == 200

    def test_dlg_perfect_correlation_high_risk(self) -> None:
        g = [float(i) for i in range(1, 11)]
        payload = {"original_gradients": g, "received_gradients": g}
        response = client.post("/api/v1/privacy-defense/audit/dlg", json=payload)
        data = response.json()
        assert data["dlg_leakage_score"] >= 0.99
        assert data["risk_tier"] == "high_risk"

    def test_dlg_empty_inputs(self) -> None:
        payload = {"original_gradients": [], "received_gradients": []}
        response = client.post("/api/v1/privacy-defense/audit/dlg", json=payload)
        assert response.status_code == 200
        assert response.json()["dlg_leakage_score"] == 0.0


class TestBudgetLogEndpoint:
    def test_budget_log_returns_200(self) -> None:
        response = client.get("/api/v1/privacy-defense/budget-log")
        assert response.status_code == 200

    def test_budget_log_returns_list(self) -> None:
        response = client.get("/api/v1/privacy-defense/budget-log")
        assert isinstance(response.json(), list)

    def test_budget_log_epsilon_limit_param(self) -> None:
        response = client.get("/api/v1/privacy-defense/budget-log?epsilon_limit=5.0")
        assert response.status_code == 200


class TestNoiseCalibrationAndRDPCompositionEndpoints:
    def test_calibrate_noise_success(self) -> None:
        payload = {
            "target_epsilon": 1.0,
            "target_delta": 1e-5,
            "sensitivity": 1.0,
            "mechanism": "gaussian",
        }
        response = client.post("/api/v1/privacy-defense/calibrate-noise", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["calibrated_sigma"] > 0
        assert data["mechanism"] == "gaussian"
        assert "formula" in data

    def test_calibrate_noise_validation_error(self) -> None:
        payload = {
            "target_epsilon": -1.0,
            "target_delta": 1e-5,
            "sensitivity": 1.0,
        }
        response = client.post("/api/v1/privacy-defense/calibrate-noise", json=payload)
        assert response.status_code == 422

    def test_rdp_composition_success(self) -> None:
        payload = {
            "sigmas": [1.2, 1.2, 1.2, 1.2, 1.2],
            "target_delta": 1e-5,
            "sample_ratio_q": 0.05,
        }
        response = client.post("/api/v1/privacy-defense/rdp-composition", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["total_rounds"] == 5
        assert data["cumulative_epsilon"] > 0
        assert data["optimal_order_alpha"] > 1.0
        assert data["privacy_saving_pct"] >= 0.0
        assert "2.0" in data["rdp_map"]

    def test_rdp_composition_invalid_sigmas(self) -> None:
        payload = {
            "sigmas": [],
            "target_delta": 1e-5,
        }
        response = client.post("/api/v1/privacy-defense/rdp-composition", json=payload)
        assert response.status_code == 422

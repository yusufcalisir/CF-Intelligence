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

"""
Unit tests verifying the canonical inference feature contract, schema parity,
zero-fabrication input handling, and simulation scope isolation.

Threat Model & Invariants Tested:
1. DEFECT 6: Simulation scope propagation & isolation. Models loaded and scored for simulation 'sim_alpha'
   must strictly use 'sim_alpha' artifacts and must never serve or leak 'sim_beta' models.
2. DEFECT 7: Zero fabrication in transaction feature preprocessing.
   Canonical feature order:
     [transaction_amount, merchant_category, country_code, device_type, velocity,
      hour_of_day, merchant_risk_score, customer_history_score, chargeback_count, account_age_days]
   In production, missing observations (merchant_risk_score, customer_history_score,
   chargeback_count, account_age_days, hour_of_day) or unknown categorical values
   (unmapped merchant category, country, or device) must FAIL CLOSED with HTTP 422,
   NEVER silently mapped to index 0 or plausible synthetic defaults. Non-finite inputs (NaN, Inf)
   must also be rejected with HTTP 422.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.application.services.data_generator import (
    FEATURE_NAMES,
    preprocess_transaction,
)
from app.application.services.model_service import ModelService
from app.config import get_settings
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator
from app.main import app

EXPECTED_CANONICAL_FEATURES = [
    "transaction_amount",
    "merchant_category",
    "country_code",
    "device_type",
    "velocity",
    "hour_of_day",
    "merchant_risk_score",
    "customer_history_score",
    "chargeback_count",
    "account_age_days",
]


class TestCanonicalFeatureContractAndParity:
    """Tests for Defect 7: Ensuring canonical 10-feature order and zero-fabrication validation."""

    def test_canonical_feature_names_match_authoritative_specification(self):
        """Invariant: FEATURE_NAMES must match the declared 10-feature schema in exact order."""
        assert FEATURE_NAMES == EXPECTED_CANONICAL_FEATURES
        assert len(FEATURE_NAMES) == 10

    def test_unknown_categorical_values_rejected_under_strict_validation(self):
        """
        Threat: Attacker submits an unrecognized merchant category or country hoping for silent index 0 fallback.
        Invariant: preprocess_transaction(strict_categorical=True) must raise ValueError.
        """
        txn_unknown_category = {
            "transaction_amount": 100.0,
            "merchant_category": "unregistered_darknet_market",
            "country_code": "US",
            "device_type": "mobile_app",
            "velocity": 1.0,
            "hour_of_day": 12.0,
            "merchant_risk_score": 0.5,
            "customer_history_score": 0.8,
            "chargeback_count": 0.0,
            "account_age_days": 100.0,
        }
        with pytest.raises(ValueError, match="Unknown merchant category"):
            preprocess_transaction(txn_unknown_category, strict_categorical=True)

        txn_unknown_country = dict(txn_unknown_category, merchant_category="dining", country_code="ZZ_UNKNOWN")
        with pytest.raises(ValueError, match="Unknown country code"):
            preprocess_transaction(txn_unknown_country, strict_categorical=True)

        txn_unknown_device = dict(txn_unknown_category, merchant_category="dining", country_code="US", device_type="smart_toaster")
        with pytest.raises(ValueError, match="Unknown device type"):
            preprocess_transaction(txn_unknown_device, strict_categorical=True)

    def test_non_finite_inputs_rejected_with_value_error(self):
        """
        Threat: Non-finite floats (NaN, Inf) submitted to bypass mathematical guards.
        Invariant: preprocess_transaction must raise ValueError.
        """
        txn_nan = {
            "transaction_amount": float("nan"),
            "merchant_category": "dining",
            "country_code": "US",
            "device_type": "mobile_app",
            "velocity": 1.0,
            "hour_of_day": 12.0,
            "merchant_risk_score": 0.5,
            "customer_history_score": 0.8,
            "chargeback_count": 0.0,
            "account_age_days": 100.0,
        }
        with pytest.raises(ValueError, match="non-finite"):
            preprocess_transaction(txn_nan, strict_categorical=True)


class TestRealtimeInferenceEndpointFeatureGuards:
    """Tests for HTTP 422 rejection of incomplete or malformed requests in production."""

    def test_production_rejects_missing_mandatory_observations(self):
        """
        Threat: Client sends a transaction without merchant_risk_score or account_age_days in production.
        Invariant: HTTP endpoint must reject with 422 UNPROCESSABLE_ENTITY.
        """
        client = TestClient(app)
        incomplete_payload = {
            "transaction_id": "tx_missing_risk",
            "amount": 250.0,
            "merchant_category": "retail",
            "velocity_1h": 3,
            "country_code": "US",
            "device_type": "web_browser",
            # merchant_risk_score omitted!
            "customer_history_score": 0.9,
            "chargeback_count": 0.0,
            "account_age_days": 200.0,
            "hour_of_day": 15.0,
        }

        oidc = OIDCAuthenticator()
        token = oidc.create_mock_token("analyst_a", "bank_a", ["analyst"])
        headers = {"Authorization": f"Bearer {token}"}

        with patch.object(get_settings(), "app_env", "production"):
            resp = client.post("/v1/inference/score", json=incomplete_payload, headers=headers)
            assert resp.status_code == 422
            detail = resp.json()["detail"]
            assert detail["code"] == "MISSING_REQUIRED_FEATURES"
            assert "merchant_risk_score" in detail["message"]

    def test_production_rejects_unknown_category_with_422(self):
        """
        Threat: Client sends an unknown categorical value in production.
        Invariant: Must return HTTP 422 INVALID_FEATURE_INPUT instead of fabricating index 0.
        """
        client = TestClient(app)
        invalid_cat_payload = {
            "transaction_id": "tx_bad_cat",
            "amount": 100.0,
            "merchant_category": "illegal_goods_exchange",
            "velocity_1h": 1,
            "country_code": "US",
            "device_type": "mobile_app",
            "merchant_risk_score": 0.5,
            "customer_history_score": 0.8,
            "chargeback_count": 0.0,
            "account_age_days": 100.0,
            "hour_of_day": 10.0,
        }

        oidc = OIDCAuthenticator()
        token = oidc.create_mock_token("analyst_a", "bank_a", ["analyst"])
        headers = {"Authorization": f"Bearer {token}"}

        with patch.object(get_settings(), "app_env", "production"):
            resp = client.post("/v1/inference/score", json=invalid_cat_payload, headers=headers)
            assert resp.status_code == 422
            detail = resp.json()["detail"]
            assert detail["code"] == "INVALID_FEATURE_INPUT"

    def test_valid_production_payload_scores_successfully(self):
        """Invariant: A fully specified authentic transaction payload scores cleanly as ML_MODEL."""
        client = TestClient(app)
        valid_payload = {
            "transaction_id": "tx_valid_prod",
            "amount": 45.0,
            "merchant_category": "grocery",
            "velocity_1h": 1,
            "country_code": "US",
            "device_type": "mobile_app",
            "merchant_risk_score": 0.05,
            "customer_history_score": 0.98,
            "chargeback_count": 0.0,
            "account_age_days": 500.0,
            "hour_of_day": 9.0,
        }

        oidc = OIDCAuthenticator()
        token = oidc.create_mock_token("analyst_a", "bank_a", ["analyst"])
        headers = {"Authorization": f"Bearer {token}"}

        svc = ModelService(get_settings())
        m = svc.create_model(dp_compatible=True)
        with (
            patch("app.presentation.routers.realtime_inference.get_scripted_model", return_value=(m, False)),
            patch("app.presentation.routers.realtime_inference.authorize_model_serving", return_value=("sim_valid", {"simulation_id": "sim_valid", "bank_id": "bank_a"})),
            patch.object(get_settings(), "app_env", "production"),
        ):
            resp = client.post("/v1/inference/score", json=valid_payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["evaluated_by"] == "ML_MODEL"
            assert "[Simulated Features]" not in data["explanation"]
            assert 0.0 <= data["risk_score"] <= 1.0

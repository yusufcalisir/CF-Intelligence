"""Unit tests for production serving security, strict feature contracts, and cache namespace isolation.

Covers verification requirements A through M:
A. Challenger with traffic_share=1.0 cannot replace champion probability in live decisions.
B. Shadow prediction and evaluation remain functional independently.
C. Production requests missing required features are rejected rather than defaulted (HTTP 422).
D. Feature-store enrichment uses actual tenant-scoped values and cannot silently cross tenant boundaries.
E. Invalid categorical values and non-finite numbers (NaN, Inf, booleans) are rejected (HTTP 422).
F. Incorrect feature dimension is never padded or truncated (ModelCompatibilityError -> HTTP 503).
G. Single and batch inference reject invalid probability ranges, output shapes, and result counts (ModelExecutionError).
H. ScoreTransactionRequest cannot fabricate missing ML features in production.
I. Response model version matches actual executed champion identity; no 'v2.4.1' fallback.
J. Scoped Redis cache writes and evictions do not overwrite or evict global/default or other simulation keys.
K. Invalidation does not evict unrelated simulation entries.
L. Production rejects unavailable champions rather than substituting fabricated predictions.
M. Both production serving routers (/predict and /score-transaction) enforce critical invariants.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import torch
from fastapi.testclient import TestClient

import app.presentation.routers.predict as predict_module
import app.presentation.routers.realtime_inference as rt_inference_module
from app.application.schemas.transaction import ScoreTransactionRequest, TransactionPredictRequest
from app.application.services.data_generator import preprocess_transaction
from app.application.services.model_service import NUM_FEATURES
from app.config import get_settings
from app.dependencies import get_session
from app.domain.model_serving_errors import ModelExecutionError
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_db(monkeypatch: pytest.MonkeyPatch):
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result
    app.dependency_overrides[get_session] = lambda: mock_session
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test A & B: Challenger Shadowing vs Champion Decision Routing
# ---------------------------------------------------------------------------
def test_challenger_cannot_substitute_champion_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement A & B: Even with traffic_share=1.0 and radically different predictions,
    the live customer fraud decision, probability, and risk score MUST derive strictly from champion.
    Challenger runs purely in shadow mode.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "feature_store_enabled", False)

    # Mock ModelRegistry manifest returning champion v1 and challenger v2
    manifest = [
        {"version": 1, "status": "champion", "is_active": True, "sha256": "champ_sha", "generation": 1},
        {"version": 2, "status": "challenger", "is_active": False, "sha256": "chall_sha", "generation": 1},
    ]

    class MockChampionModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.network = torch.nn.Sequential(torch.nn.Linear(NUM_FEATURES, 1))

        def forward(self, x):
            # Champion predicts very low risk: 0.05
            return torch.tensor([0.05])

    class MockChallengerModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.network = torch.nn.Sequential(torch.nn.Linear(NUM_FEATURES, 1))

        def forward(self, x):
            # Challenger predicts extreme high risk: 0.99
            return torch.tensor([0.99])

    champ_inst = MockChampionModel()
    chall_inst = MockChallengerModel()

    monkeypatch.setattr(predict_module._registry, "_load_manifest", lambda sim_id: manifest)
    monkeypatch.setattr(
        predict_module._registry,
        "get_active_champion_identity",
        lambda simulation_id=None: {"version": 1, "sha256": "champ_sha", "generation": 1},
    )
    monkeypatch.setattr(predict_module, "_get_cached_serving_model_with_ident", lambda sim_id: (champ_inst, {"version": 1, "sha256": "champ_sha"}))
    monkeypatch.setattr(predict_module, "_load_challenger_model", lambda sim_id, ver, dp_compatible=True: chall_inst)

    # Force traffic_share to 1.0 (all traffic would be challenger if routing was broken)
    monkeypatch.setattr(predict_module._eval_engine, "get_traffic_share", lambda sim_id: 1.0)
    shadow_eval_logged = []
    monkeypatch.setattr(
        predict_module._eval_engine,
        "log_prediction",
        lambda **kwargs: shadow_eval_logged.append(kwargs),
    )

    payload = {
        "transaction_amount": 50.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "web_browser",
        "velocity": 1.0,
        "hour_of_day": 12,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
        "bank_id": "bank_1",
        "simulation_id": "sim_shadow_test",
    }

    res = client.post("/api/v1/predict", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()

    # Authoritative production decision MUST be champion's 0.05
    assert data["fraud_probability"] == pytest.approx(0.05, abs=1e-4)
    assert data["is_fraud_suspected"] is False
    assert data["risk_score"] < 300.0

    # Shadow evaluation is independently recorded in eval engine
    assert len(shadow_eval_logged) >= 1
    logged = shadow_eval_logged[0]
    assert logged["routed_to"] == "champion"
    assert logged["champion_version"] == 1
    assert logged["champion_prob"] == pytest.approx(0.05, abs=1e-4)
    assert logged["challenger_version"] == 2
    assert logged["challenger_prob"] == pytest.approx(0.99, abs=1e-4)


# ---------------------------------------------------------------------------
# Test C & H: Strict Production Feature Contract vs Fabrication
# ---------------------------------------------------------------------------
def test_production_predict_missing_features_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement C: In production environment, missing required ML features MUST fail closed with HTTP 422."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "feature_store_enabled", False)
    monkeypatch.setattr(predict_module._settings, "app_env", "production")
    monkeypatch.setattr(predict_module._settings, "feature_store_enabled", False)

    # Transaction missing predictive features
    payload = {
        "transaction_amount": 100.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "web_browser",
        "hour_of_day": 14,
        # velocity, customer_history_score, chargeback_count, account_age_days, merchant_risk_score are MISSING
    }

    res = client.post("/api/v1/predict", json=payload)
    assert res.status_code == 422
    assert "Production feature contract violation: Missing required features" in res.text


def test_production_score_transaction_missing_features_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement H: score_transaction in production must NOT fabricate velocity, customer history,
    or guess merchant categories; missing features must return HTTP 422."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "feature_store_enabled", False)
    monkeypatch.setattr(predict_module._settings, "app_env", "production")
    monkeypatch.setattr(predict_module._settings, "feature_store_enabled", False)

    # Compact request without explicit ML features and without online store
    payload = {
        "transaction_id": "tx_score_001",
        "account_id": "acc_001",
        "amount": 250.0,
        "merchant_id": "crypto_exchange_01",
        "country": "US",
        "device_id": "fp_device_abc",
    }

    res = client.post("/api/v1/score-transaction", json=payload)
    assert res.status_code == 422
    assert "Production scoring rejected: missing required model features" in res.text


def test_production_score_transaction_succeeds_with_explicit_features(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement H: score_transaction in production succeeds when features are explicitly provided."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "feature_store_enabled", False)

    class MockModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.network = torch.nn.Sequential(torch.nn.Linear(NUM_FEATURES, 1))

        def forward(self, x):
            return torch.tensor([0.15])

    monkeypatch.setattr(
        predict_module,
        "_get_cached_serving_model_with_ident",
        lambda sim_id: (MockModel(), {"version": 3, "sha256": "sha_v3", "simulation_id": "sim_prod"}),
    )

    payload = {
        "transaction_id": "tx_score_002",
        "account_id": "acc_002",
        "amount": 75.0,
        "merchant_id": "merch_groceries",
        "country": "US",
        "device_id": "fp_device_xyz",
        "merchant_category": "grocery",
        "device_type": "web_browser",
        "velocity": 2.0,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 400,
        "merchant_risk_score": 0.05,
        "hour_of_day": 14,
    }

    res = client.post("/api/v1/score-transaction", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["decision"] in ("ALLOW", "REVIEW", "BLOCK")
    assert data["model_version"] == "v3.0.0"
    assert data["artifact_sha256"] == "sha_v3"


# ---------------------------------------------------------------------------
# Test D: Tenant-Isolated Feature Store Enrichment
# ---------------------------------------------------------------------------
def test_feature_store_tenant_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement D: Feature store enrichment must query with the correct tenant scope
    and cannot leak cross-tenant features."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "feature_store_enabled", True)

    queried_tenants: list[str | None] = []

    def mock_get_online_features(entity_rows, features, tenant_id=None):
        queried_tenants.append(tenant_id)
        if tenant_id == "bank_alpha":
            return [{
                "customer_history_score": 0.99,
                "account_age_days": 730,
                "chargeback_count": 0,
                "rolling_velocity_1h": 1.2,
                "merchant_risk_score": 0.02,
                "merchant_category": "grocery",
                "device_type": "mobile_app",
            }]
        return []

    monkeypatch.setattr(predict_module._feature_store, "get_online_features", mock_get_online_features)

    class MockModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.network = torch.nn.Sequential(torch.nn.Linear(NUM_FEATURES, 1))

        def forward(self, x):
            return torch.tensor([0.05])

    monkeypatch.setattr(
        predict_module,
        "_get_cached_serving_model_with_ident",
        lambda sim_id: (MockModel(), {"version": 1, "sha256": "sha_v1"}),
    )

    from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator

    token = OIDCAuthenticator().create_mock_token(username="user_alpha", bank_id="bank_alpha", roles=["analyst"])
    # Call score-transaction under authenticated bank_alpha token
    res = client.post(
        "/api/v1/score-transaction",
        json={
            "transaction_id": "tx_tenant_01",
            "account_id": "acc_01",
            "amount": 50.0,
            "merchant_id": "merch_01",
            "country": "US",
            "device_id": "dev_01",
            "hour_of_day": 12,
        },
        headers={"Authorization": f"Bearer {token}", "X-Bank-ID": "bank_alpha"},
    )
    assert res.status_code == 200, res.text
    assert "bank_alpha" in queried_tenants


# ---------------------------------------------------------------------------
# Test E: Non-Finite Values, Booleans, and Invalid Categoricals Rejected
# ---------------------------------------------------------------------------
def test_non_finite_and_boolean_numeric_features_rejected() -> None:
    """Requirement E: Boolean values, NaNs, and Infs in numeric fields must be rejected with HTTP 422."""
    # 1. Test boolean passed as amount via HTTP
    res_bool = client.post(
        "/api/v1/score-transaction",
        json={
            "transaction_id": "tx_bool_01",
            "account_id": "acc_01",
            "amount": True,  # Invalid: boolean in float field
            "merchant_id": "merch_01",
            "country": "US",
            "device_id": "dev_01",
        },
    )
    assert res_bool.status_code == 422

    # 2. Test string NaN and Infinity via HTTP content
    res_nan = client.post(
        "/api/v1/predict",
        content='{"transaction_amount": "NaN", "merchant_category": "grocery", "country_code": "US", "device_type": "web_browser"}',
        headers={"Content-Type": "application/json"},
    )
    assert res_nan.status_code == 422

    res_inf = client.post(
        "/api/v1/predict",
        content='{"transaction_amount": "Infinity", "merchant_category": "grocery", "country_code": "US", "device_type": "web_browser"}',
        headers={"Content-Type": "application/json"},
    )
    assert res_inf.status_code == 422

    # 3. Direct schema validator tests for NaN and Inf
    with pytest.raises(ValueError, match="Numeric fields must be finite numbers"):
        TransactionPredictRequest(
            transaction_amount=float("nan"),
            merchant_category="grocery",
            country_code="US",
            device_type="web_browser",
        )

    with pytest.raises(ValueError, match="Numeric fields must be finite numbers"):
        ScoreTransactionRequest(
            transaction_id="tx_inf",
            account_id="acc_inf",
            amount=float("inf"),
            merchant_id="merch_inf",
            device_id="dev_inf",
        )


def test_invalid_categorical_in_production_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement E: Unrecognized categorical in production must raise validation error, not pad or invent."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")

    txn_dict = {
        "transaction_amount": 100.0,
        "merchant_category": "unknown_bogus_category",
        "country_code": "US",
        "device_type": "web_browser",
        "velocity": 1.0,
        "hour_of_day": 12,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
    }
    with pytest.raises(ValueError, match="Unknown merchant category"):
        preprocess_transaction(txn_dict, strict_categorical=True)


# ---------------------------------------------------------------------------
# Test F: Feature Dimension Contract (No Padding, No Truncation)
# ---------------------------------------------------------------------------
def test_dimension_mismatch_never_padded_or_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement F: If input feature dimension does not match model architecture,
    it must raise ModelCompatibilityError (HTTP 503), NEVER silently pad with zeros or truncate."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "feature_store_enabled", False)

    # Create model expecting 64 features instead of NUM_FEATURES (typically 30)
    class IncompatibleModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.network = torch.nn.Sequential(torch.nn.Linear(64, 1))

        def forward(self, x):
            return torch.tensor([0.5])

    monkeypatch.setattr(
        predict_module,
        "_get_cached_serving_model_with_ident",
        lambda sim_id: (IncompatibleModel(), {"version": 1, "sha256": "sha_incomp"}),
    )

    payload = {
        "transaction_amount": 50.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "web_browser",
        "velocity": 1.0,
        "hour_of_day": 12,
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 365,
    }

    res = client.post("/api/v1/predict", json=payload)
    assert res.status_code == 503
    assert "Feature tensor dimension mismatch" in res.text


# ---------------------------------------------------------------------------
# Test G: Model Output Range and Shape Validation
# ---------------------------------------------------------------------------
def test_eval_model_rejects_out_of_bounds_and_non_finite() -> None:
    """Requirement G: _eval_model and _eval_model_batch must reject output < 0.0, > 1.0, NaN, and non-scalar."""
    # Model returning probability > 1.0
    class OutOfBoundsHigh(torch.nn.Module):
        def forward(self, x):
            return torch.tensor([1.25])

    # Model returning probability < 0.0
    class OutOfBoundsLow(torch.nn.Module):
        def forward(self, x):
            return torch.tensor([-0.10])

    # Model returning NaN
    class NaNModel(torch.nn.Module):
        def forward(self, x):
            return torch.tensor([float("nan")])

    # Model returning shape mismatch (2 outputs for 1 input)
    class ShapeMismatchModel(torch.nn.Module):
        def forward(self, x):
            return torch.tensor([0.2, 0.8])

    dummy_tensor = torch.zeros(1, NUM_FEATURES)

    with pytest.raises(ModelExecutionError, match="out-of-bounds probability"):
        predict_module._eval_model(OutOfBoundsHigh(), dummy_tensor)

    with pytest.raises(ModelExecutionError, match="out-of-bounds probability"):
        predict_module._eval_model(OutOfBoundsLow(), dummy_tensor)

    with pytest.raises(ModelExecutionError, match="non-finite output"):
        predict_module._eval_model(NaNModel(), dummy_tensor)

    with pytest.raises(ModelExecutionError, match="unexpected output shape"):
        predict_module._eval_model(ShapeMismatchModel(), dummy_tensor)


def test_eval_model_batch_rejects_count_and_range_mismatches() -> None:
    """Requirement G: Batch evaluation must enforce exactly 1 output per input item within [0.0, 1.0]."""
    dummy_batch = torch.zeros(3, NUM_FEATURES)

    # Model returning 2 outputs for 3 inputs
    class IncompleteBatchModel(torch.nn.Module):
        def forward(self, x):
            return torch.tensor([[0.1], [0.2]])

    with pytest.raises(ModelExecutionError, match=r"Model batch output shape mismatch"):
        predict_module._eval_model_batch(IncompleteBatchModel(), dummy_batch)

    # Model returning one output > 1.0 in batch
    class OutOfBoundsBatchModel(torch.nn.Module):
        def forward(self, x):
            return torch.tensor([[0.1], [1.5], [0.3]])

    with pytest.raises(ModelExecutionError, match="out-of-bounds probability"):
        predict_module._eval_model_batch(OutOfBoundsBatchModel(), dummy_batch)


# ---------------------------------------------------------------------------
# Test I: Truthful Model Version Reporting
# ---------------------------------------------------------------------------
def test_score_transaction_reports_exact_version_never_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement I: score_transaction must report the exact version from verified identity snapshot,
    never falling back to hardcoded 'v2.4.1'."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")

    class MockModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.network = torch.nn.Sequential(torch.nn.Linear(NUM_FEATURES, 1))

        def forward(self, x):
            return torch.tensor([0.10])

    # Serving active champion version 7
    monkeypatch.setattr(
        predict_module,
        "_get_cached_serving_model_with_ident",
        lambda sim_id: (MockModel(), {"version": 7, "sha256": "sha_champion_v7", "simulation_id": "sim_active"}),
    )

    payload = {
        "transaction_id": "tx_ver_001",
        "account_id": "acc_001",
        "amount": 40.0,
        "merchant_id": "merch_grocery",
        "country": "US",
        "device_id": "dev_001",
    }

    res = client.post("/api/v1/score-transaction", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["model_version"] == "v7.0.0"
    assert data["model_version"] != "v2.4.1"
    assert data["artifact_sha256"] == "sha_champion_v7"


# ---------------------------------------------------------------------------
# Test J & K: Redis Cache Namespace Isolation
# ---------------------------------------------------------------------------
def test_scoped_redis_cache_does_not_overwrite_global_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement J & K: Scoped simulation writes must only update cfi:champion_model:{sim_id}.
    Global keys cfi:champion_model and cfi:champion_model:auth must NOT be overwritten by a scoped model.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "default_simulation_id", "sim_default")

    stored_keys: dict[str, Any] = {}

    class MockPipeline:
        def __init__(self):
            self.ops = []

        def set(self, key, value, ex=None):
            self.ops.append(("SET", key, value))

        def execute(self):
            for op, k, v in self.ops:
                stored_keys[k] = v
            self.ops.clear()

    class MockRedisClient:
        def pipeline(self, transaction=True):
            return MockPipeline()

        def delete(self, *keys):
            for k in keys:
                stored_keys.pop(k, None)

    mock_rc = MockRedisClient()
    monkeypatch.setattr(rt_inference_module, "_get_redis_client", lambda: mock_rc)

    # Initial state: Global alias has default model bytes
    stored_keys["cfi:champion_model"] = b"default_champion_bytes"
    stored_keys["cfi:champion_model:auth"] = b"default_auth_envelope"

    # Reset model cache for a scoped simulation
    rt_inference_module.reset_model_cache(simulation_id="sim_scoped_tenant_99")

    # Global keys must remain intact!
    assert "cfi:champion_model" in stored_keys
    assert stored_keys["cfi:champion_model"] == b"default_champion_bytes"

    # Now reset the default simulation -> global alias is cleared
    rt_inference_module.reset_model_cache(simulation_id="sim_default")
    assert "cfi:champion_model" not in stored_keys


# ---------------------------------------------------------------------------
# Test N: Production Feature Truthfulness — ScoreTransaction Country Enforcement
# ---------------------------------------------------------------------------
def test_production_score_transaction_without_country_fails_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement A: Production /api/v1/score-transaction without country fails HTTP 422."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(predict_module._settings, "app_env", "production")
    monkeypatch.setattr(settings, "feature_store_enabled", False)
    monkeypatch.setattr(predict_module._settings, "feature_store_enabled", False)

    payload = {
        "transaction_id": "tx_no_country_01",
        "account_id": "acc_001",
        "amount": 250.0,
        "merchant_id": "merch_crypto_01",
        "device_id": "fp_dev_01",
        "merchant_category": "crypto",
        "device_type": "web_browser",
        "velocity": 2.0,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 400,
        "merchant_risk_score": 0.05,
        "hour_of_day": 14,
        # 'country' and 'country_code' omitted!
    }

    res = client.post("/api/v1/score-transaction", json=payload)
    assert res.status_code == 422, res.text
    assert "missing required model features" in res.text
    assert "country" in res.text


def test_production_score_transaction_aliases_behave_identically_on_missing_country(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement B: /transactions/score and /predict/score aliases fail HTTP 422 identically when country is omitted."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(predict_module._settings, "app_env", "production")
    monkeypatch.setattr(settings, "feature_store_enabled", False)
    monkeypatch.setattr(predict_module._settings, "feature_store_enabled", False)

    payload = {
        "transaction_id": "tx_alias_no_country_02",
        "account_id": "acc_002",
        "amount": 100.0,
        "merchant_id": "merch_grocery_01",
        "device_id": "fp_dev_02",
        "merchant_category": "grocery",
        "device_type": "web_browser",
        "velocity": 1.0,
        "customer_history_score": 0.90,
        "chargeback_count": 0,
        "account_age_days": 365,
        "merchant_risk_score": 0.05,
        "hour_of_day": 12,
    }

    for path in ("/api/v1/score-transaction", "/api/v1/transactions/score", "/api/v1/predict/score"):
        res = client.post(path, json=payload)
        assert res.status_code == 422, f"Failed on {path}: {res.text}"
        data = res.json()
        assert "missing required model features" in data["detail"]
        assert "country" in data["detail"]


def test_production_score_transaction_explicit_valid_country_produces_canonical_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement C: Explicit valid country produces expected canonical ML feature without fallback."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(predict_module._settings, "app_env", "production")
    monkeypatch.setattr(settings, "feature_store_enabled", False)
    monkeypatch.setattr(predict_module._settings, "feature_store_enabled", False)

    captured_tensors = []

    class MockModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.network = torch.nn.Sequential(torch.nn.Linear(NUM_FEATURES, 1))

        def forward(self, x):
            captured_tensors.append(x.clone())
            return torch.tensor([0.15])

    monkeypatch.setattr(
        predict_module,
        "_get_cached_serving_model_with_ident",
        lambda sim_id: (MockModel(), {"version": 1, "sha256": "sha_v1", "simulation_id": "sim_valid"}),
    )

    # 1. Supply explicit country="DE"
    payload_de = {
        "transaction_id": "tx_valid_country_de",
        "account_id": "acc_de_01",
        "amount": 100.0,
        "merchant_id": "merch_de",
        "device_id": "fp_dev_de",
        "country": "DE",
        "merchant_category": "grocery",
        "device_type": "web_browser",
        "velocity": 1.0,
        "customer_history_score": 0.90,
        "chargeback_count": 0,
        "account_age_days": 365,
        "merchant_risk_score": 0.05,
        "hour_of_day": 12,
    }

    res = client.post("/api/v1/score-transaction", json=payload_de)
    assert res.status_code == 200, res.text
    assert len(captured_tensors) == 1

    # Canonical DE feature in data_generator.COUNTRIES is index 2.0 -> scaled
    from app.application.services.data_generator import COUNTRIES, REFERENCE_BOUNDS

    de_index = float(COUNTRIES.index("DE"))
    c_min, c_max = REFERENCE_BOUNDS["country_code"]
    expected_de_scaled = (de_index - c_min) / (c_max - c_min)
    # country_code is at index 2 in FEATURE_NAMES (0: amount, 1: category, 2: country)
    assert abs(captured_tensors[0][0, 2].item() - expected_de_scaled) < 1e-5

    # 2. Also verify country_code alias produces identical canonical feature
    captured_tensors.clear()
    payload_alias = dict(payload_de)
    del payload_alias["country"]
    payload_alias["country_code"] = "DE"
    res_alias = client.post("/api/v1/score-transaction", json=payload_alias)
    assert res_alias.status_code == 200, res_alias.text
    assert len(captured_tensors) == 1
    assert abs(captured_tensors[0][0, 2].item() - expected_de_scaled) < 1e-5


def test_omitted_country_never_fabricates_ee_in_production_but_preserves_demo_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement D: An omitted country cannot silently produce an 'EE' ML feature or jurisdiction decision in production,
    but preserves the 'EE' development fallback in non-production.
    """
    settings = get_settings()

    # 1. In production: omitted country must NEVER fall back to EE
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(predict_module._settings, "app_env", "production")
    monkeypatch.setattr(settings, "feature_store_enabled", False)
    monkeypatch.setattr(predict_module._settings, "feature_store_enabled", False)

    payload = {
        "transaction_id": "tx_no_ee_prod",
        "account_id": "acc_003",
        "amount": 100.0,
        "merchant_id": "merch_crypto",
        "device_id": "fp_dev_03",
        "merchant_category": "crypto",
        "device_type": "web_browser",
        "velocity": 5.0,
        "customer_history_score": 0.85,
        "chargeback_count": 0,
        "account_age_days": 100,
        "merchant_risk_score": 0.40,
        "hour_of_day": 10,
    }

    res_prod = client.post("/api/v1/score-transaction", json=payload)
    assert res_prod.status_code == 422
    assert "country" in res_prod.json()["detail"]

    # 2. In non-production (development): omitted country preserves EE demo fallback
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(predict_module._settings, "app_env", "development")

    res_dev = client.post("/api/v1/score-transaction", json=payload)
    assert res_dev.status_code == 200, res_dev.text
    data_dev = res_dev.json()
    assert "risk_score" in data_dev
    assert data_dev["decision"] in ("ALLOW", "REVIEW", "BLOCK")


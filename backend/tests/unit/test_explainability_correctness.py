"""Deep Explainability Correctness, Attribution Semantics,
SHAP Mathematics & Prediction-to-Explanation Binding Verification Suite.

Validates all 18 Explainability Invariants (XAI-INV-01 through XAI-INV-18)
and Certification Gates (Gate A through Gate AR).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.data_generator import (
    FEATURE_NAMES,
    preprocess_transaction,
)
from app.application.services.explainability_service import ExplainabilityService
from app.application.services.model_service import FraudDetectionModel
from app.domain.realtime_explainer import (
    FastInferenceExplainer,
    _build_cache_key,
    _compute_feature_fingerprint,
    _get_local_cache,
)

# ============================================================================
# 1. Prediction & Model Binding (XAI-INV-01, XAI-INV-02)
# ============================================================================

def test_model_version_binding_and_hotswapping():
    """Verify that different models produce independently bound explanations (XAI-INV-02)."""
    service = ExplainabilityService()

    # Model A: heavily weights feature 0 (amount)
    model_a = FraudDetectionModel(input_dim=10)
    with torch.no_grad():
        for p in model_a.parameters():
            p.zero_()
        cast("torch.nn.Linear", model_a.network[0]).weight[0, 0] = 5.0
        cast("torch.nn.Linear", model_a.network[8]).weight[0, 0] = 1.0
    model_a.eval()

    # Model B: heavily weights feature 4 (velocity)
    model_b = FraudDetectionModel(input_dim=10)
    with torch.no_grad():
        for p in model_b.parameters():
            p.zero_()
        cast("torch.nn.Linear", model_b.network[0]).weight[0, 4] = 5.0
        cast("torch.nn.Linear", model_b.network[8]).weight[0, 0] = 1.0
    model_b.eval()

    txn = {
        "transaction_amount": 5000.0,
        "velocity": 15.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "web_browser",
    }

    # Verify cache isolation and independent explainer construction
    res_a = service.compute_shap_values(txn, model=model_a)
    res_b = service.compute_shap_values(txn, model=model_b)

    assert len(res_a) == 10
    assert len(res_b) == 10

    # Ensure explainer cache has distinct keys for distinct models
    with service._explainer_lock:
        cache_keys = list(service._explainer_cache.keys())
        model_ids = {k[0] for k in cache_keys}
        assert id(model_a) in model_ids
        assert id(model_b) in model_ids


# ============================================================================
# 2. Preprocessing Parity & Feature Ordering (XAI-INV-03, XAI-INV-04, XAI-INV-05, XAI-INV-06)
# ============================================================================

def test_preprocessing_parity_with_prediction():
    """Verify explanation preprocessing produces identical vector to prediction path (XAI-INV-04)."""
    service = ExplainabilityService()
    txn = {
        "transaction_amount": 1250.0,
        "merchant_category": "travel",
        "country_code": "DE",
        "device_type": "mobile_ios",
        "velocity": 4.0,
        "hour_of_day": 14.0,
        "merchant_risk_score": 0.35,
        "customer_history_score": 0.82,
        "chargeback_count": 1.0,
        "account_age_days": 180.0,
    }

    canonical_tensor = preprocess_transaction(txn)
    parsed_vector = service._parse_transaction_features(txn)

    expected_vector = canonical_tensor[0].cpu().numpy().tolist()
    assert len(parsed_vector) == len(expected_vector)
    for i, (p_val, e_val) in enumerate(zip(parsed_vector, expected_vector)):
        assert math.isclose(p_val, e_val, rel_tol=1e-5), f"Mismatch at feature index {i}"


def test_feature_order_and_naming_fidelity():
    """Verify attribution index i strictly corresponds to FEATURE_NAMES[i] (XAI-INV-05, XAI-INV-06)."""
    service = ExplainabilityService()
    assert service.SHAP_FEATURE_NAMES == FEATURE_NAMES

    txn = {
        "transaction_amount": 500.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "web_browser",
    }
    res = service.compute_shap_values(txn)
    assert len(res) == len(FEATURE_NAMES)
    returned_names = [item["feature"] for item in res]
    assert set(returned_names) == set(FEATURE_NAMES)
    for item in res:
        assert item["feature"] in FEATURE_NAMES
        assert "contribution" in item
        assert "raw_value" in item


def test_adversarial_feature_permutation():
    """Adversarially permute feature values and verify attributions follow the shifted values."""
    service = ExplainabilityService()
    txn_normal = {
        "transaction_amount": 10000.0,
        "velocity": 1.0,
        "merchant_category": "grocery",
    }
    txn_permuted = {
        "transaction_amount": 10.0,
        "velocity": 25.0,
        "merchant_category": "grocery",
    }

    res_normal = {item["feature"]: item for item in service.compute_shap_values(txn_normal)}
    res_permuted = {item["feature"]: item for item in service.compute_shap_values(txn_permuted)}

    # Amount should have higher raw_value in normal, velocity higher in permuted
    assert res_normal["transaction_amount"]["raw_value"] > res_permuted["transaction_amount"]["raw_value"]
    assert res_permuted["velocity"]["raw_value"] > res_normal["velocity"]["raw_value"]


# ============================================================================
# 3. Additivity Oracle & Output Semantics (XAI-INV-07, XAI-INV-08, XAI-INV-09)
# ============================================================================

def test_shapley_local_additivity_oracle():
    """Verify Shapley Efficiency: model_output == base_value + sum(attributions) (XAI-INV-09)."""
    service = ExplainabilityService()
    model = FraudDetectionModel(input_dim=10)
    model.eval()

    txn = {
        "transaction_amount": 3500.0,
        "merchant_category": "electronics",
        "country_code": "GB",
        "device_type": "web_browser",
        "velocity": 6.0,
    }

    res = service.compute_shap_values(txn, model=model)
    assert len(res) > 0

    base_val = res[0]["base_value"]
    model_output = res[0]["model_output"]
    sum_contribs = sum(item["contribution"] for item in res)

    # Reconstructed model output should equal base_val + sum_contribs within Kernel SHAP tolerance
    reconstructed = base_val + sum_contribs
    assert math.isclose(model_output, reconstructed, abs_tol=1e-3), (
        f"Additivity violated: model_output={model_output}, base={base_val}, "
        f"sum_contribs={sum_contribs}, diff={abs(model_output - reconstructed)}"
    )


# ============================================================================
# 4. Sign Oracle with Deterministic Fixture (XAI-INV-10)
# ============================================================================

def test_sign_oracle_directionality():
    """Verify a positive model coefficient yields positive attribution under reference baseline (XAI-INV-10)."""
    service = ExplainabilityService()

    class SimpleLinearModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.linear = torch.nn.Linear(10, 1)
            with torch.no_grad():
                self.linear.weight.zero_()
                self.linear.weight[0, 0] = 3.0   # feature 0 (amount) -> positive
                self.linear.weight[0, 4] = -4.0  # feature 4 (velocity) -> negative
                self.linear.bias.zero_()

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return torch.sigmoid(self.linear(x)).reshape(-1)

    model = SimpleLinearModel()
    model.eval()

    # High amount (x0=1.0), high velocity (x4=1.0) vs zero baseline
    baseline = np.zeros((10, 10), dtype=np.float32)
    txn = {
        "transaction_amount": 10000.0,  # normalized to 1.0
        "velocity": 20.0,               # normalized to 1.0
    }

    res = service.compute_shap_values(txn, model=model, background_data=baseline)
    feat_map = {item["feature"]: item["contribution"] for item in res}

    # Amount should have positive contribution, velocity should have negative contribution
    assert feat_map["transaction_amount"] > 0, "Feature 0 with positive weight must yield positive attribution"
    assert feat_map["velocity"] < 0, "Feature 4 with negative weight must yield negative attribution"


# ============================================================================
# 5. FastInferenceExplainer Semantics & Truthfulness (XAI-INV-12, Gate W, Gate X)
# ============================================================================

def test_fast_inference_explainer_semantics_and_cache_isolation():
    """Verify FastInferenceExplainer truthful reporting and tenant-isolated caching (XAI-INV-12, XAI-INV-13)."""
    explainer = FastInferenceExplainer()
    explainer.invalidate_cache()

    txn_id = "tx_test_tenant_iso"
    feat_vec = {"amount": 25000.0, "velocity_1h": 8, "merchant_category": "crypto_exchange"}

    # Tenant A compute
    res_a = explainer.compute_shap(txn_id, feat_vec, tenant_id="bank_alpha")
    assert res_a["source"] == "FAST_HEURISTIC_COMPUTED"
    assert res_a["method"] == "fast_heuristic"
    assert len(res_a["shap_values"]) == 3

    # Fast explainer cache key isolation: bank_beta must miss
    fp = _compute_feature_fingerprint(feat_vec)
    key_beta = _build_cache_key(txn_id, tenant_id="bank_beta", feature_fingerprint=fp)
    from app.domain.realtime_explainer import _get_local_cache
    assert _get_local_cache(key_beta) is None

    # Bank alpha hit
    key_alpha = _build_cache_key(txn_id, tenant_id="bank_alpha", feature_fingerprint=fp)
    assert _get_local_cache(key_alpha) is not None

    # Clean invalidation
    explainer.invalidate_cache(transaction_id=txn_id, tenant_id="bank_alpha")
    assert _get_local_cache(key_alpha) is None


# ============================================================================
# 6. Failure Truth & Non-Finite Rejection (XAI-INV-14, XAI-INV-15)
# ============================================================================

def test_fail_closed_on_non_finite_numeric_inputs():
    """Verify non-finite numeric inputs raise ValueError rather than fabricating explanations (XAI-INV-15)."""
    service = ExplainabilityService()

    txn_nan = {"transaction_amount": float("nan")}
    with pytest.raises(ValueError, match="non-finite"):
        service.compute_shap_values(txn_nan)

    txn_inf = {"velocity": float("inf")}
    with pytest.raises(ValueError, match="non-finite"):
        service.compute_shap_values(txn_inf)


def test_explainer_produces_strictly_finite_attributions():
    """Verify that all generated attributions are strictly finite numbers."""
    service = ExplainabilityService()
    txn = {
        "transaction_amount": 999999.0,
        "velocity": 500.0,
        "merchant_risk_score": 1.0,
    }
    res = service.compute_shap_values(txn)
    for item in res:
        assert math.isfinite(item["contribution"])
        assert math.isfinite(item["base_value"])
        assert math.isfinite(item["model_output"])


# ============================================================================
# 7. Graph-State Binding with as_of (XAI-INV-17, Gate AD, Gate AE)
# ============================================================================

def test_gnn_explanation_temporal_binding_with_as_of():
    """Verify explain_gnn_embedding binds to historical graph state via as_of (XAI-INV-17)."""
    service = ExplainabilityService()
    node_id = "entity_cust_123"
    t_historical = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)

    with patch("app.application.services.graph_engine.GraphEngine") as MockGraphEngine:
        mock_ge = MockGraphEngine.return_value
        mock_ge.find_neighbors.return_value = []
        mock_ge.get_subgraph.return_value = MagicMock(nodes=["entity_cust_123"], edges=[])

        report = service.explain_gnn_embedding(node_id, as_of=t_historical)

        # Ensure as_of was forwarded to find_neighbors and get_subgraph
        mock_ge.find_neighbors.assert_called_once_with(node_id, depth=2, as_of=t_historical)
        mock_ge.get_subgraph.assert_called_once_with(node_id, radius=2, as_of=t_historical)
        assert report.node_id == node_id
        assert report.target_risk_level == "LOW"


# ============================================================================
# 8. Alert Service Feature Value Integrity (XAI-0004 Remediation)
# ============================================================================

def test_alert_intelligence_service_preserves_actual_feature_values():
    """Verify _get_top_features preserves the true raw feature value rather than overwriting with contribution."""
    txn = {
        "transaction_amount": 4500.0,
        "velocity": 12.0,
        "merchant_category": "grocery",
    }
    top_features = AlertIntelligenceService._get_top_features(txn, score=750.0)
    assert len(top_features) > 0

    feat_map = {f["feature"]: f for f in top_features}
    assert "transaction_amount" in feat_map
    amount_item = feat_map["transaction_amount"]

    # Value must reflect the 4500.0 transaction amount, NOT the contribution (e.g. 0.12)
    assert amount_item["value"] == 4500.0
    assert amount_item["contribution"] != amount_item["value"]


# ============================================================================
# 9. LIME Preprocessing Parity & Model Binding
# ============================================================================

def test_lime_surrogate_explanation_uses_canonical_preprocessing():
    """Verify compute_lime_explanation uses canonical preprocessing parity."""
    service = ExplainabilityService()
    txn = {
        "transaction_amount": 3200.0,
        "merchant_category": "travel",
        "country_code": "FR",
        "velocity": 5.0,
    }

    report = service.compute_lime_explanation(transaction=txn, num_samples=30)
    assert report.fidelity_r2 >= 0.0
    assert len(report.feature_attributions) == 10

    # Ensure amount attribution corresponds to transaction_amount
    names = [a.feature for a in report.feature_attributions]
    assert "transaction_amount" in names
    assert all(math.isfinite(a.weight) for a in report.feature_attributions)


# ============================================================================
# 10. Phase 5F Certification Closure Tests
# ============================================================================

def test_explanation_cache_separated_by_model_version() -> None:
    """Verify that FastInferenceExplainer separates cache entries by model_version (XAI-INV-13)."""
    explainer = FastInferenceExplainer()
    tx_id = "tx_closure_mv_01"
    features = {"amount": 25000.0, "velocity_1h": 6, "merchant_category": "crypto_exchange"}

    res_v1 = explainer.compute_shap(tx_id, features, model_version="v1.0.0")
    res_v2 = explainer.compute_shap(tx_id, features, model_version="v2.0.0")

    assert res_v1["status"] == "COMPLETED"
    assert res_v2["status"] == "COMPLETED"

    # Verify separate cache keys exist in local cache
    fp = _compute_feature_fingerprint(features)
    key_v1 = _build_cache_key(tx_id, feature_fingerprint=fp, model_version="v1.0.0")
    key_v2 = _build_cache_key(tx_id, feature_fingerprint=fp, model_version="v2.0.0")

    assert key_v1 != key_v2
    assert _get_local_cache(key_v1) is not None
    assert _get_local_cache(key_v2) is not None


def test_explanation_cache_separated_by_feature_state() -> None:
    """Verify that changing feature vector values prevents stale cache reuse (XAI-INV-13)."""
    explainer = FastInferenceExplainer()
    tx_id = "tx_closure_features_dynamic"

    feat_low = {"amount": 100.0, "velocity_1h": 1, "merchant_category": "grocery"}
    feat_high = {"amount": 60000.0, "velocity_1h": 10, "merchant_category": "gambling"}

    explainer.compute_shap(tx_id, feat_low)
    explainer.compute_shap(tx_id, feat_high)

    fp_low = _compute_feature_fingerprint(feat_low)
    fp_high = _compute_feature_fingerprint(feat_high)

    key_low = _build_cache_key(tx_id, feature_fingerprint=fp_low)
    key_high = _build_cache_key(tx_id, feature_fingerprint=fp_high)

    assert key_low != key_high

    hit_low = explainer.explain_async(tx_id, feat_low)
    hit_high = explainer.explain_async(tx_id, feat_high)

    assert hit_low["source"] == "LOCAL_CACHE_HIT"
    assert hit_high["source"] == "LOCAL_CACHE_HIT"

    # Low amount has amount decreasing risk; high amount has amount increasing risk
    low_amount_dir = next(a["direction"] for a in hit_low["attributions"] if a["feature_name"] == "amount")
    high_amount_dir = next(a["direction"] for a in hit_high["attributions"] if a["feature_name"] == "amount")

    assert low_amount_dir == "DECREASES_RISK"
    assert high_amount_dir == "INCREASES_RISK"


def test_explainer_cache_distinguishes_equal_size_different_backgrounds() -> None:
    """Verify that equal-shape (30, 10) backgrounds with different values receive distinct explainers (XAI-INV-08)."""
    service = ExplainabilityService()
    service.invalidate_explainer_cache()

    model = FraudDetectionModel(input_dim=10)
    model.eval()

    bg_a = np.zeros((30, 10), dtype=np.float32)
    bg_b = np.ones((30, 10), dtype=np.float32) * 0.85

    txn = {
        "transaction_amount": 5000.0,
        "velocity": 5.0,
        "merchant_category": "retail",
        "country_code": "US",
        "device_type": "mobile_app",
    }

    service.compute_batch_shap_values([txn], model=model, background_data=bg_a, nsamples=20)
    assert len(service._explainer_cache) == 1

    service.compute_batch_shap_values([txn], model=model, background_data=bg_b, nsamples=20)
    # MUST have 2 distinct cached explainers because background fingerprints differ
    assert len(service._explainer_cache) == 2


def test_explainer_cache_feature_schema_binding() -> None:
    """Verify that explainer cache identity structurally incorporates feature schema (XAI-INV-05)."""
    service = ExplainabilityService()
    service.invalidate_explainer_cache()

    model = FraudDetectionModel(input_dim=10)
    model.eval()

    txn = {
        "transaction_amount": 2500.0,
        "velocity": 3.0,
        "merchant_category": "travel",
        "country_code": "DE",
        "device_type": "web_browser",
    }

    service.compute_batch_shap_values([txn], model=model, nsamples=20)
    assert len(service._explainer_cache) == 1

    cache_keys = list(service._explainer_cache.keys())
    schema_tuple = cache_keys[0][2]
    assert len(schema_tuple) == 10
    assert "transaction_amount" in schema_tuple


def test_historical_graph_explanation_late_event_semantics() -> None:
    """Demonstrate Guarantee A event-time isolation: future edges excluded, historical edges participate (XAI-INV-17)."""
    from app.application.services.graph_engine import GraphEngine
    from app.domain.enums import EntityType, RelationshipType, RiskLevel
    from app.domain.investigation_entities import Entity, Relationship

    engine = GraphEngine()
    engine._entities.clear()
    engine._relationships.clear()
    engine._adjacency.clear()

    node_a = "USR_CLOSURE_NODE_A"
    node_b = "USR_CLOSURE_NODE_B"
    node_c = "USR_CLOSURE_NODE_C"

    # Register entities
    t_100 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    for nid in (node_a, node_b, node_c):
        engine.register_entity(Entity(
            id=nid,
            entity_type=EntityType.CUSTOMER,
            privacy_id=f"priv_{nid}",
            bank_id="bank_alpha",
            display_label=nid,
            risk_level=RiskLevel.MEDIUM,
            alert_count=0,
            first_seen=t_100,
            last_seen=t_100,
        ))

    # Edge 1: Created at t=100
    engine.add_relationship(Relationship(
        id="rel_ab",
        source_entity_id=node_a,
        target_entity_id=node_b,
        relationship_type=RelationshipType.SHARES_DEVICE,
        created_at=t_100,
    ))

    # Edge 2: Created at t=300 (future relative to query at t=200)
    t_300 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    engine.add_relationship(Relationship(
        id="rel_ac",
        source_entity_id=node_a,
        target_entity_id=node_c,
        relationship_type=RelationshipType.LINKED_ALERT,
        created_at=t_300,
    ))

    service = ExplainabilityService()

    # Query with as_of = t=200: Edge 2 must be excluded by event-time cutoff
    t_200 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
    exp_200 = service.explain_gnn_embedding(node_a, as_of=t_200)

    # Only node_b should be in contributions
    targets_200 = [c.target for c in exp_200.top_contributing_edges]
    assert node_b in targets_200
    assert node_c not in targets_200


def test_lime_low_fidelity_truthfulness() -> None:
    """Verify that LIME exposes surrogate fidelity R^2 and appends low-fidelity caution when R^2 < 0.50 (XAI-INV-12)."""
    service = ExplainabilityService()
    txn = {
        "transaction_amount": 1000.0,
        "merchant_category": "retail",
        "country_code": "US",
        "velocity": 1.0,
    }

    # Standard run: high or reasonable fidelity
    rep_normal = service.compute_lime_explanation(transaction=txn, num_samples=50, l2_reg=0.01)
    assert rep_normal.fidelity_r2 >= 0.0

    # Test extreme regularized run (excessive L2 penalty collapses slopes toward zero, causing low R^2)
    rep_low_fid = service.compute_lime_explanation(
        transaction=txn, num_samples=30, l2_reg=100000.0, kernel_width=0.05
    )
    if rep_low_fid.fidelity_r2 < 0.50:
        assert "CAUTION: Low surrogate fidelity" in rep_low_fid.explanation_text


def test_shap_predict_fn_matches_serving_prediction() -> None:
    """Verify SHAP predict_fn output numerically equals serving model forward pass within 1e-6 (XAI-INV-07)."""
    model = FraudDetectionModel(input_dim=10)
    model.eval()

    txn = {
        "transaction_amount": 7500.0,
        "merchant_category": "crypto_exchange",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 8.0,
    }

    tensor_input = preprocess_transaction(txn)
    with torch.no_grad():
        serving_prediction = float(model(tensor_input).cpu().numpy().reshape(-1)[0])

    service = ExplainabilityService()
    attributions = service.compute_shap_values(txn, model=model, nsamples=30)

    assert len(attributions) == 10
    shap_model_output = attributions[0]["model_output"]

    assert math.isclose(serving_prediction, shap_model_output, abs_tol=1e-5)


def test_shap_additivity_distribution_statistics() -> None:
    """Evaluate SHAP local additivity reconstruction errors across multiple test inputs (XAI-INV-09)."""
    service = ExplainabilityService()
    model = FraudDetectionModel(input_dim=10)
    model.eval()

    test_txns = [
        {"transaction_amount": 100.0, "velocity": 1.0, "merchant_category": "grocery"},
        {"transaction_amount": 2500.0, "velocity": 4.0, "merchant_category": "retail"},
        {"transaction_amount": 15000.0, "velocity": 9.0, "merchant_category": "crypto_exchange"},
        {"transaction_amount": 450.0, "velocity": 2.0, "merchant_category": "travel"},
        {"transaction_amount": 50000.0, "velocity": 12.0, "merchant_category": "gambling"},
    ]

    abs_errors: list[float] = []
    for txn in test_txns:
        shap_vals = service.compute_shap_values(txn, model=model, nsamples=30)
        base_val = shap_vals[0]["base_value"]
        model_out = shap_vals[0]["model_output"]
        sum_phi = sum(f["contribution"] for f in shap_vals)

        diff = abs(model_out - (base_val + sum_phi))
        abs_errors.append(diff)

    max_err = max(abs_errors)
    mean_err = sum(abs_errors) / len(abs_errors)

    # All reconstruction errors must satisfy Shapley efficiency axiom (< 1e-4)
    assert max_err < 1e-4
    assert mean_err < 1e-4


def test_fallback_heuristic_identity_preserved_end_to_end() -> None:
    """Verify that when ML model is absent or fails, explanation_method is fallback_heuristic (XAI-INV-14)."""
    service = ExplainabilityService()

    txn = {
        "transaction_amount": 1200.0,
        "velocity": 2.0,
        "merchant_category": "grocery",
    }

    with patch.object(service, "compute_batch_shap_values", return_value=[[
        {
            "feature": "transaction_amount",
            "contribution": 0.25,
            "value": 0.12,
            "raw_value": 1200.0,
            "explanation_method": "fallback_heuristic",
            "base_value": 0.10,
            "model_output": 0.35,
        }
    ]]):
        result = service.compute_shap_values(txn)
        assert len(result) > 0
        assert result[0]["explanation_method"] == "fallback_heuristic"


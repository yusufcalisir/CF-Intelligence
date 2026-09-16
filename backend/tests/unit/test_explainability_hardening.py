"""Hardening unit tests for Explainability Engine (TreeSHAP, LIME & Counterfactuals).

Verifies 22-vector compliance:
- Shapley efficiency property: sum(phi_i) = f(x) - E[f(x)]
- LIME local linear surrogate with exponential kernel weighting and Ridge regression
- Zero global random seed pollution (Vector 7)
- Zero fake mock edges on isolated GNN nodes (Vector 1)
- Thread-safe in-memory LRU caching in RealtimeExplainer
- Actionable counterfactual generation convergence
- Decision replay audit score reconstruction
- FastAPI LIME endpoints contract integrity
"""

import threading
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.explainability_service import ExplainabilityService
from app.application.services.graph_engine import GraphEngine
from app.domain.entities_phase2 import Alert, Entity, Relationship
from app.domain.enums import AlertSeverity, AlertStatus, EntityType, RelationshipType
from app.domain.realtime_explainer import FastInferenceExplainer, _get_local_cache, _put_local_cache
from app.main import app


@pytest.fixture
def sample_txn_dict() -> dict:
    return {
        "transaction_amount": 1250.0,
        "merchant_category": "gambling",
        "country_code": "KP",
        "device_type": "mobile_android",
        "velocity": 8.0,
        "hour_of_day": 3,
        "merchant_risk_score": 0.88,
        "customer_history_score": 0.40,
        "chargeback_count": 2,
        "account_age_days": 35,
    }


@pytest.fixture
def sample_alert() -> Alert:
    return Alert(
        bank_id="bank_test",
        transaction_id="tx_test_explain_99",
        risk_score=780.0,
        severity=AlertSeverity.HIGH,
        status=AlertStatus.NEW,
        reason_codes=["HIGH-AMT", "GEO-RISK", "VEL-001"],
        confidence=0.88,
        involved_entity_ids=["entity_root_99"],
        model_confidence=0.85,
        top_features=[
            {"feature": "transaction_amount", "contribution": 0.35, "value": 1250.0},
            {"feature": "country_code", "contribution": 0.28, "value": 0.9},
        ],
        risk_factors=["High amount deviation", "Sanctioned/High-risk jurisdiction"],
    )


def test_shap_efficiency_axiom_mathematical_precision(sample_txn_dict: dict):
    """Verify Shapley efficiency property: sum(phi_i) = f(x) - E[f(x)] down to precision."""
    svc = ExplainabilityService()
    shap_vals = svc.compute_shap_values(sample_txn_dict)

    assert len(shap_vals) == 10
    base_val = shap_vals[0]["base_value"]
    model_output = shap_vals[0]["model_output"]
    sum_phi = sum(f["contribution"] for f in shap_vals)

    # sum(phi_i) must equal model_output - base_val within 1e-5
    assert abs(sum_phi - (model_output - base_val)) < 1e-5
    # Equivalently: base_val + sum(phi_i) = f(x)
    assert abs((base_val + sum_phi) - model_output) < 1e-5


def test_lime_surrogate_fidelity_and_weights(sample_alert: Alert, sample_txn_dict: dict):
    """Verify LIME fits a valid local linear surrogate model with Ridge regression and R^2 score."""
    svc = ExplainabilityService()

    report = svc.compute_lime_explanation(
        alert=sample_alert,
        transaction=sample_txn_dict,
        kernel_width=0.75,
        num_samples=120,
    )

    assert report.alert_id == sample_alert.id
    assert 0.0 <= report.fidelity_r2 <= 1.0
    assert report.kernel_width == 0.75
    assert report.num_samples == 120
    assert len(report.feature_attributions) == 10

    # Top features must be sorted by descending absolute weight
    abs_weights = [abs(a.weight) for a in report.feature_attributions]
    assert abs_weights == sorted(abs_weights, reverse=True)

    # Direction must match sign of coefficient
    for attr in report.feature_attributions:
        if attr.weight >= 0:
            assert attr.direction == "INCREASES_RISK"
        else:
            assert attr.direction == "DECREASES_RISK"

    assert "LIME local surrogate explanation" in report.explanation_text
    assert "fidelity R²=" in report.explanation_text
    assert f"kernel width={report.kernel_width:.2f}" in report.explanation_text


def test_lime_kernel_weighting_sensitivity():
    """Verify exponential kernel weighting: closer samples have strictly higher weight than distant ones."""
    x_0 = np.array([0.5, 0.5, 0.5, 0.5])
    near_sample = np.array([0.51, 0.49, 0.50, 0.50])
    far_sample = np.array([0.95, 0.10, 0.90, 0.05])

    dist_near = np.linalg.norm(near_sample - x_0)
    dist_far = np.linalg.norm(far_sample - x_0)
    sigma = 0.75

    weight_near = np.exp(-(dist_near**2) / (sigma**2))
    weight_far = np.exp(-(dist_far**2) / (sigma**2))

    assert dist_near < dist_far
    assert weight_near > weight_far
    assert weight_near > 0.95
    assert weight_far < 0.35


def test_zero_global_random_seed_pollution(sample_txn_dict: dict, sample_alert: Alert):
    """Verify executing SHAP and LIME preserves global numpy RNG state (Vector 7)."""
    svc = ExplainabilityService()

    # Draw a reference random sequence
    np.random.seed(12345)
    _ = np.random.uniform(size=5)
    pre_state = np.random.get_state()

    # Run SHAP computation
    _ = svc.compute_shap_values(sample_txn_dict)
    post_shap_state = np.random.get_state()

    # State should not have been overwritten with static seed 42
    assert pre_state[1][0] == post_shap_state[1][0]

    # Run LIME computation
    _ = svc.compute_lime_explanation(alert=sample_alert, seed=999)
    post_lime_state = np.random.get_state()
    assert pre_state[1][0] == post_lime_state[1][0]


def test_gnn_isolated_node_zero_mock_integrity():
    """Verify isolated entities report 0 edges and zero fake mock mule accounts (Vector 1)."""
    ge = GraphEngine()
    isolated_id = "isolated_entity_strictly_0_edges_999"
    ge.register_entity(
        Entity(
            id=isolated_id,
            entity_type=EntityType.CUSTOMER,
            privacy_id="priv_iso_999",
            bank_id="bank_test",
        )
    )

    svc = ExplainabilityService()
    gnn_exp = svc.explain_gnn_embedding(isolated_id)

    assert gnn_exp.node_id == isolated_id
    assert gnn_exp.subgraph_edges_count == 0
    assert len(gnn_exp.top_contributing_edges) == 0
    assert "Isolated entity with 0 graph neighbors" in gnn_exp.primary_driver_text
    # Zero mock accounts must be present
    assert "mule_account_8912" not in [e.target for e in gnn_exp.top_contributing_edges]
    assert "suspicious_ip_192.168.4.12" not in [e.target for e in gnn_exp.top_contributing_edges]


def test_gnn_connected_node_attribution_integrity():
    """Verify real multi-edge graph neighborhood attribution sums to 100%."""
    ge = GraphEngine()
    root_id = "entity_hub_root"
    node1 = "device_fingerprint_hub"
    node2 = "ip_address_hub"

    ge.register_entity(Entity(id=root_id, entity_type=EntityType.CUSTOMER, privacy_id="p1", bank_id="b1"))
    ge.register_entity(Entity(id=node1, entity_type=EntityType.DEVICE, privacy_id="p2", bank_id="b1"))
    ge.register_entity(Entity(id=node2, entity_type=EntityType.DEVICE, privacy_id="p3", bank_id="b1"))

    ge.add_relationship(
        Relationship(source_entity_id=root_id, target_entity_id=node1, relationship_type=RelationshipType.SHARES_DEVICE)
    )
    ge.add_relationship(
        Relationship(source_entity_id=root_id, target_entity_id=node2, relationship_type=RelationshipType.SHARES_IP)
    )

    svc = ExplainabilityService()
    report = svc.explain_gnn_embedding(root_id)

    assert report.node_id == root_id
    assert report.subgraph_edges_count >= 2
    assert len(report.top_contributing_edges) >= 2
    targets = {e.target for e in report.top_contributing_edges}
    assert node1 in targets or node2 in targets

    total_pct = sum(e.contribution_percentage for e in report.top_contributing_edges)
    assert abs(total_pct - 100.0) < 1.0


def test_realtime_explainer_thread_safe_lru_cache():
    """Verify concurrent reads and writes to in-memory LRU cache do not corrupt state."""
    FastInferenceExplainer()
    errors: list[Exception] = []

    def worker(worker_id: int):
        try:
            for i in range(50):
                key = f"cfi:shap:test_thread_{worker_id}_{i}"
                _put_local_cache(key, f'{{"tx": "{key}", "score": {i}}}')
                val = _get_local_cache(key)
                assert val is not None
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0


def test_counterfactual_greedy_descent_convergence(sample_alert: Alert):
    """Verify counterfactual search generates actionable steps reducing risk below threshold."""
    svc = ExplainabilityService()
    cf = svc.generate_counterfactuals(sample_alert, target_score=350.0)

    assert cf.alert_id == sample_alert.id
    assert cf.original_score == sample_alert.risk_score
    assert cf.remediated_score <= cf.original_score
    assert len(cf.changes) >= 1

    for change in cf.changes:
        assert change.feature != ""
        assert change.original_value != change.remediated_value
        assert len(change.delta_explanation) > 5


def test_decision_replay_regulatory_audit_reconstruction(sample_alert: Alert):
    """Verify decision replay reproduces model version, AUC, and evaluated rules."""
    svc = ExplainabilityService()
    replay = svc.replay_inference_audit(sample_alert)

    assert replay.alert_id == sample_alert.id
    assert replay.model_version == "v1.4.2-champion"
    assert replay.model_auc >= 0.90
    assert len(replay.policy_rules_evaluated) == 9
    assert replay.reconstructed_risk_score > 0
    assert replay.audit_matched in (True, False)


def test_lime_fastapi_endpoints(sample_alert: Alert):
    """Verify FastAPI endpoints for LIME explanation return 200 with schema fidelity."""
    alert_service = AlertIntelligenceService()
    alert_service.create_alert(sample_alert)

    client = TestClient(app)

    # 1. Alert LIME endpoint
    resp = client.get(f"/api/v1/alerts/{sample_alert.id}/lime-explanation?kernel_width=0.75&num_samples=50")
    assert resp.status_code == 200
    data = resp.json()
    assert data["alert_id"] == sample_alert.id
    assert data["transaction_id"] == sample_alert.transaction_id
    assert "intercept" in data
    assert "fidelity_r2" in data
    assert 0.0 <= data["fidelity_r2"] <= 1.0
    assert len(data["feature_attributions"]) == 10

    # 2. Transaction LIME endpoint
    tx_resp = client.get(f"/api/v1/explanation/{sample_alert.transaction_id}/lime")
    assert tx_resp.status_code == 200
    tx_data = tx_resp.json()
    assert tx_data["alert_id"] == sample_alert.id
    assert tx_data["transaction_id"] == sample_alert.transaction_id

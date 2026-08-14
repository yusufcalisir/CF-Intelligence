"""Comprehensive Provider API Endpoint Contract Tests.

Validates that all backend API endpoints adhere strictly to their response schemas,
HTTP status code contracts, parameter validation, and data invariants.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    """FastAPI TestClient fixture."""
    return TestClient(app)


# ── 1. System Health & Root Contract ──────────────────────────────────────────


def test_system_health_and_root_contract(client: TestClient):
    """Validate /health, /health/ready, and root / contracts."""
    # Liveness probe
    res_health = client.get("/health")
    assert res_health.status_code == 200
    data_health = res_health.json()
    assert "status" in data_health
    assert data_health["status"] in ("ok", "healthy")

    # Readiness probe
    res_ready = client.get("/health/ready")
    assert res_ready.status_code in (200, 503)

    # Root endpoint
    res_root = client.get("/")
    assert res_root.status_code == 200
    data_root = res_root.json()
    assert "service" in data_root
    assert "version" in data_root
    assert "docs" in data_root


# ── 2. Bank Nodes & Non-IID Distribution Contract ────────────────────────────


def test_banks_and_distributions_contract(client: TestClient):
    """Validate /api/v1/banks and /api/v1/banks/distributions contracts."""
    # List banks
    res = client.get("/api/v1/banks")
    assert res.status_code == 200
    banks = res.json()
    assert isinstance(banks, list)
    assert len(banks) >= 3

    for bank in banks:
        assert "id" in bank
        assert "name" in bank
        assert "tier" in bank
        assert "default_fraud_ratio" in bank
        assert "default_transactions" in bank
        assert "fraud_pattern" in bank
        assert "characteristics" in bank
        assert isinstance(bank["characteristics"], list)

    # Bank distributions & data drift
    res_dist = client.get("/api/v1/banks/distributions")
    assert res_dist.status_code == 200
    data_dist = res_dist.json()
    assert "banks" in data_dist
    assert "divergence_summary" in data_dist
    assert isinstance(data_dist["banks"], dict)
    assert "overall_non_iid_score" in data_dist["divergence_summary"]


# ── 3. Federated Learning Simulations & AI Act Compliance Contract ────────────


def test_simulations_lifecycle_contract(client: TestClient):
    """Validate /api/v1/simulations, /ai-act-report, and training rounds contracts."""
    # List simulations
    res_list = client.get("/api/v1/simulations")
    assert res_list.status_code == 200
    assert isinstance(res_list.json(), list)

    # Create simulation
    config_payload = {
        "num_rounds": 2,
        "local_epochs": 1,
        "learning_rate": 0.001,
        "batch_size": 32,
        "min_clients_per_round": 2,
        "enable_latency_simulation": False,
        "enable_dropout_simulation": False,
        "enable_reconnect_simulation": True,
        "privacy_mechanism": "none",
        "dp_epsilon": 1.0,
        "dp_delta": 1e-5,
        "dp_max_grad_norm": 1.0,
        "bank_a_transactions": 1000,
        "bank_b_transactions": 1000,
        "bank_c_transactions": 1000,
    }
    res_create = client.post("/api/v1/simulations", json=config_payload)
    assert res_create.status_code == 202
    create_data = res_create.json()
    assert "id" in create_data
    assert "status" in create_data
    assert "message" in create_data
    sim_id = create_data["id"]

    # Get simulation details
    res_get = client.get(f"/api/v1/simulations/{sim_id}")
    assert res_get.status_code == 200
    sim_detail = res_get.json()
    assert sim_detail["id"] == sim_id
    assert "status" in sim_detail
    assert "config" in sim_detail
    assert "banks" in sim_detail
    assert "rounds" in sim_detail
    assert isinstance(sim_detail["banks"], list)
    assert isinstance(sim_detail["rounds"], list)

    # AI Act Compliance Report
    res_ai_act = client.get(f"/api/v1/simulations/{sim_id}/ai-act-report")
    assert res_ai_act.status_code in (200, 404)
    if res_ai_act.status_code == 200:
        report = res_ai_act.json()
        assert isinstance(report, dict)

    # Training rounds contract
    res_rounds = client.get(f"/api/v1/training/{sim_id}/rounds")
    assert res_rounds.status_code == 200
    assert isinstance(res_rounds.json(), list)


# ── 4. AML Alerts, Explainability & Decision Replay Contract ──────────────────


def test_alerts_and_explainability_contract(client: TestClient):
    """Validate alerts feeds, explainability, counterfactuals, and replay contracts."""
    # List alerts
    res_alerts = client.get("/api/v1/alerts")
    assert res_alerts.status_code == 200
    alerts = res_alerts.json()
    assert isinstance(alerts, list)

    if alerts:
        alert = alerts[0]
        alert_id = alert["id"]
        assert "bank_id" in alert
        assert "severity" in alert
        assert "risk_score" in alert

        # Get single alert
        res_single = client.get(f"/api/v1/alerts/{alert_id}")
        assert res_single.status_code == 200
        assert res_single.json()["id"] == alert_id

        # Explainability
        res_explain = client.get(f"/api/v1/alerts/{alert_id}/explain")
        assert res_explain.status_code in (200, 404)
        if res_explain.status_code == 200:
            exp_data = res_explain.json()
            assert "top_features" in exp_data

        # Counterfactuals
        res_cf = client.get(f"/api/v1/alerts/{alert_id}/counterfactuals")
        assert res_cf.status_code in (200, 404)
        if res_cf.status_code == 200:
            cf_data = res_cf.json()
            assert "changes" in cf_data
            assert isinstance(cf_data["changes"], list)

        # Decision Replay
        res_replay = client.get(f"/api/v1/alerts/{alert_id}/decision-replay")
        assert res_replay.status_code in (200, 404)
        if res_replay.status_code == 200:
            replay_data = res_replay.json()
            assert "replay_verified" in replay_data

        # GNN Explanation
        res_gnn = client.get(f"/api/v1/alerts/{alert_id}/gnn-explanation")
        assert res_gnn.status_code in (200, 404)
        if res_gnn.status_code == 200:
            gnn_data = res_gnn.json()
            assert "node_attributions" in gnn_data

    # Shared Intelligence & Stats
    res_intel = client.get("/api/v1/intelligence")
    assert res_intel.status_code == 200
    assert isinstance(res_intel.json(), list)

    res_stats = client.get("/api/v1/intelligence/stats")
    assert res_stats.status_code == 200
    intel_stats = res_stats.json()
    assert "total_items" in intel_stats


# ── 5. Case Management, Cryptographic Evidence & SAR Filing Contract ──────────


def test_cases_and_evidence_contract(client: TestClient):
    """Validate case lifecycle, immutable evidence hashing, and audit logs."""
    # List cases
    res_cases = client.get("/api/v1/cases")
    assert res_cases.status_code == 200
    assert isinstance(res_cases.json(), list)

    # Create new case
    case_payload = {
        "title": "API Contract AML Triage Case #999",
        "priority": "p2_high",
        "alert_ids": ["ALT-001"],
    }
    res_create = client.post("/api/v1/cases", json=case_payload)
    assert res_create.status_code == 200
    case_data = res_create.json()
    assert "id" in case_data
    assert case_data["title"] == case_payload["title"]
    case_id = case_data["id"]

    # Get case details
    res_get_case = client.get(f"/api/v1/cases/{case_id}")
    assert res_get_case.status_code == 200
    assert res_get_case.json()["id"] == case_id

    # Register evidence with SHA-256 integrity
    evidence_payload = {
        "evidence_type": "TRANSACTION_LEDGER",
        "title": "Contract Wire Transfer Proof",
        "file_path": "evidence/contracts/txn_999.json",
        "content": '{"source": "Bank A", "target": "Bank B", "amount": 250000.0}',
        "uploaded_by": "Compliance Lead",
    }
    res_ev = client.post(f"/api/v1/cases/{case_id}/evidence", json=evidence_payload)
    assert res_ev.status_code == 200
    ev_data = res_ev.json()
    assert "id" in ev_data
    assert "content_hash" in ev_data
    assert len(ev_data["content_hash"]) == 64  # Hex SHA-256 length

    # List evidence
    res_list_ev = client.get(f"/api/v1/cases/{case_id}/evidence")
    assert res_list_ev.status_code == 200
    assert len(res_list_ev.json()) >= 1

    # Investigator Audit logs
    res_audit = client.get("/api/v1/cases/audit/logs")
    assert res_audit.status_code == 200
    assert isinstance(res_audit.json(), list)


# ── 6. Identity Entities, Graph Investigation & Fuzzy PSI Contract ────────────


def test_entities_graph_and_psi_contract(client: TestClient):
    """Validate identity entities, graph topology metrics, and PSI cross-bank matching."""
    # List entities
    res_entities = client.get("/api/v1/entities")
    assert res_entities.status_code == 200
    entities = res_entities.json()
    assert isinstance(entities, list)

    # Graph summary statistics
    res_graph_stats = client.get("/api/v1/graph/stats/summary")
    assert res_graph_stats.status_code == 200
    gstats = res_graph_stats.json()
    assert "total_nodes" in gstats
    assert "total_edges" in gstats
    assert "cluster_count" in gstats

    # Cross-Bank Private Set Intersection (PSI)
    psi_payload = {
        "bank_a_id": "bank_a",
        "bank_b_id": "bank_b",
        "entity_type": "customer",
        "enable_fuzzy": False,
    }
    res_psi = client.post("/api/v1/entities/psi", json=psi_payload)
    assert res_psi.status_code == 200
    psi_data = res_psi.json()
    assert "matches" in psi_data
    assert "stats" in psi_data
    assert isinstance(psi_data["matches"], list)

    # Fuzzy Entity Resolution
    fuzzy_payload = {
        "query_name": "Alexander Hamilton",
        "entity_type": "customer",
        "threshold": 0.75,
    }
    res_fuzzy = client.post("/api/v1/entities/fuzzy-resolve", json=fuzzy_payload)
    assert res_fuzzy.status_code == 200
    fuzzy_data = res_fuzzy.json()
    assert "matches" in fuzzy_data
    assert isinstance(fuzzy_data["matches"], list)


# ── 7. Declarative Policy Rules & AST Evaluation Contract ─────────────────────


def test_policy_rules_engine_contract(client: TestClient):
    """Validate policy rules listing, rule creation, and rule AST evaluation test."""
    # List rules
    res_rules = client.get("/api/v1/rules")
    assert res_rules.status_code == 200
    rules = res_rules.json()
    assert isinstance(rules, list)

    # Create rule with unique name
    unique_rule_name = f"Contract Structuring Rule {uuid.uuid4().hex[:8]}"
    rule_payload = {
        "rule_name": unique_rule_name,
        "condition": {
            "and": [
                {"field": "composite_risk_score", "operator": ">=", "value": 800},
                {"field": "velocity", "operator": ">", "value": 5.0},
            ],
        },
        "action": "BLOCK",
        "is_active": True,
    }
    res_create = client.post("/api/v1/rules", json=rule_payload)
    assert res_create.status_code in (200, 201)
    rule_data = res_create.json()
    assert "id" in rule_data
    assert rule_data["rule_name"] == rule_payload["rule_name"]
    rule_id = rule_data["id"]

    # Test rule AST evaluation against payload
    test_eval_payload = {
        "condition": rule_payload["condition"],
        "transaction": {
            "composite_risk_score": 850,
            "velocity": 6.2,
            "transaction_amount": 15000.0,
        },
    }
    res_test = client.post("/api/v1/rules/test", json=test_eval_payload)
    assert res_test.status_code == 200
    test_data = res_test.json()
    assert "matches" in test_data
    assert "message" in test_data
    assert test_data["matches"] is True

    # Delete rule
    res_del = client.delete(f"/api/v1/rules/{rule_id}")
    assert res_del.status_code in (200, 204)


# ── 8. Cryptographic Security Posture & ABAC Evaluation Contract ──────────────


def test_security_posture_and_abac_contract(client: TestClient):
    """Validate mTLS/ABAC security posture, ABAC evaluator, and audit chain."""
    # Security Status
    res_sec = client.get("/api/v1/security/status")
    assert res_sec.status_code == 200
    sec_data = res_sec.json()
    assert "mtls" in sec_data
    assert "abac" in sec_data
    assert "audit_chain" in sec_data

    # ABAC Evaluation
    abac_payload = {
        "actor": "lead_analyst_01",
        "actor_role": "compliance_supervisor",
        "bank_id": "bank_a",
        "action": "VIEW_CROSS_BANK_INTELLIGENCE",
        "resource": "sar_case_record",
        "attributes": {"clearance_level": "LEVEL_3"},
    }
    res_abac = client.post("/api/v1/security/abac/evaluate", json=abac_payload)
    assert res_abac.status_code == 200
    abac_res = res_abac.json()
    assert "allowed" in abac_res
    assert "reason" in abac_res

    # Audit Chain
    res_chain = client.get("/api/v1/security/audit-chain")
    assert res_chain.status_code == 200
    assert isinstance(res_chain.json(), list)

    # Audit Chain Merkle Verification
    res_verify = client.post("/api/v1/security/audit-chain/verify")
    assert res_verify.status_code == 200
    verify_data = res_verify.json()
    assert "is_valid" in verify_data
    assert "total_records" in verify_data


# ── 9. Real-Time Drift Analysis & Calibration Monitoring Contract ─────────────


def test_drift_and_calibration_monitoring_contract(client: TestClient):
    """Validate drift analysis report, calibration ECE metrics, and active alerts."""
    # Drift Analysis
    res_drift = client.get("/api/v1/monitoring/drift/analyze")
    assert res_drift.status_code == 200
    drift_data = res_drift.json()
    assert "overall_status" in drift_data
    assert "feature_drifts" in drift_data
    assert "calibration" in drift_data

    # Calibration report
    res_calib = client.get("/api/v1/monitoring/calibration")
    assert res_calib.status_code == 200
    calib_data = res_calib.json()
    assert "expected_calibration_error" in calib_data
    assert "max_calibration_error" in calib_data
    assert "bins" in calib_data
    assert isinstance(calib_data["bins"], list)

    # Monitoring Alerts
    res_alerts = client.get("/api/v1/monitoring/alerts")
    assert res_alerts.status_code == 200
    assert isinstance(res_alerts.json(), list)


# ── 10. Coordinator Client Negotiation & Privacy Defense Audits Contract ──────


def test_coordinator_and_privacy_defense_contract(client: TestClient):
    """Validate client hardware negotiation and MIA/Inversion audit contracts."""
    # Coordinator Clients
    res_clients = client.get("/api/v1/coordinator/clients")
    assert res_clients.status_code == 200
    assert isinstance(res_clients.json(), list)

    # Hyperparameter Negotiation
    res_neg = client.get(
        "/api/v1/coordinator/negotiate",
        params={"bank_id": "bank_a", "base_batch_size": 32, "base_epochs": 5},
    )
    assert res_neg.status_code == 200
    neg_data = res_neg.json()
    assert "bank_id" in neg_data
    assert "batch_size" in neg_data
    assert "local_epochs" in neg_data

    # Aggregation Methods Catalog
    res_aggr = client.get("/api/v1/privacy-defense/aggregation-methods")
    assert res_aggr.status_code == 200
    methods = res_aggr.json()
    assert isinstance(methods, list)
    assert len(methods) >= 4

    # Privacy Budget Log
    res_budget = client.get("/api/v1/privacy-defense/budget-log")
    assert res_budget.status_code == 200
    assert isinstance(res_budget.json(), list)

    # Membership Inference Attack (MIA) Audit
    mia_payload = {
        "train_losses": [0.45, 0.42, 0.38, 0.35, 0.30],
        "test_losses": [0.48, 0.46, 0.45, 0.44, 0.42],
    }
    res_mia = client.post("/api/v1/privacy-defense/audit/mia", json=mia_payload)
    assert res_mia.status_code == 200
    mia_data = res_mia.json()
    assert "membership_leakage_asr" in mia_data
    assert "risk_tier" in mia_data

    # Model Inversion Audit
    inv_payload = {"gradient_norms": [0.012, 0.018, 0.015, 0.022]}
    res_inv = client.post("/api/v1/privacy-defense/audit/model-inversion", json=inv_payload)
    assert res_inv.status_code == 200
    inv_data = res_inv.json()
    assert "reconstruction_risk_score" in inv_data
    assert "risk_tier" in inv_data


# ── 11. Model Registry, Shadow Metrics & Real-Time Inference Contract ─────────


def test_registry_and_inference_contract(client: TestClient):
    """Validate model registry tracking, shadow evaluation, and real-time scoring."""
    # List model versions for mock simulation
    res_versions = client.get("/api/v1/registry/live_prod_v2/versions")
    assert res_versions.status_code == 200
    assert isinstance(res_versions.json(), list)

    # Shadow metrics
    res_shadow = client.get("/api/v1/registry/live_prod_v2/shadow/metrics")
    assert res_shadow.status_code == 200
    shadow_data = res_shadow.json()
    assert "champion_version" in shadow_data

    # List Scenarios
    res_scenarios = client.get("/api/v1/scenarios")
    assert res_scenarios.status_code == 200
    scenarios = res_scenarios.json()
    assert isinstance(scenarios, list)
    assert len(scenarios) >= 3

    # Predict transaction contract
    predict_payload = {
        "transaction_amount": 12500.0,
        "merchant_category": "crypto_exchange",
        "country_code": "NG",
        "device_type": "mobile_ios",
        "velocity": 7.4,
        "hour_of_day": 3,
        "merchant_risk_score": 0.85,
        "customer_history_score": 0.40,
        "chargeback_count": 2,
        "account_age_days": 14,
        "bank_id": "bank_a",
    }
    res_predict = client.post("/api/v1/predict", json=predict_payload)
    assert res_predict.status_code == 200
    pred_data = res_predict.json()
    assert "fraud_probability" in pred_data
    assert "risk_score" in pred_data
    assert "is_fraud_suspected" in pred_data
    assert "risk_level" in pred_data
    assert "policy_action" in pred_data
    assert isinstance(pred_data["triggered_rules"], list)

    # Transaction Feedback submission
    feedback_payload = {
        "transaction_id": "TXN-998822",
        "actual_label": 1,
        "simulation_id": "live_prod_v2",
    }
    res_feedback = client.post("/api/v1/predict/feedback", json=feedback_payload)
    assert res_feedback.status_code == 200
    fb_data = res_feedback.json()
    assert "status" in fb_data

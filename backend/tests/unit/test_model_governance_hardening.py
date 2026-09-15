"""Unit and hardening tests for Model Governance, SR 11-7 Validation & Fairness Audits."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.application.services.model_governance_service import (
    CanaryQualityGate,
    ModelGovernanceService,
)
from app.domain.model_governance import (
    DualSignoffGate,
    ModelGovernanceError,
    ModelRegistryVault,
    ModelStatus,
    SemanticVersion,
    ShadowDeploymentEngine,
)
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_distinct_user_four_eyes_enforcement() -> None:
    """Verifies DualSignoffGate rejects self-approval by the same actor under different roles (Vector 16)."""
    gate = DualSignoffGate()

    # 1. Single user attempting to sign off as both roles (Four-Eyes violation)
    self_approval_attempt = [
        {"role": "ml_engineer", "user": "alice_risk", "signature": "sig_mle_999"},
        {"role": "compliance_officer", "user": "alice_risk", "signature": "sig_comp_999"},
    ]
    can_promote, reason = gate.can_promote(self_approval_attempt)
    assert can_promote is False
    assert "Four-Eyes Principle violation" in reason
    assert "self-approval prohibited" in reason

    # 2. Distinct authorized actors succeed
    valid_dual_signoff = [
        {"role": "ml_engineer", "user": "alice_risk", "signature": "sig_mle_999"},
        {"role": "compliance_officer", "user": "bob_auditor", "signature": "sig_comp_888"},
    ]
    can_promote2, reason2 = gate.can_promote(valid_dual_signoff)
    assert can_promote2 is True
    assert "Dual Signoff Gate Passed" in reason2


def test_disparate_impact_eeoc_80_rule_and_division_guards() -> None:
    """Verifies Disparate Impact calculation handles edge cases and enforces the EEOC 80% Rule (0.80 <= DI <= 1.25)."""
    svc = ModelGovernanceService()

    # 1. Equal treatment passing EEOC rule
    scores = [0.85, 0.90, 0.20, 0.15, 0.88, 0.92, 0.25, 0.10]
    sens = [1, 1, 1, 1, 0, 0, 0, 0]  # 2 positive per group out of 4 -> rates = 0.5, DI = 1.0
    res1 = svc.audit_fairness(scores, sens, threshold=0.50)
    assert res1["disparate_impact_ratio"] == 1.0
    assert res1["eeoc_80_percent_rule"] == "PASSED"
    assert res1["overall_fairness_status"] == "COMPLIANT"

    # 2. Extreme disparity (protected group has 0 selection, reference has 1.0)
    scores_biased = [0.10, 0.20, 0.30, 0.95, 0.90, 0.85]
    sens_biased = [1, 1, 1, 0, 0, 0]
    res2 = svc.audit_fairness(scores_biased, sens_biased, threshold=0.50)
    assert res2["disparate_impact_ratio"] == 0.0
    assert res2["eeoc_80_percent_rule"] == "FAILED"
    assert res2["overall_fairness_status"] == "FLAGGED_FOR_REVIEW"

    # 3. Both groups 0 selection (guarded division, should yield 1.0)
    scores_none = [0.1, 0.2, 0.1, 0.2]
    sens_none = [1, 1, 0, 0]
    res3 = svc.audit_fairness(scores_none, sens_none, threshold=0.80)
    assert res3["disparate_impact_ratio"] == 1.0
    assert res3["eeoc_80_percent_rule"] == "PASSED"

    # 4. Reference group has 0 selection but protected has selection (safe 999.0 clamp)
    scores_inv = [0.9, 0.9, 0.1, 0.1]
    sens_inv = [1, 1, 0, 0]
    res4 = svc.audit_fairness(scores_inv, sens_inv, threshold=0.50)
    assert res4["disparate_impact_ratio"] == 999.0
    assert res4["eeoc_80_percent_rule"] == "FAILED"


def test_equal_opportunity_and_average_odds_difference() -> None:
    """Verifies True Positive Rate, False Positive Rate, Equal Opportunity Difference, and Average Odds Difference."""
    svc = ModelGovernanceService()

    y_pred_probs = [0.8, 0.7, 0.3, 0.2, 0.9, 0.6, 0.4, 0.1]
    y_true = [1, 1, 0, 0, 1, 1, 0, 0]
    sensitive_attrs = [1, 1, 1, 1, 0, 0, 0, 0]

    report = svc.audit_fairness(
        y_pred_probs=y_pred_probs,
        sensitive_attributes=sensitive_attrs,
        y_true=y_true,
        threshold=0.50,
    )

    assert "equal_opportunity_difference" in report
    assert "average_odds_difference" in report
    assert report["protected_tpr"] == 1.0  # Both actual positives predicted positive
    assert report["reference_tpr"] == 1.0
    assert report["equal_opportunity_difference"] == 0.0
    assert report["average_odds_difference"] == 0.0


def test_conceptual_soundness_audit_checklist() -> None:
    """Verifies SR 11-7 Pillar I Conceptual Soundness checklist across GNN, Dirichlet, and DP accounting."""
    svc = ModelGovernanceService()
    report = svc.audit_conceptual_soundness()

    assert report["compliance_score_pct"] == 100.0
    assert report["overall_status"] == "COMPLIANT"
    assert report["total_clauses"] == 6
    assert report["passed_clauses"] == 6

    clause_ids = {c["clause_id"] for c in report["clauses"]}
    assert "SR11-7-PILLAR1-GNN" in clause_ids
    assert "SR11-7-PILLAR1-CALIBRATION" in clause_ids
    assert "SR11-7-PILLAR1-DIRICHLET" in clause_ids
    assert "SR11-7-PILLAR1-DP" in clause_ids
    assert "SR11-7-PILLAR1-ZERO-PII" in clause_ids
    assert "SR11-7-PILLAR1-LOSS" in clause_ids


def test_sr11_7_validation_schedule_cadence_and_deadlines() -> None:
    """Verifies 4-quarter independent model validation schedule generation and milestone tracking."""
    svc = ModelGovernanceService()
    schedule = svc.get_validation_schedule(reference_year=2026)

    assert schedule["reference_year"] == 2026
    assert schedule["overall_mrm_status"] == "COMPLIANT"
    assert len(schedule["milestones"]) == 4

    quarters = [m["quarter"] for m in schedule["milestones"]]
    assert quarters == ["Q1 2026", "Q2 2026", "Q3 2026", "Q4 2026"]
    assert schedule["milestones"][0]["status"] == "COMPLETED"
    assert schedule["milestones"][1]["status"] == "COMPLETED"
    assert schedule["milestones"][2]["status"] == "ON_TRACK"
    assert schedule["milestones"][3]["status"] == "SCHEDULED"


def test_canary_quality_gate_evaluation() -> None:
    """Verifies CanaryQualityGate approval and rejection conditions."""
    gate = CanaryQualityGate()

    champion_metrics = {"auc_roc": 0.85, "disparate_impact_ratio": 1.0, "p99_latency_ms": 45.0, "fpr": 0.02}

    # 1. Candidate strictly superior on AUC and nominal on all gates -> APPROVE
    cand_pass = {"auc_roc": 0.88, "disparate_impact_ratio": 0.95, "p99_latency_ms": 50.0, "fpr": 0.015}
    eval1 = gate.evaluate(cand_pass, champion_metrics)
    assert eval1["passed"] is True
    assert eval1["decision"] == "APPROVE_CANARY_PROMOTION"
    assert len(eval1["reasons"]) == 0

    # 2. Candidate violates disparate impact (< 0.80) -> REJECT
    cand_bias = {"auc_roc": 0.89, "disparate_impact_ratio": 0.65, "p99_latency_ms": 50.0, "fpr": 0.02}
    eval2 = gate.evaluate(cand_bias, champion_metrics)
    assert eval2["passed"] is False
    assert eval2["decision"] == "REJECT_CANARY_PROMOTION"
    assert any("Disparate Impact ratio" in r for r in eval2["reasons"])

    # 3. Candidate violates latency SLA (> 200ms) -> REJECT
    cand_slow = {"auc_roc": 0.89, "disparate_impact_ratio": 1.0, "p99_latency_ms": 250.0, "fpr": 0.02}
    eval3 = gate.evaluate(cand_slow, champion_metrics)
    assert eval3["passed"] is False
    assert any("p99 inference latency" in r for r in eval3["reasons"])


def test_semantic_version_complex_parsing() -> None:
    """Verifies SemanticVersion correctly parses complex tags with pre-release and build markers."""
    v_rc = SemanticVersion.parse("v2.1.0-rc1")
    assert v_rc.major == 2
    assert v_rc.minor == 1
    assert v_rc.patch == 0
    assert v_rc.to_tag() == "v2.1.0"

    v_build = SemanticVersion.parse("3.4.5+build.2026")
    assert v_build.major == 3
    assert v_build.minor == 4
    assert v_build.patch == 5

    v_fallback = SemanticVersion.parse("invalid_tag")
    assert v_fallback.major == 1
    assert v_fallback.minor == 0
    assert v_fallback.patch == 0


def test_shadow_deployment_sha256_routing_distribution() -> None:
    """Verifies ShadowDeploymentEngine uses SHA-256 for deterministic, well-distributed routing."""
    engine = ShadowDeploymentEngine(shadow_ratio=0.10)

    # Across 1000 pseudo-unique request IDs
    routed_count = sum(1 for i in range(1000) if engine.should_route_to_shadow(f"tx_req_sha_{i}"))
    assert 70 <= routed_count <= 130

    # Empty request ID returns False
    assert engine.should_route_to_shadow("") is False


def test_model_registry_vault_parameter_validation() -> None:
    """Verifies ModelRegistryVault.register_checkpoint rejects empty weights or invalid DP parameters."""
    vault = ModelRegistryVault()

    # Empty weights rejected
    with pytest.raises(ModelGovernanceError, match="weights_bytes must not be empty"):
        vault.register_checkpoint(
            version_str="v1.0.0",
            weights_bytes=b"",
            hyperparameters={},
            dataset_hash="hash",
            dp_epsilon=1.0,
        )

    # Invalid epsilon <= 0 rejected
    with pytest.raises(ModelGovernanceError, match="dp_epsilon must be positive"):
        vault.register_checkpoint(
            version_str="v1.0.0",
            weights_bytes=b"valid_weights",
            hyperparameters={},
            dataset_hash="hash",
            dp_epsilon=-0.5,
        )

    # Invalid delta <= 0 or >= 1 rejected
    with pytest.raises(ModelGovernanceError, match="dp_delta must be in"):
        vault.register_checkpoint(
            version_str="v1.0.0",
            weights_bytes=b"valid_weights",
            hyperparameters={},
            dataset_hash="hash",
            dp_epsilon=1.0,
            dp_delta=1.5,
        )


def test_compliance_fastapi_endpoints(client: TestClient) -> None:
    """Verifies compliance REST API routes return compliant 200 OK responses."""
    # 1. SOC2 evidence report (both /v1/compliance and /api/v1/compliance)
    res_soc2_legacy = client.get("/v1/compliance/soc2-evidence")
    assert res_soc2_legacy.status_code == 200

    res_soc2_canonical = client.get("/api/v1/compliance/soc2-evidence")
    assert res_soc2_canonical.status_code == 200

    # 2. SR 11-7 conceptual soundness audit
    res_sr11 = client.get("/api/v1/compliance/sr11-7/audit")
    assert res_sr11.status_code == 200
    data_sr11 = res_sr11.json()
    assert data_sr11["compliance_score_pct"] == 100.0
    assert data_sr11["overall_status"] == "COMPLIANT"

    # 3. SR 11-7 validation schedule
    res_sched = client.get("/api/v1/compliance/sr11-7/schedule?year=2026")
    assert res_sched.status_code == 200
    data_sched = res_sched.json()
    assert len(data_sched["milestones"]) == 4

    # 4. Fairness audit evaluation endpoint
    fairness_payload = {
        "y_pred_probs": [0.85, 0.20, 0.90, 0.15],
        "sensitive_attributes": [1, 1, 0, 0],
        "y_true": [1, 0, 1, 0],
        "threshold": 0.50,
    }
    res_fair = client.post("/api/v1/compliance/fairness/evaluate", json=fairness_payload)
    assert res_fair.status_code == 200
    data_fair = res_fair.json()
    assert data_fair["disparate_impact_ratio"] == 1.0
    assert data_fair["eeoc_80_percent_rule"] == "PASSED"

    # 5. Canary gate evaluation endpoint
    canary_payload = {
        "candidate_metrics": {
            "auc_roc": 0.87,
            "disparate_impact_ratio": 1.05,
            "p99_latency_ms": 65.0,
            "fpr": 0.015,
        },
        "champion_metrics": {
            "auc_roc": 0.85,
            "disparate_impact_ratio": 1.0,
            "p99_latency_ms": 70.0,
            "fpr": 0.02,
        },
    }
    res_canary = client.post("/api/v1/compliance/sr11-7/canary-gate", json=canary_payload)
    assert res_canary.status_code == 200
    data_canary = res_canary.json()
    assert data_canary["passed"] is True
    assert data_canary["decision"] == "APPROVE_CANARY_PROMOTION"

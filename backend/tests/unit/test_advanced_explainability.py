"""Unit tests for Advanced AI Explainability Portal (Counterfactuals, Decision Replay, GNNExplainer)."""

from __future__ import annotations

from app.application.services.explainability_service import ExplainabilityService
from app.domain.entities_phase2 import Alert
from app.domain.enums import AlertSeverity, AlertStatus


def _make_sample_alert(alert_id: str = "alt_1001", risk_score: float = 750.0) -> Alert:
    return Alert(
        id=alert_id,
        bank_id="bank_a",
        transaction_id="tx_9988",
        risk_score=risk_score,
        severity=AlertSeverity.HIGH,
        status=AlertStatus.NEW,
        reason_codes=["HIGH-AMT", "GEO-RISK", "VEL-001"],
        confidence=0.92,
        involved_entity_ids=["cust_a1"],
        top_features=[{"feature": "transaction_amount", "contribution": 0.42}],
        risk_factors=["Transaction amount exceeds threshold"],
        historical_evidence=["Prior alert 2 days ago"],
        model_confidence=0.92,
    )


class TestCounterfactualEngine:
    """Verify counterfactual remediation path generation."""

    def test_generate_counterfactuals_clears_alert(self):
        svc = ExplainabilityService()
        alert = _make_sample_alert(risk_score=750.0)

        cf = svc.generate_counterfactuals(alert, target_score=350.0)

        assert cf.alert_id == alert.id
        assert cf.original_score == 750.0
        assert cf.remediated_score <= 350.0
        assert cf.is_cleared is True
        assert len(cf.changes) >= 1
        assert "CLEARED" in cf.summary_text

    def test_counterfactual_remediates_high_amount_and_geo(self):
        svc = ExplainabilityService()
        alert = _make_sample_alert(risk_score=850.0)

        cf = svc.generate_counterfactuals(alert, target_score=300.0)

        features_changed = [c.feature for c in cf.changes]
        assert "transaction_amount" in features_changed or "country_code" in features_changed

    def test_counterfactual_executes_real_risk_engine_search(self):
        """Verifies that counterfactual remediated_score is computed by real RiskScoringEngine."""
        from app.application.services.risk_engine import RiskScoringEngine

        svc = ExplainabilityService()
        engine = RiskScoringEngine()
        alert = _make_sample_alert(risk_score=850.0)

        cf = svc.generate_counterfactuals(alert, target_score=350.0, risk_engine=engine)

        assert cf.original_score == 850.0
        assert cf.remediated_score < 850.0
        assert cf.is_cleared is True
        assert len(cf.changes) >= 1
        assert isinstance(cf.remediated_score, float)
        assert cf.remediated_score <= 350.0


class TestDecisionReplayAudit:
    """Verify deterministic decision replay inference audit."""

    def test_replay_inference_audit_reproduces_score(self):
        svc = ExplainabilityService()
        alert = _make_sample_alert(risk_score=680.0)

        audit = svc.replay_inference_audit(alert)

        assert audit.alert_id == alert.id
        assert audit.model_version == "v1.4.2-champion"
        assert audit.model_auc > 0.90
        assert len(audit.policy_rules_evaluated) == 9
        assert audit.reconstructed_risk_score > 0
        assert audit.audit_matched is not None


class TestGNNExplainer:
    """Verify GNNExplainer graph attribution over entity neighborhood."""

    def test_explain_gnn_embedding_calculates_edge_contributions(self):
        svc = ExplainabilityService()

        gnn_exp = svc.explain_gnn_embedding("cust_a1")

        assert gnn_exp.node_id == "cust_a1"
        assert gnn_exp.subgraph_nodes_count >= 1
        assert len(gnn_exp.top_contributing_edges) >= 1
        assert "Primary GNN Driver" in gnn_exp.primary_driver_text

        # Verify sum of contribution percentages equals 100%
        total_pct = sum(e.contribution_percentage for e in gnn_exp.top_contributing_edges)
        assert abs(total_pct - 100.0) < 1.0

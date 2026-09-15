"""Unit tests for GNN Privacy Audit Service (LRA and MIA attacks)."""

from __future__ import annotations

import numpy as np
import pytest

from app.application.services.privacy_audit_service import PrivacyAuditService


@pytest.fixture
def audit_service() -> PrivacyAuditService:
    return PrivacyAuditService()


class TestPrivacyAuditService:
    def test_audit_link_reconstruction_empty(self, audit_service: PrivacyAuditService) -> None:
        result = audit_service.audit_link_reconstruction(
            embeddings={},
            adjacency_lists=[],
            node_id_to_index={},
        )
        assert result["link_leakage_auc"] == 0.5
        assert result["risk_tier"] == "safe"

    def test_audit_link_reconstruction_leakage_measurement(
        self, audit_service: PrivacyAuditService
    ) -> None:
        # 3 nodes: index 0, 1, 2
        # Embs: node_0 and node_1 have identical embeddings (perfect cosine similarity 1.0)
        # and they are linked. Node_2 has an orthogonal embedding.
        embeddings = {
            "node_0": np.array([1.0, 0.0]),
            "node_1": np.array([1.0, 0.0]),
            "node_2": np.array([0.0, 1.0]),
        }
        node_id_to_index = {"node_0": 0, "node_1": 1, "node_2": 2}

        # Positive link between 0 and 1
        adjacency_lists = [[1], [0], []]

        result = audit_service.audit_link_reconstruction(
            embeddings=embeddings,
            adjacency_lists=adjacency_lists,
            node_id_to_index=node_id_to_index,
        )

        assert "link_leakage_auc" in result
        # AUC should be computed and be >= 0.5
        assert result["link_leakage_auc"] >= 0.5
        assert result["risk_tier"] in ("low_risk", "moderate_risk", "high_risk")

    def test_audit_membership_inference_empty(self, audit_service: PrivacyAuditService) -> None:
        result = audit_service.audit_membership_inference(
            train_losses=[],
            test_losses=[],
        )
        assert result["membership_leakage_asr"] == 0.5
        assert result["risk_tier"] == "safe"

    def test_audit_membership_inference_leakage_measurement(
        self, audit_service: PrivacyAuditService
    ) -> None:
        # Member losses are significantly lower than non-member losses (perfect leakage signal)
        train_losses = [0.01, 0.02, 0.03, 0.015, 0.025]
        test_losses = [0.5, 0.6, 0.45, 0.55, 0.7]

        result = audit_service.audit_membership_inference(
            train_losses=train_losses,
            test_losses=test_losses,
        )

        assert "membership_leakage_asr" in result
        # The threshold classifier should correctly separate them with high accuracy (ASR near 1.0)
        assert result["membership_leakage_asr"] >= 0.8
        assert result["risk_tier"] == "high_risk"

    def test_audit_membership_inference_random_chance(
        self, audit_service: PrivacyAuditService
    ) -> None:
        # Train and test losses are identical (random distribution, zero leakage signal)
        train_losses = [0.1, 0.2, 0.3, 0.4, 0.5]
        test_losses = [0.1, 0.2, 0.3, 0.4, 0.5]

        result = audit_service.audit_membership_inference(
            train_losses=train_losses,
            test_losses=test_losses,
        )

        assert (
            result["membership_leakage_asr"] == 0.5
        )  # Median threshold splits them: TP=3, TN=2 -> ASR = 5/10 = 0.5
        assert result["risk_tier"] == "low_risk"

    # ── Model Inversion Tests ──────────────────────────────────

    def test_audit_model_inversion_empty(self, audit_service: PrivacyAuditService) -> None:
        """Empty gradient norms should return safe tier without error."""
        result = audit_service.audit_model_inversion(gradient_norms=[])
        assert result["reconstruction_risk_score"] == 0.0
        assert result["risk_tier"] == "safe"

    def test_audit_model_inversion_low_risk(self, audit_service: PrivacyAuditService) -> None:
        """Homogeneous gradient norms (low CV) should yield low reconstruction risk."""
        # All norms equal → zero std → CV ≈ 0 → score < 0.3
        norms = [1.0, 1.0, 1.0, 1.0, 1.0]
        result = audit_service.audit_model_inversion(gradient_norms=norms)
        assert result["reconstruction_risk_score"] < 0.3
        assert result["risk_tier"] == "low_risk"

    def test_audit_model_inversion_high_risk(self, audit_service: PrivacyAuditService) -> None:
        """Highly variable gradient norms should yield high reconstruction risk."""
        # High variance relative to mean → CV > 0.6
        norms = [0.001, 100.0, 0.001, 100.0, 0.001]
        result = audit_service.audit_model_inversion(gradient_norms=norms)
        assert result["reconstruction_risk_score"] >= 0.6
        assert result["risk_tier"] == "high_risk"
        assert "mean_gradient_norm" in result
        assert "num_gradients_audited" in result
        assert result["num_gradients_audited"] == 5

    # ── DLG Gradient Leakage Tests ─────────────────────────────

    def test_audit_dlg_empty_inputs(self, audit_service: PrivacyAuditService) -> None:
        """Empty gradient vectors should return safe tier without error."""
        result = audit_service.audit_gradient_leakage_dlg(
            original_gradients=[],
            received_gradients=[],
        )
        assert result["dlg_leakage_score"] == 0.0
        assert result["risk_tier"] == "safe"

    def test_audit_dlg_low_leakage_with_noise(self, audit_service: PrivacyAuditService) -> None:
        """Gradients with DP noise (uncorrelated) should yield low DLG score."""
        rng = np.random.default_rng(42)
        original = rng.normal(0, 1, 50).tolist()
        # Received = independent noise → near-zero Pearson correlation
        received = rng.normal(0, 1, 50).tolist()
        result = audit_service.audit_gradient_leakage_dlg(
            original_gradients=original,
            received_gradients=received,
        )
        assert result["dlg_leakage_score"] < 0.5  # relaxed bound for random data
        assert "params_audited" in result
        assert result["params_audited"] == 50

    def test_audit_dlg_high_leakage_perfect_correlation(
        self, audit_service: PrivacyAuditService
    ) -> None:
        """Identical gradient vectors should yield maximum DLG leakage score."""
        gradients = [float(i) for i in range(1, 21)]
        result = audit_service.audit_gradient_leakage_dlg(
            original_gradients=gradients,
            received_gradients=gradients,  # perfect correlation → score == 1.0
        )
        assert result["dlg_leakage_score"] >= 0.99
        assert result["risk_tier"] == "high_risk"

    def test_audit_link_reconstruction_single_node(
        self, audit_service: PrivacyAuditService
    ) -> None:
        """Single node graph should return safe tier due to insufficient structure."""
        result = audit_service.audit_link_reconstruction(
            embeddings={"node_0": np.array([1.0, 0.0])},
            adjacency_lists=[[]],
            node_id_to_index={"node_0": 0},
        )
        assert result["link_leakage_auc"] == 0.5
        assert result["risk_tier"] == "safe"

    def test_audit_model_inversion_negative_norms_raises_error(
        self, audit_service: PrivacyAuditService
    ) -> None:
        """Negative gradient norms should raise ValueError."""
        with pytest.raises(ValueError, match="Gradient norms must be non-negative"):
            audit_service.audit_model_inversion(gradient_norms=[1.0, -2.0, 3.0])

    def test_audit_dlg_single_element_gradients(
        self, audit_service: PrivacyAuditService
    ) -> None:
        """Length < 2 gradient vectors should return safe tier with explanatory message."""
        result = audit_service.audit_gradient_leakage_dlg(
            original_gradients=[1.0],
            received_gradients=[1.0],
        )
        assert result["dlg_leakage_score"] == 0.0
        assert result["risk_tier"] == "safe"
        assert "min 2 elements required" in result["message"]


class TestPrivacyServiceBudgetLog:
    """Tests for multi-simulation privacy budget summary (get_all_budgets_summary)."""

    def test_empty_budget_log(self) -> None:
        """get_all_budgets_summary returns empty list when no simulations tracked."""
        from app.application.services.privacy_service import PrivacyService

        svc = PrivacyService()
        result = svc.get_all_budgets_summary()
        assert result == []

    def test_budget_log_single_simulation(self) -> None:
        """A single simulation's epsilon spend is reflected in the summary."""
        from app.application.services.privacy_service import PrivacyService

        svc = PrivacyService()
        svc.record_opacus_epsilon(simulation_id="sim-test-001", epsilon=1.5, limit=8.0)
        result = svc.get_all_budgets_summary(epsilon_limit=8.0)
        assert len(result) == 1
        entry = result[0]
        assert entry["simulation_id"] == "sim-test-001"
        assert abs(entry["total_epsilon"] - 1.5) < 1e-4
        assert entry["budget_exhausted"] is False

    def test_budget_log_exhaustion_flag(self) -> None:
        """Simulations exceeding epsilon_limit are flagged as exhausted in the summary."""
        from app.application.services.privacy_service import PrivacyService

        svc = PrivacyService()
        # Bypass the spend guard by manually appending to _epsilon_history
        budget = svc.get_or_create_budget("sim-exhaust-001")
        budget._epsilon_history.append(9.0)  # noqa: SLF001  # bypasses the raise-guard for testing
        budget.rounds_spent = 1
        result = svc.get_all_budgets_summary(epsilon_limit=8.0)
        assert len(result) == 1
        assert result[0]["budget_exhausted"] is True
        assert result[0]["total_epsilon"] > 8.0

    def test_budget_log_sorted_by_epsilon_descending(self) -> None:
        """Budget log must be sorted highest epsilon first."""
        from app.application.services.privacy_service import PrivacyService

        svc = PrivacyService()
        svc.record_opacus_epsilon("sim-low", epsilon=1.0, limit=8.0)
        svc.record_opacus_epsilon("sim-high", epsilon=5.0, limit=8.0)
        svc.record_opacus_epsilon("sim-mid", epsilon=3.0, limit=8.0)
        result = svc.get_all_budgets_summary()
        epsilons = [e["total_epsilon"] for e in result]
        assert epsilons == sorted(epsilons, reverse=True)

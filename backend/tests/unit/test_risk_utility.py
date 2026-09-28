"""Unit tests for Cost-Sensitive Risk Utility & Empirical Threshold Sweeps.

Verifies:
  1. CostMatrixConfig validation and serialization.
  2. Confusion matrix computation, partition completeness, and scale alignment.
  3. Mathematical monotonicity invariants (Recall and FPR decrease as threshold increases).
  4. Financial cost and net savings objective functions.
  5. Boundary conditions (extreme low and high cutoffs).
  6. RiskUtilityService sweep evaluation and Bayesian threshold recommendations.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.risk_utility import (
    DEFAULT_COST_FN,
    DEFAULT_COST_FP,
    DEFAULT_COST_TN,
    DEFAULT_COST_TP,
    DEFAULT_DISCRETE_THRESHOLDS,
    CostMatrixConfig,
    RiskUtilityService,
    ThresholdSweepReport,
    calculate_confusion_matrix,
    compute_threshold_metrics,
)


class TestCostMatrixConfig:
    """Verifies cost matrix configuration validation and invariants."""

    def test_default_initialization(self) -> None:
        cfg = CostMatrixConfig()
        assert cfg.cost_fn == DEFAULT_COST_FN
        assert cfg.cost_fp == DEFAULT_COST_FP
        assert cfg.cost_tp == DEFAULT_COST_TP
        assert cfg.cost_tn == DEFAULT_COST_TN
        assert cfg.currency == "USD"

    def test_custom_positive_costs(self) -> None:
        cfg = CostMatrixConfig(cost_fn=1200.0, cost_fp=60.0, cost_tp=20.0, cost_tn=1.0, currency="EUR")
        assert cfg.cost_fn == 1200.0
        assert cfg.cost_fp == 60.0
        assert cfg.cost_tp == 20.0
        assert cfg.cost_tn == 1.0
        assert cfg.currency == "EUR"

    def test_rejects_negative_costs(self) -> None:
        with pytest.raises(ValueError, match="cost_fn must be non-negative"):
            CostMatrixConfig(cost_fn=-1.0)

        with pytest.raises(ValueError, match="cost_fp must be non-negative"):
            CostMatrixConfig(cost_fp=-10.0)

        with pytest.raises(ValueError, match="cost_tp must be non-negative"):
            CostMatrixConfig(cost_tp=-5.0)

        with pytest.raises(ValueError, match="cost_tn must be non-negative"):
            CostMatrixConfig(cost_tn=-0.01)

    def test_to_dict_structure(self) -> None:
        cfg = CostMatrixConfig(cost_fn=500.0, cost_fp=30.0)
        d = cfg.to_dict()
        assert d == {
            "cost_fn": 500.0,
            "cost_fp": 30.0,
            "cost_tp": DEFAULT_COST_TP,
            "cost_tn": DEFAULT_COST_TN,
            "currency": "USD",
        }


class TestConfusionMatrixCalculation:
    """Verifies partition completeness, dimension checks, and scale alignment."""

    def test_dimension_mismatch_raises(self) -> None:
        y_true = np.array([1, 0, 1])
        scores = np.array([0.9, 0.1])
        with pytest.raises(ValueError, match="Dimension mismatch"):
            calculate_confusion_matrix(y_true, scores, 0.5)

    def test_partition_completeness(self) -> None:
        rng = np.random.default_rng(42)
        y_true = rng.integers(0, 2, size=200)
        scores = rng.uniform(0.0, 1.0, size=200)

        for thresh in [0.1, 0.3, 0.5, 0.7, 0.9]:
            tp, fp, tn, fn = calculate_confusion_matrix(y_true, scores, thresh)
            assert tp + fp + tn + fn == 200
            assert tp >= 0
            assert fp >= 0
            assert tn >= 0
            assert fn >= 0

    def test_scale_alignment_integer_threshold_prob_scores(self) -> None:
        """When threshold is in [0, 1000] and scores are in [0, 1], scores scale up."""
        y_true = np.array([1, 0, 1, 0])
        scores = np.array([0.85, 0.60, 0.70, 0.20])  # in [0, 1]
        threshold = 750.0  # in [0, 1000]

        # Scaled scores: [850, 600, 700, 200]
        # At threshold 750: index 0 (true pos=1) is >= 750 -> TP=1
        # index 1 (true pos=0, 600) -> TN
        # index 2 (true pos=1, 700) -> FN
        # index 3 (true pos=0, 200) -> TN
        tp, fp, tn, fn = calculate_confusion_matrix(y_true, scores, threshold)
        assert (tp, fp, tn, fn) == (1, 0, 2, 1)

    def test_scale_alignment_prob_threshold_integer_scores(self) -> None:
        """When threshold is in [0, 1] and scores are in [0, 1000], threshold scales up."""
        y_true = np.array([1, 0, 1, 0])
        scores = np.array([850.0, 600.0, 700.0, 200.0])  # in [0, 1000]
        threshold = 0.75  # in [0, 1]

        # Effective threshold: 750.0
        tp, fp, tn, fn = calculate_confusion_matrix(y_true, scores, threshold)
        assert (tp, fp, tn, fn) == (1, 0, 2, 1)

    def test_exact_matches(self) -> None:
        y_true = np.array([1, 1, 0, 0])
        scores = np.array([750.0, 800.0, 600.0, 400.0])
        tp, fp, tn, fn = calculate_confusion_matrix(y_true, scores, 750.0)
        assert (tp, fp, tn, fn) == (2, 0, 2, 0)


class TestThresholdMetricsInvariants:
    """Verifies mathematical monotonicity and cost-utility consistency."""

    @pytest.fixture
    def synthetic_dataset(self) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(101)
        n_pos = 100
        n_neg = 900
        # Positive scores centered higher, negative scores centered lower
        pos_scores = rng.normal(780.0, 80.0, size=n_pos).clip(0.0, 1000.0)
        neg_scores = rng.normal(350.0, 120.0, size=n_neg).clip(0.0, 1000.0)

        y_true = np.concatenate([np.ones(n_pos, dtype=int), np.zeros(n_neg, dtype=int)])
        scores = np.concatenate([pos_scores, neg_scores])
        return y_true, scores

    def test_monotonicity_of_recall_and_fpr(
        self, synthetic_dataset: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """As decision threshold increases, Recall and FPR must be monotonically non-increasing."""
        y_true, scores = synthetic_dataset
        thresholds = [400.0, 500.0, 600.0, 700.0, 800.0, 900.0]

        recalls = []
        fprs = []
        for t in thresholds:
            m = compute_threshold_metrics(y_true, scores, t)
            recalls.append(m.recall)
            fprs.append(m.fpr)

        for i in range(len(thresholds) - 1):
            assert recalls[i] >= recalls[i + 1] - 1e-9, f"Recall non-monotonic at {thresholds[i]}"
            assert fprs[i] >= fprs[i + 1] - 1e-9, f"FPR non-monotonic at {thresholds[i]}"

    def test_cost_and_net_savings_arithmetic(
        self, synthetic_dataset: tuple[np.ndarray, np.ndarray]
    ) -> None:
        y_true, scores = synthetic_dataset
        cfg = CostMatrixConfig(cost_fn=1000.0, cost_fp=50.0, cost_tp=20.0, cost_tn=0.0)
        m = compute_threshold_metrics(y_true, scores, 700.0, cfg)

        expected_total_cost = 1000.0 * m.fn + 50.0 * m.fp + 20.0 * m.tp + 0.0 * m.tn
        assert abs(m.total_cost - expected_total_cost) < 1e-5

        total_positives = m.tp + m.fn
        expected_baseline_cost = 1000.0 * total_positives
        assert abs(m.net_savings - (expected_baseline_cost - expected_total_cost)) < 1e-5
        assert abs(m.efficiency_ratio - (m.net_savings / expected_baseline_cost)) < 1e-5

    def test_extreme_boundary_low_threshold(self) -> None:
        """At threshold = 0.0, all samples are predicted positive."""
        y_true = np.array([1, 1, 0, 0])
        scores = np.array([100.0, 200.0, 50.0, 80.0])
        m = compute_threshold_metrics(y_true, scores, 0.0)

        assert m.tp == 2
        assert m.fp == 2
        assert m.tn == 0
        assert m.fn == 0
        assert m.recall == 1.0
        assert m.fpr == 1.0

    def test_extreme_boundary_high_threshold(self) -> None:
        """At threshold = 1000.0 when all scores < 1000, all samples predicted negative."""
        y_true = np.array([1, 1, 0, 0])
        scores = np.array([800.0, 850.0, 600.0, 700.0])
        m = compute_threshold_metrics(y_true, scores, 1000.0)

        assert m.tp == 0
        assert m.fp == 0
        assert m.tn == 2
        assert m.fn == 2
        assert m.recall == 0.0
        assert m.precision == 0.0
        assert m.f1_score == 0.0


class TestRiskUtilityService:
    """Verifies full threshold sweeps, optimal cutoff determination, and Bayesian theory."""

    def test_service_evaluate_sweep_structure(self) -> None:
        rng = np.random.default_rng(2026)
        y_true = np.array([1] * 50 + [0] * 450)
        # Positives have higher scores, negatives lower
        scores = np.concatenate([
            rng.normal(820.0, 60.0, size=50).clip(0, 1000),
            rng.normal(400.0, 100.0, size=450).clip(0, 1000),
        ])

        service = RiskUtilityService()
        report = service.evaluate_sweep(y_true, scores)

        assert isinstance(report, ThresholdSweepReport)
        assert report.n_samples == 500
        assert report.n_positives == 50
        assert report.n_negatives == 450
        assert len(report.sweep_points) == len(DEFAULT_DISCRETE_THRESHOLDS)

        # Baseline cost = 50 * 850 = $42,500
        assert report.baseline_cost == 50 * 850.0
        assert report.min_cost < report.baseline_cost
        assert report.max_net_savings > 0.0
        assert 500.0 <= report.optimal_threshold <= 900.0

        # Optimal point check
        opt_point = report.get_point(report.optimal_threshold)
        assert opt_point is not None
        assert abs(opt_point.total_cost - report.min_cost) < 1e-4

        # Non-existent point
        assert report.get_point(9999.0) is None

        # Serialization to dict
        d = report.to_dict()
        assert d["optimal_threshold"] == report.optimal_threshold
        assert len(d["sweep_points"]) == len(DEFAULT_DISCRETE_THRESHOLDS)
        assert "evaluated_at" in d

    def test_custom_thresholds_sweep(self) -> None:
        y_true = np.array([1, 1, 0, 0])
        scores = np.array([720.0, 810.0, 650.0, 300.0])
        service = RiskUtilityService()
        custom_cutoffs = [600.0, 700.0, 800.0]

        report = service.evaluate_sweep(y_true, scores, thresholds=custom_cutoffs)
        assert len(report.sweep_points) == 3
        assert [p.threshold for p in report.sweep_points] == custom_cutoffs

    def test_empty_sweep_fallback(self) -> None:
        y_true = np.array([1, 0])
        scores = np.array([800.0, 200.0])
        service = RiskUtilityService()
        report = service.evaluate_sweep(y_true, scores, thresholds=[])

        assert report.optimal_threshold == 750.0
        assert report.sweep_points == []

    def test_recommend_bayes_threshold_calculation(self) -> None:
        service = RiskUtilityService()
        # c_fn = 850, c_fp = 45 -> theta* = 45 / (45 + 850) = 45 / 895 = 0.050279...
        # tau* = theta* * 1000 = 50.279 -> clipped to 100.0 min
        bayes_tau = service.recommend_bayes_threshold(cost_fn=850.0, cost_fp=45.0)
        assert bayes_tau == 100.0

        # Balanced costs: c_fn = 100, c_fp = 100 -> theta* = 0.50 -> tau* = 500.0
        assert service.recommend_bayes_threshold(cost_fn=100.0, cost_fp=100.0) == 500.0

        # Higher FP cost: c_fn = 100, c_fp = 300 -> theta* = 300/400 = 0.75 -> tau* = 750.0
        assert service.recommend_bayes_threshold(cost_fn=100.0, cost_fp=300.0) == 750.0

        # Zero sum fallback
        assert service.recommend_bayes_threshold(cost_fn=0.0, cost_fp=0.0) == 750.0

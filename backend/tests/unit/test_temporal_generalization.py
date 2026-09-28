"""Unit and Integration Tests for Temporal Generalization and Out-of-Time Degradation Suite.

Validates:
1. Temporal split protocols and concept-drifted synthetic transaction stream generation.
2. Metric helper invariants (Recall @ fixed FPR, ECE, PSI, KS feature drift).
3. Fast neural classifier training, probability prediction, and partition evaluation.
4. Optimistic randomized K-fold cross-validation vs chronological OOT evaluation.
5. Pydantic v2 schema roundtrip serialization and markdown table formatting.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from experiments.temporal.temporal_generalization import (
    FeatureDriftProfile,
    KFoldMetrics,
    PeriodMetrics,
    TemporalDegradationMetrics,
    TemporalGeneralizationConfig,
    TemporalGeneralizationSuiteResult,
    TemporalSplitProtocol,
    _evaluate_kfold_cv,
    _evaluate_partition,
    _predict_proba,
    _train_model,
    calculate_ece,
    calculate_feature_drift,
    calculate_recall_at_fixed_fpr,
    compute_psi,
    format_temporal_benchmark_markdown,
    generate_temporal_stream,
    run_temporal_generalization_benchmark,
)


class TestTemporalSplitProtocolAndDataGeneration:
    """Validates chronological protocol enums and temporal stream synthesis."""

    def test_protocol_enum_members(self) -> None:
        """Assert all expected protocol phases are defined."""
        assert TemporalSplitProtocol.PERIOD_1_PAST == "PERIOD_1_PAST"
        assert TemporalSplitProtocol.PERIOD_2_PRESENT == "PERIOD_2_PRESENT"
        assert TemporalSplitProtocol.PERIOD_3_FUTURE == "PERIOD_3_FUTURE"

    def test_config_validation_and_defaults(self) -> None:
        """Verify default configuration attributes and constraints."""
        cfg = TemporalGeneralizationConfig()
        assert cfg.num_samples_per_period >= 200
        assert cfg.num_features >= 4
        assert 0.0 < cfg.base_fraud_rate < 0.5
        assert 0.0 <= cfg.drift_intensity <= 1.0
        assert cfg.k_folds >= 2

    def test_generate_temporal_stream_shapes_and_timestamps(self) -> None:
        """Verify generated periods have valid shapes, positive samples, and monotonic timestamps."""
        cfg = TemporalGeneralizationConfig(
            num_samples_per_period=400,
            num_features=8,
            base_fraud_rate=0.05,
            drift_intensity=0.30,
            random_seed=123,
        )
        p1, p2, p3 = generate_temporal_stream(cfg)

        for p_idx, p in enumerate([p1, p2, p3], start=1):
            assert p["X"].shape == (400, 8)
            assert p["y"].shape == (400,)
            assert p["timestamps"].shape == (400,)
            assert len(p["feature_names"]) == 8
            assert p["period_idx"] == p_idx
            assert np.sum(p["y"] == 1) >= 5
            # Timestamps must be sorted
            assert np.all(np.diff(p["timestamps"]) >= 0.0)

        # Timestamps of P2 must be strictly greater than P1
        assert p2["timestamps"][0] >= p1["timestamps"][-1]
        assert p3["timestamps"][0] >= p2["timestamps"][-1]

    def test_reproducible_stream_generation(self) -> None:
        """Confirm bit-identical arrays under identical seeds."""
        cfg1 = TemporalGeneralizationConfig(num_samples_per_period=200, random_seed=777)
        cfg2 = TemporalGeneralizationConfig(num_samples_per_period=200, random_seed=777)
        p1_a, _, _ = generate_temporal_stream(cfg1)
        p1_b, _, _ = generate_temporal_stream(cfg2)

        np.testing.assert_array_equal(p1_a["X"], p1_b["X"])
        np.testing.assert_array_equal(p1_a["y"], p1_b["y"])


class TestStatisticalMetricsAndDriftHelpers:
    """Validates metric calculations including Recall@FPR, ECE, PSI, and feature drift."""

    def test_calculate_recall_at_fixed_fpr_bounds(self) -> None:
        """Verify Recall @ fixed FPR returns bounded values in [0.0, 1.0]."""
        y_true = np.array([0] * 900 + [1] * 100)
        y_prob = np.concatenate([
            np.random.default_rng(42).uniform(0.0, 0.4, size=900),
            np.random.default_rng(42).uniform(0.6, 1.0, size=100),
        ])

        rec_01 = calculate_recall_at_fixed_fpr(y_true, y_prob, target_fpr=0.001)
        rec_10 = calculate_recall_at_fixed_fpr(y_true, y_prob, target_fpr=0.010)

        assert 0.0 <= rec_01 <= 1.0
        assert 0.0 <= rec_10 <= 1.0
        assert rec_10 >= rec_01  # higher FPR budget allows equal or higher recall

    def test_calculate_ece_metric(self) -> None:
        """Verify Expected Calibration Error calculation."""
        y_true = np.array([1, 0, 1, 0, 1, 0, 0, 1])
        y_prob = np.array([0.9, 0.1, 0.8, 0.2, 0.7, 0.3, 0.1, 0.85])
        ece = calculate_ece(y_true, y_prob, num_bins=5)
        assert 0.0 <= ece <= 1.0

    def test_compute_psi_identical_vs_drifted(self) -> None:
        """Verify PSI is near zero for identical distributions and elevated for shifted distributions."""
        rng = np.random.default_rng(42)
        dist_a = rng.normal(loc=0.0, scale=1.0, size=1000)
        dist_b = rng.normal(loc=0.0, scale=1.0, size=1000)
        dist_shifted = rng.normal(loc=2.5, scale=1.5, size=1000)

        psi_ident = compute_psi(dist_a, dist_b, num_bins=10)
        psi_drift = compute_psi(dist_a, dist_shifted, num_bins=10)

        assert psi_ident < 0.05
        assert psi_drift > 0.30

    def test_calculate_feature_drift_profiles(self) -> None:
        """Verify feature drift profile computation and status assignment."""
        rng = np.random.default_rng(42)
        ref_X = rng.normal(loc=0.0, scale=1.0, size=(500, 3))
        p2_X = rng.normal(loc=0.1, scale=1.0, size=(500, 3))
        p3_X = rng.normal(loc=2.0, scale=1.5, size=(500, 3))

        feature_names = ["feat_0", "feat_1", "feat_2"]
        profiles = calculate_feature_drift(ref_X, p2_X, p3_X, feature_names)

        assert len(profiles) == 3
        for prof in profiles:
            assert isinstance(prof, FeatureDriftProfile)
            assert prof.p1_to_p2_ks_stat >= 0.0
            assert prof.p1_to_p3_ks_stat >= prof.p1_to_p2_ks_stat
            assert prof.drift_status in ("STABLE", "MODERATE_DRIFT", "SEVERE_DRIFT")


class TestClassifierAndPartitionEvaluation:
    """Validates neural risk classifier training and partition evaluation."""

    def test_train_model_and_predict_proba(self) -> None:
        """Verify model training produces valid probabilities in [0.0, 1.0]."""
        cfg = TemporalGeneralizationConfig(
            num_samples_per_period=300,
            num_features=6,
            hidden_dim=32,
            epochs=5,
            random_seed=42,
        )
        p1, _, _ = generate_temporal_stream(cfg)
        model = _train_model(p1["X"], p1["y"], cfg)
        probs = _predict_proba(model, p1["X"])

        assert probs.shape == (300,)
        assert np.all(probs >= 0.0)
        assert np.all(probs <= 1.0)

    def test_evaluate_partition_metrics(self) -> None:
        """Verify evaluation returns well-formed PeriodMetrics."""
        cfg = TemporalGeneralizationConfig(num_samples_per_period=300, num_features=6, epochs=5)
        p1, _, _ = generate_temporal_stream(cfg)
        model = _train_model(p1["X"], p1["y"], cfg)

        metrics = _evaluate_partition(
            model=model,
            X=p1["X"],
            y=p1["y"],
            period_name="Test Period 1",
            protocol=TemporalSplitProtocol.PERIOD_1_PAST,
            t_range=p1["t_range"],
        )

        assert isinstance(metrics, PeriodMetrics)
        assert 0.0 <= metrics.pr_auc <= 1.0
        assert 0.0 <= metrics.roc_auc <= 1.0
        assert 0.0 <= metrics.f1_score <= 1.0
        assert 0.0 <= metrics.brier_score <= 1.0
        assert 0.0 <= metrics.ece <= 1.0

    def test_evaluate_kfold_cv(self) -> None:
        """Verify randomized K-fold cross validation executes and returns mean and std."""
        cfg = TemporalGeneralizationConfig(
            num_samples_per_period=200,
            num_features=4,
            k_folds=3,
            epochs=3,
            random_seed=42,
        )
        p1, p2, _ = generate_temporal_stream(cfg)
        X_pool = np.concatenate([p1["X"], p2["X"]], axis=0)
        y_pool = np.concatenate([p1["y"], p2["y"]], axis=0)

        kf = _evaluate_kfold_cv(X_pool, y_pool, cfg)
        assert isinstance(kf, KFoldMetrics)
        assert kf.num_folds == 3
        assert 0.0 <= kf.pr_auc_mean <= 1.0
        assert kf.pr_auc_std >= 0.0
        assert 0.0 <= kf.roc_auc_mean <= 1.0


class TestTemporalGeneralizationSuite:
    """Validates complete suite execution, degradation quantification, and serialization."""

    def test_run_benchmark_fast_execution_and_serialization(self) -> None:
        """Verify full benchmark runs quickly with compact config and produces valid results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "test_temporal_results.json"
            cfg = TemporalGeneralizationConfig(
                dataset_name="Fast Test Stream",
                num_samples_per_period=300,
                num_features=6,
                base_fraud_rate=0.06,
                drift_intensity=0.35,
                k_folds=2,
                epochs=4,
                random_seed=42,
            )

            suite = run_temporal_generalization_benchmark(cfg, output_dir=out_file)

            assert isinstance(suite, TemporalGeneralizationSuiteResult)
            assert out_file.exists()
            assert out_file.stat().st_size > 500

            # Verify in-period and OOT metrics exist
            assert suite.in_period_p1.pr_auc > 0.0
            assert suite.oot_period_2.pr_auc > 0.0
            assert suite.oot_period_3.pr_auc > 0.0
            assert suite.optimistic_kfold.pr_auc_mean > 0.0

            # Verify degradation metrics
            deg = suite.degradation
            assert isinstance(deg, TemporalDegradationMetrics)
            assert deg.retraining_urgency in ("NONE", "MODERATE", "CRITICAL")
            assert isinstance(deg.retraining_recommended, bool)

    def test_format_temporal_benchmark_markdown(self) -> None:
        """Verify markdown formatter generates valid GFM tables and findings."""
        cfg = TemporalGeneralizationConfig(
            num_samples_per_period=200,
            num_features=4,
            k_folds=2,
            epochs=2,
            random_seed=99,
        )
        suite = run_temporal_generalization_benchmark(cfg)
        md = format_temporal_benchmark_markdown(suite)

        assert "### Chronological Out-of-Time Degradation vs Optimistic K-Fold Benchmark" in md
        assert "### Feature Drift & Population Stability Index (PSI) Summary" in md
        assert "Optimistic Evaluation Bias" in md
        assert "Period 1: In-Period Test (Past)" in md
        assert "Period 3: Distant OOT (Future)" in md


class TestPydanticSchemaRoundTrip:
    """Validates Pydantic v2 schema JSON serialization and deserialization."""

    def test_suite_result_json_roundtrip(self) -> None:
        """Verify TemporalGeneralizationSuiteResult roundtrips losslessly through JSON."""
        cfg = TemporalGeneralizationConfig(
            num_samples_per_period=200,
            num_features=4,
            k_folds=2,
            epochs=2,
            random_seed=101,
        )
        suite = run_temporal_generalization_benchmark(cfg)

        json_str = suite.model_dump_json()
        recovered = TemporalGeneralizationSuiteResult.model_validate_json(json_str)

        assert recovered.config.dataset_name == suite.config.dataset_name
        assert recovered.in_period_p1.pr_auc == suite.in_period_p1.pr_auc
        assert recovered.oot_period_3.pr_auc == suite.oot_period_3.pr_auc
        assert recovered.degradation.p1_to_p3_pr_auc_delta == suite.degradation.p1_to_p3_pr_auc_delta
        assert len(recovered.feature_drift_profiles) == len(suite.feature_drift_profiles)

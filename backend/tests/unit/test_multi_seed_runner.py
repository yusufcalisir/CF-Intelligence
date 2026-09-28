"""Unit tests for the Multi-Seed Benchmark Protocol & Statistical Confidence Interval Engine.

Validates:
  1. Statistical calculation correctness (Mean, Bessel-corrected std, SEM, Student-t 95% CIs).
  2. Edge cases (single seed, zero variance, empty arrays).
  3. Multi-seed execution across Federated Learning strategies, fraud detection, and harness models.
  4. Pydantic v2 serialization roundtrip and golden artifact integrity.
  5. KaTeX compliance in generated Markdown reports.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from experiments.harness.multi_seed_runner import (
    CANONICAL_SEEDS,
    MultiSeedBenchmarkRunner,
    MultiSeedReportManifest,
    StatisticalMetric,
    compute_statistical_metric,
    get_t_critical_value,
)


class TestStatisticalCalculationEngine:
    """Verifies mathematical correctness of sample statistics and Student-t intervals."""

    def test_known_distribution_statistics(self):
        # Known sample: [10.0, 12.0, 14.0, 16.0, 18.0]
        # Mean = 14.0, N = 5, df = 4
        # Sum of squared deviations = 16 + 4 + 0 + 4 + 16 = 40
        # Sample variance = 40 / 4 = 10.0 -> std = sqrt(10) ≈ 3.162277
        # SEM = 3.162277 / sqrt(5) = sqrt(2) ≈ 1.414214
        # t_0.975, df=4 = 2.776445...
        # Margin = 2.776445 * 1.414214 ≈ 3.9265
        # CI_95 = [14.0 - 3.9265, 14.0 + 3.9265] = [10.0735, 17.9265]
        values = [10.0, 12.0, 14.0, 16.0, 18.0]
        res = compute_statistical_metric(values, precision=4, confidence=0.95)

        assert math.isclose(res.mean, 14.0, rel_tol=1e-4)
        assert math.isclose(res.std, 3.1623, rel_tol=1e-3)
        assert math.isclose(res.median, 14.0, rel_tol=1e-4)
        assert res.min == 10.0
        assert res.max == 18.0
        assert math.isclose(res.ci_95_lower, 10.0735, abs_tol=0.01)
        assert math.isclose(res.ci_95_upper, 17.9265, abs_tol=0.01)
        assert res.formatted_mu_sigma == "14.0000 +/- 3.1623"
        assert res.formatted_ci_95.startswith("[10.07")

    def test_single_seed_behavior_no_divide_by_zero(self):
        values = [0.8500]
        res = compute_statistical_metric(values, precision=4)

        assert res.mean == 0.8500
        assert res.std == 0.0
        assert res.sem == 0.0
        assert res.ci_95_lower == 0.8500
        assert res.ci_95_upper == 0.8500
        assert res.formatted_mu_sigma == "0.8500 +/- 0.0000"

    def test_empty_values_behavior(self):
        res = compute_statistical_metric([], precision=4)
        assert res.mean == 0.0
        assert res.std == 0.0
        assert res.formatted_mu_sigma == "0.0000 +/- 0.0000"
        assert res.formatted_ci_95 == "[0.0000, 0.0000]"

    def test_confidence_interval_monotonicity(self):
        values = [0.72, 0.75, 0.78, 0.81, 0.74]
        res = compute_statistical_metric(values, precision=4)

        assert res.ci_95_lower <= res.min or res.ci_95_lower <= res.mean
        assert res.ci_95_upper >= res.max or res.ci_95_upper >= res.mean
        assert res.ci_95_lower <= res.mean <= res.ci_95_upper

    def test_student_t_critical_values_lookup(self):
        # Test exact Student-t lookup for df=4 (N=5)
        t_crit = get_t_critical_value(df=4, confidence=0.95)
        assert math.isclose(t_crit, 2.776, abs_tol=0.005)

        # Test df=1
        t_crit_1 = get_t_critical_value(df=1, confidence=0.95)
        assert math.isclose(t_crit_1, 12.706, abs_tol=0.01)

        # Test large df fallback
        t_crit_large = get_t_critical_value(df=100, confidence=0.95)
        assert 1.95 <= t_crit_large <= 2.05


class TestMultiSeedRunnerExecution:
    """Verifies end-to-end execution across multiple seeds for all benchmark domains."""

    def test_canonical_seeds_specification(self):
        assert len(CANONICAL_SEEDS) == 5
        assert CANONICAL_SEEDS == [42, 123, 456, 789, 1024]

    def test_run_multi_seed_fl_strategies(self):
        runner = MultiSeedBenchmarkRunner(seeds=[42, 123], save_artifact=False)
        suite_results = runner.run_multi_seed_fl_strategies(n_clients=3, rounds=2)

        assert len(suite_results) == 3  # FedAvg, FedProx, SCAFFOLD
        strategies = {s.model_or_strategy for s in suite_results}
        assert strategies == {"FEDAVG", "FEDPROX", "SCAFFOLD"}

        for s in suite_results:
            assert s.num_seeds == 2
            assert "final_pr_auc" in s.metrics
            assert "final_roc_auc" in s.metrics
            pr_stats = s.metrics["final_pr_auc"]
            assert pr_stats.mean >= 0.0
            assert pr_stats.ci_95_lower <= pr_stats.ci_95_upper

    def test_run_multi_seed_fraud_detection(self):
        runner = MultiSeedBenchmarkRunner(seeds=[42, 123], save_artifact=False)
        suite_results = runner.run_multi_seed_fraud_detection(dataset_name="paysim", rounds=2)

        assert len(suite_results) == 2  # Centralized Baseline, Federated FedAvg
        names = {s.model_or_strategy for s in suite_results}
        assert names == {"Centralized Baseline", "Federated FedAvg"}

        for s in suite_results:
            assert "pr_auc" in s.metrics
            assert "roc_auc" in s.metrics
            assert s.metrics["roc_auc"].mean > 0.50

    def test_run_multi_seed_harness_experiments(self):
        runner = MultiSeedBenchmarkRunner(seeds=[42, 123], save_artifact=False)
        suite_results = runner.run_multi_seed_harness_experiments(rounds=2)

        assert len(suite_results) == 1
        res = suite_results[0]
        assert "pr_auc" in res.metrics
        assert "roc_auc" in res.metrics
        assert "brier_score" in res.metrics
        assert res.metrics["roc_auc"].mean > 0.50


class TestMultiSeedArtifactSerializationAndIntegrity:
    """Verifies JSON schema validity and golden artifact persistence."""

    def test_save_artifact_false_does_not_modify_golden(self):
        golden_path = Path(__file__).resolve().parents[3] / "benchmarks" / "results" / "raw" / "multi_seed_statistical_summary.json"
        mtime_before = golden_path.stat().st_mtime if golden_path.exists() else None

        runner = MultiSeedBenchmarkRunner(seeds=[42], save_artifact=False)
        runner.run_all(fl_rounds=1, fraud_rounds=1, harness_rounds=1)

        if mtime_before is not None:
            mtime_after = golden_path.stat().st_mtime
            assert mtime_before == mtime_after

    def test_custom_destination_path_saves_valid_json(self, tmp_path):
        custom_out = tmp_path / "custom_summary.json"
        runner = MultiSeedBenchmarkRunner(
            seeds=[42, 123],
            output_file=custom_out,
            save_artifact=True,
        )
        manifest = runner.run_all(fl_rounds=1, fraud_rounds=1, harness_rounds=1)

        assert custom_out.exists()
        with open(custom_out, encoding="utf-8") as f:
            data = json.load(f)

        assert data["protocol_version"] == "Phase-33-MultiSeed-v1.0"
        assert len(data["seeds"]) == 2
        assert "federated_learning" in data["suites"]
        assert "fraud_detection" in data["suites"]
        assert "neural_architectures" in data["suites"]

    def test_calibrated_golden_artifact_content(self):
        golden_path = Path(__file__).resolve().parents[3] / "benchmarks" / "results" / "raw" / "multi_seed_statistical_summary.json"
        assert golden_path.exists()

        with open(golden_path, encoding="utf-8") as f:
            data = json.load(f)

        manifest = MultiSeedReportManifest.model_validate(data)
        assert len(manifest.seeds) == 5
        assert manifest.seeds == CANONICAL_SEEDS
        assert "hardware" in data
        assert data["hardware"]["cpu_model"] != ""

        # Validate that Centralized Baseline ROC-AUC exceeds 0.85
        fraud_suites = manifest.suites["fraud_detection"]
        central = next(s for s in fraud_suites if s.model_or_strategy == "Centralized Baseline")
        assert central.metrics["roc_auc"].mean > 0.85


class TestMarkdownReportKaTeXIntegrity:
    """Verifies that generated Markdown reports adhere to KaTeX formatting standards."""

    def test_markdown_report_formatting(self):
        runner = MultiSeedBenchmarkRunner(seeds=[42, 123], save_artifact=False)
        manifest = runner.run_all(fl_rounds=1, fraud_rounds=1, harness_rounds=1)
        md = runner.generate_markdown_report_section(manifest)

        # Check section header
        assert "### 26.1 Multi-Seed Statistical Protocol Specifications" in md
        assert "### 26.2 Multi-Seed Benchmark Statistical Matrix" in md
        assert "### 26.3 Statistical Robustness Observations & Analysis" in md

        # Check display math isolation (must have surrounding blank lines)
        assert "\n\n$$\\mu = " in md

        # Check table formatting
        assert "| Benchmark Suite | Paradigm / Strategy | Metric Dimension |" in md
        assert "+/-" in md

        # Check math delimiters are balanced
        dollar_count = md.count("$")
        assert dollar_count % 2 == 0, f"Unbalanced '$' math delimiters (count: {dollar_count})"

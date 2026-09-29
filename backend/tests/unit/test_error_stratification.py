"""Unit tests for systematic error analysis, stratification, and failure mode dossier."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
from experiments.error_analysis.stratify_errors import (
    ErrorStratifier,
    run_error_stratification_analysis,
)


class TestStratumMetricCalculations:
    """Verifies precision, recall, FPR, FNR, and cost loss mathematical integrity."""

    def test_known_confusion_matrix_metrics(self) -> None:
        # TP=4, FP=1, TN=3, FN=2 (Total=10, Positives=6, Negatives=4)
        y_true = np.array([1, 1, 1, 1, 0, 0, 0, 0, 1, 1])
        y_pred = np.array([1, 1, 1, 1, 1, 0, 0, 0, 0, 0])

        metrics = ErrorStratifier.compute_stratum_metrics(
            y_true=y_true,
            y_pred=y_pred,
            dimension="test_dimension",
            bin_label="test_bin",
            cost_fn=850.0,
            cost_fp=25.0,
        )

        assert metrics.total_samples == 10
        assert metrics.positive_samples == 6
        assert metrics.negative_samples == 4
        assert metrics.base_rate == pytest.approx(0.60)
        assert metrics.true_positives == 4
        assert metrics.false_positives == 1
        assert metrics.true_negatives == 3
        assert metrics.false_negatives == 2

        # Precision: 4 / (4 + 1) = 0.8
        assert metrics.precision == pytest.approx(0.80)
        # Recall: 4 / (4 + 2) = 0.6667
        assert metrics.recall == pytest.approx(4 / 6)
        # FPR: 1 / (1 + 3) = 0.25
        assert metrics.fpr == pytest.approx(0.25)
        # FNR: 2 / (2 + 4) = 0.3333
        assert metrics.fnr == pytest.approx(2 / 6)
        # F1: 2 * (0.8 * 4/6) / (0.8 + 4/6) = 0.72727
        expected_f1 = 2 * 0.8 * (4 / 6) / (0.8 + (4 / 6))
        assert metrics.f1_score == pytest.approx(expected_f1)
        # Cost Loss: 2 * 850 + 1 * 25 = 1725.0
        assert metrics.cost_weighted_loss == pytest.approx(1725.0)
        assert metrics.dominant_error_type == "FN_DOMINANT"

    def test_empty_stratum_no_division_by_zero(self) -> None:
        y_true = np.array([], dtype=int)
        y_pred = np.array([], dtype=int)

        metrics = ErrorStratifier.compute_stratum_metrics(
            y_true=y_true,
            y_pred=y_pred,
            dimension="empty",
            bin_label="empty_bin",
        )

        assert metrics.total_samples == 0
        assert metrics.precision == 0.0
        assert metrics.recall == 0.0
        assert metrics.fpr == 0.0
        assert metrics.fnr == 0.0
        assert metrics.f1_score == 0.0
        assert metrics.cost_weighted_loss == 0.0
        assert metrics.dominant_error_type == "BALANCED"

    def test_dominant_error_type_fp_and_balanced(self) -> None:
        # FP dominant: FP=3, FN=1
        y_true_fp = np.array([1, 0, 0, 0, 1])
        y_pred_fp = np.array([1, 1, 1, 1, 0])
        m_fp = ErrorStratifier.compute_stratum_metrics(y_true_fp, y_pred_fp, "dim", "bin")
        assert m_fp.dominant_error_type == "FP_DOMINANT"

        # Balanced: FP=1, FN=1
        y_true_bal = np.array([1, 0, 1])
        y_pred_bal = np.array([1, 1, 0])
        m_bal = ErrorStratifier.compute_stratum_metrics(y_true_bal, y_pred_bal, "dim", "bin")
        assert m_bal.dominant_error_type == "BALANCED"


class TestStratificationDimensions:
    """Verifies partitioning logic across Amount, Hour, MCC, and Network Degree."""

    def test_amount_stratification_covers_all_ranges(self) -> None:
        amounts = np.array([10.0, 150.0, 500.0, 5000.0, 9500.0, 25000.0])
        y_true = np.ones(6, dtype=int)
        y_pred = np.ones(6, dtype=int)

        strata = ErrorStratifier.stratify_by_amount(amounts, y_true, y_pred)
        assert len(strata) == 6
        labels = [s.bin_label for s in strata]
        assert "Micro (<$50)" in labels[0]
        assert "Low ($50-$250)" in labels[1]
        assert "Medium ($250-$1,000)" in labels[2]
        assert "High ($1,000-$9,000)" in labels[3]
        assert "Near-Threshold Structuring ($9,000-$10,000)" in labels[4]
        assert "Large / Jumbo (>$10,000)" in labels[5]

        # Each bin contains exactly 1 sample
        for s in strata:
            assert s.total_samples == 1

    def test_hour_stratification_quadrants(self) -> None:
        hours = np.array([2, 8, 14, 21])
        y_true = np.ones(4, dtype=int)
        y_pred = np.ones(4, dtype=int)

        strata = ErrorStratifier.stratify_by_hour(hours, y_true, y_pred)
        assert len(strata) == 4
        labels = [s.bin_label for s in strata]
        assert "Late Night" in labels[0]
        assert "Morning Peak" in labels[1]
        assert "Afternoon Business" in labels[2]
        assert "Evening Leisure" in labels[3]

        for s in strata:
            assert s.total_samples == 1

    def test_mcc_grouping_and_uncategorized_fallback(self) -> None:
        mccs = ["6011", "6012", "5411", "5812", "7995", "5999", "9999"]
        y_true = np.ones(7, dtype=int)
        y_pred = np.ones(7, dtype=int)

        strata = ErrorStratifier.stratify_by_mcc(mccs, y_true, y_pred)
        assert len(strata) == 7
        labels = [s.bin_label for s in strata]
        assert any("ATM" in lbl for lbl in labels)
        assert any("Quasi-Cash" in lbl for lbl in labels)
        assert any("Retail / Grocery" in lbl for lbl in labels)
        assert any("Restaurants" in lbl for lbl in labels)
        assert any("High-Risk" in lbl for lbl in labels)
        assert any("Specialty" in lbl for lbl in labels)
        assert any("Uncategorized" in lbl for lbl in labels)

        # 9999 must fall into Uncategorized / Other
        uncat = next(s for s in strata if "Uncategorized" in s.bin_label)
        assert uncat.total_samples == 1

    def test_graph_degree_computation_and_stratification(self) -> None:
        src = ["acc_1", "acc_1", "acc_2", "acc_3"]
        dst = ["acc_2", "acc_4", "acc_3", "acc_4"]
        # acc_1 degree: 2, acc_2 degree: 2, acc_3 degree: 2, acc_4 degree: 2
        degrees = ErrorStratifier.compute_graph_degrees(src, dst)
        assert len(degrees) == 4
        assert degrees[0] == 2
        assert degrees[1] == 2

        # Test degree strata
        test_degrees = np.array([1, 3, 10, 25, 75])
        y_true = np.ones(5, dtype=int)
        y_pred = np.ones(5, dtype=int)
        strata = ErrorStratifier.stratify_by_network_degree(test_degrees, y_true, y_pred)
        assert len(strata) == 5
        labels = [s.bin_label for s in strata]
        assert "Isolated / Peripheral (k=1)" in labels[0]
        assert "Low Connectivity (k=2-4)" in labels[1]
        assert "Moderate Connectivity (k=5-15)" in labels[2]
        assert "High Connectivity Hub (k=16-50)" in labels[3]
        assert "Super-Hub / Aggregator (k>50)" in labels[4]
        for s in strata:
            assert s.total_samples == 1


class TestFailureModeDossierAndAnalysisPipeline:
    """Verifies failure mode dossier compilation and end-to-end analysis pipeline."""

    def test_failure_mode_dossier_contains_canonical_modes(self) -> None:
        analysis = run_error_stratification_analysis(
            sample_size=1000,
            seed=42,
            save_artifact=False,
        )
        assert len(analysis.failure_mode_dossier) == 4
        mode_ids = [fm.mode_id for fm in analysis.failure_mode_dossier]
        assert "FM-01" in mode_ids
        assert "FM-02" in mode_ids
        assert "FM-03" in mode_ids
        assert "FM-04" in mode_ids

        fm1 = next(fm for fm in analysis.failure_mode_dossier if fm.mode_id == "FM-01")
        assert "Smurfing" in fm1.name
        assert fm1.primary_error_type == "FALSE_NEGATIVE"
        assert len(fm1.remediation_strategy) > 20

        fm2 = next(fm for fm in analysis.failure_mode_dossier if fm.mode_id == "FM-02")
        assert "Batch Clearing" in fm2.name
        assert fm2.primary_error_type == "FALSE_POSITIVE"

    def test_overall_metrics_consistency(self) -> None:
        analysis = run_error_stratification_analysis(
            sample_size=2000,
            seed=123,
            save_artifact=False,
        )
        assert analysis.sample_size == 2000
        ov = analysis.overall_metrics
        assert ov["total_tp"] + ov["total_fp"] + ov["total_tn"] + ov["total_fn"] == 2000
        assert 0.0 <= ov["precision"] <= 1.0
        assert 0.0 <= ov["recall"] <= 1.0
        assert 0.0 <= ov["f1_score"] <= 1.0

    def test_markdown_report_formatting(self) -> None:
        analysis = run_error_stratification_analysis(
            sample_size=1000,
            seed=42,
            save_artifact=False,
        )
        md = analysis.to_markdown()
        assert "# Systematic Error Stratification" in md
        assert "### 1.1 Transaction Amount Stratification" in md
        assert "### 1.2 Diurnal Temporal Stratification" in md
        assert "### 1.3 Merchant Category Code" in md
        assert "### 1.4 Network Degree Stratification" in md
        assert "FM-01:" in md
        assert "FM-02:" in md
        assert "FM-03:" in md
        assert "FM-04:" in md


class TestArtifactSerializationAndIntegrity:
    """Verifies JSON artifact generation, loading, and schema adherence."""

    def test_custom_output_path_saves_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "test_error_analysis.json"
            run_error_stratification_analysis(
                sample_size=500,
                seed=42,
                output_path=out_file,
                save_artifact=True,
            )
            assert out_file.exists()
            with open(out_file, encoding="utf-8") as f:
                data = json.load(f)
            assert data["sample_size"] == 500
            assert "amount_stratification" in data
            assert "hour_stratification" in data
            assert "mcc_stratification" in data
            assert "degree_stratification" in data
            assert "failure_mode_dossier" in data

    def test_golden_artifact_content_and_schema(self) -> None:
        golden_path = (
            Path(__file__).resolve().parents[3]
            / "benchmarks"
            / "results"
            / "raw"
            / "error_stratification_analysis.json"
        )
        assert golden_path.exists(), f"Golden artifact missing at {golden_path}"
        with open(golden_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["sample_size"] == 10000
        assert len(data["amount_stratification"]) == 6
        assert len(data["hour_stratification"]) == 4
        assert len(data["mcc_stratification"]) == 7
        assert len(data["degree_stratification"]) == 5
        assert len(data["failure_mode_dossier"]) == 4

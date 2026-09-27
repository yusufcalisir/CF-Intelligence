"""Unit and integration tests for Cross-Bank Consortium Value & Information Gain Quantification.

Validates Shannon Entropy, Conditional Entropy, Mutual Information, Partial Horizon Isolation,
Financial Value at Risk (VaR) Aversion, Communication Bandwidth Models, and Report Generation.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from experiments.cross_bank import (
    CommunicationCostModel,
    ConsortiumValueQuantifier,
    CrossBankNetworkGenerator,
    InformationHorizonAnalyzer,
    run_consortium_value_quantification,
)


class TestInformationHorizonAnalyzer:
    """Test suite for Shannon entropy, conditional entropy, and mutual information."""

    def test_shannon_entropy_properties(self) -> None:
        # 1. Single-class deterministic distribution -> H(Y) = 0.0
        all_zeros = np.zeros(100, dtype=int)
        assert InformationHorizonAnalyzer.compute_entropy(all_zeros) == 0.0

        all_ones = np.ones(50, dtype=int)
        assert InformationHorizonAnalyzer.compute_entropy(all_ones) == 0.0

        # 2. Perfectly balanced binary distribution -> H(Y) = 1.0 bit
        balanced = np.array([0, 1] * 50, dtype=int)
        h_balanced = InformationHorizonAnalyzer.compute_entropy(balanced)
        assert pytest.approx(h_balanced, abs=1e-3) == 1.0

        # 3. Empty distribution -> H(Y) = 0.0
        assert InformationHorizonAnalyzer.compute_entropy([]) == 0.0

    def test_conditional_entropy_and_mutual_information(self) -> None:
        # Perfect correlation between feature and label
        labels = np.array([0] * 50 + [1] * 50, dtype=int)
        features_perfect = np.array([10.0] * 50 + [5000.0] * 50, dtype=float)

        h_y = InformationHorizonAnalyzer.compute_entropy(labels)
        h_y_given_x = InformationHorizonAnalyzer.compute_conditional_entropy(features_perfect, labels, n_bins=5)
        mi = InformationHorizonAnalyzer.compute_mutual_information(features_perfect, labels)

        # Perfect information implies H(Y | X) = 0 and I(X; Y) = H(Y)
        assert h_y_given_x < 0.1
        assert pytest.approx(mi, abs=0.1) == h_y

        # Non-informative constant feature -> H(Y | X) = H(Y), I(X; Y) = 0
        features_constant = np.ones(100, dtype=float)
        mi_const = InformationHorizonAnalyzer.compute_mutual_information(features_constant, labels)
        assert mi_const == 0.0

    def test_analyze_horizon_isolation_structure(self) -> None:
        generator = CrossBankNetworkGenerator(seed=42)
        df = generator.generate_benchmark_dataset(n_total_transactions=500, timesteps=48)

        horizons = InformationHorizonAnalyzer.analyze_horizon_isolation(df, generator)

        assert "global_consortium" in horizons
        assert horizons["global_consortium"]["observation_coverage"] == 1.0
        assert horizons["global_consortium"]["total_transactions"] == len(df)

        for b_id in ["bank_a", "bank_b", "bank_c"]:
            assert b_id in horizons
            b_cov = horizons[b_id]["observation_coverage"]
            assert 0.10 <= b_cov < 1.0, f"Expected partial coverage for {b_id}, got {b_cov}"
            assert horizons[b_id]["entropy_h_y"] >= 0.0
            assert horizons[b_id]["mutual_information_bits"] >= 0.0


class TestConsortiumValueQuantifier:
    """Test suite for financial impact, illicit volume aversion, and scenario aggregation."""

    def test_financial_quantification_calculation(self) -> None:
        # Mock test dataframe
        mock_data = []
        for i in range(100):
            mock_data.append({
                "transaction_id": f"tx_{i}",
                "amount": 1000.0,
                "scenario_id": "SCENARIO_3" if i < 30 else ("SCENARIO_7" if i < 60 else "SCENARIO_1"),
                "is_laundering": 1,
            })
        test_df = pd.DataFrame(mock_data)

        scenario_breakdown = {
            "SCENARIO_1": {"isolated_detection_rate": 1.0, "federated_detection_rate": 1.0},
            "SCENARIO_3": {"isolated_detection_rate": 0.50, "federated_detection_rate": 1.0},
            "SCENARIO_7": {"isolated_detection_rate": 0.0, "federated_detection_rate": 1.0},
        }

        result = ConsortiumValueQuantifier.quantify_financial_impact(test_df, scenario_breakdown)

        agg = result["aggregate"]
        assert agg["total_attempted_laundering_volume_usd"] == 100000.0
        # Sc 1: 40k * 1.0 = 40k iso, 40k fed
        # Sc 3: 30k * 0.5 = 15k iso, 30k fed (15k incremental)
        # Sc 7: 30k * 0.0 = 0k iso, 30k fed (30k incremental)
        # Total iso = 55k, total fed = 100k, incremental = 45k
        assert agg["isolated_total_detected_volume_usd"] == 55000.0
        assert agg["federated_total_detected_volume_usd"] == 100000.0
        assert agg["total_incremental_averted_volume_usd"] == 45000.0
        assert agg["consortium_prevention_rate_uplift_pct"] == 45.0

        sc_res = result["scenarios"]
        assert sc_res["SCENARIO_3"]["averted_illicit_volume_usd"] == 15000.0
        assert sc_res["SCENARIO_7"]["averted_illicit_volume_usd"] == 30000.0
        assert sc_res["SCENARIO_1"]["averted_illicit_volume_usd"] == 0.0


class TestCommunicationCostModel:
    """Test suite for communication overhead and bandwidth return on investment."""

    def test_transmission_overhead_relative_ranking(self) -> None:
        modes = CommunicationCostModel.calculate_transmission_overhead(rounds=5, n_clients=3)

        assert "uncompressed_fp32" in modes
        assert "quantized_fp16" in modes
        assert "topk_sparsified_90" in modes
        assert "pqc_secagg" in modes
        assert "ckks_homomorphic" in modes

        mb_sparse = modes["topk_sparsified_90"]["total_megabytes"]
        mb_fp16 = modes["quantized_fp16"]["total_megabytes"]
        mb_fp32 = modes["uncompressed_fp32"]["total_megabytes"]
        mb_secagg = modes["pqc_secagg"]["total_megabytes"]
        mb_ckks = modes["ckks_homomorphic"]["total_megabytes"]

        # Strict ordering invariant
        assert mb_sparse < mb_fp16 < mb_fp32 <= mb_secagg < mb_ckks

    def test_bandwidth_roi_calculation(self) -> None:
        modes = CommunicationCostModel.calculate_transmission_overhead(rounds=5, n_clients=3)
        averted_usd = 500000.0

        roi = CommunicationCostModel.compute_bandwidth_roi(averted_usd, modes)

        assert roi["topk_sparsified_90"] > roi["uncompressed_fp32"]
        assert roi["uncompressed_fp32"] > roi["ckks_homomorphic"]
        assert all(val > 0.0 for val in roi.values())


class TestEndToEndInformationGainWorkflow:
    """Test full end-to-end execution of consortium value quantification."""

    def test_run_consortium_value_quantification_small_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = run_consortium_value_quantification(
                n_transactions=500,
                seed=42,
                output_dir=tmp_dir,
            )

            assert result["benchmark_id"] == "CFI-CrossBank-01-Value-Quantification"
            assert "information_horizons" in result
            assert "financial_quantification" in result
            assert "communication_costs" in result
            assert "bandwidth_roi_usd_per_mb" in result

            # Verify files on disk
            tmp_path = Path(tmp_dir)
            json_file = tmp_path / "information_gain.json"
            report_file = tmp_path / "report.md"
            plot_file = tmp_path / "plots" / "benchmark_communication.png"

            assert json_file.exists() and json_file.stat().st_size > 0
            assert report_file.exists() and report_file.stat().st_size > 0
            assert plot_file.exists() and plot_file.stat().st_size > 0

            # Verify report contains key sections
            report_text = report_file.read_text(encoding="utf-8")
            assert "## 1. Information-Theoretic Horizon Formalization" in report_text
            assert "## 2. Empirical Value at Risk (VaR)" in report_text
            assert "## 3. Communication Cost vs Value Return" in report_text

            # Verify structured JSON
            loaded = json.loads(json_file.read_text(encoding="utf-8"))
            assert loaded["n_transactions"] == 500

"""Unit test suite for the CFI-CrossBank-01 Flagship Consortium Research Benchmark.

Validates the multi-bank cross-institutional fraud network experiment orchestrator,
including 3-bank consortium nodes, 7 canonical fraud topologies, isolated silo vs.
federated consensus evaluation, cold-start transfer rescue, and artifact persistence.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch
from experiments.cross_bank.run_flagship_experiment import (
    DEFAULT_CONSORTIUM_NODES,
    FEATURE_COLUMNS,
    SCENARIO_DEFINITIONS,
    FlagshipConsortiumExperiment,
    FlagshipMLPClassifier,
    calculate_recall_at_fpr,
)


class TestFlagshipCrossBankExperiment:
    """Comprehensive test suite for the Flagship Consortium Cross-Bank Research Benchmark."""

    def test_flagship_experiment_initialization(self, tmp_path: Path) -> None:
        """Verify initialization of the experiment orchestrator in standard and quick modes."""
        exp_standard = FlagshipConsortiumExperiment(
            n_transactions=5000,
            rounds=3,
            local_epochs=2,
            seed=123,
            output_dir=tmp_path / "standard",
            generate_plots=False,
            quick_mode=False,
        )
        assert exp_standard.n_transactions == 5000
        assert exp_standard.rounds == 3
        assert exp_standard.local_epochs == 2
        assert exp_standard.seed == 123
        assert not exp_standard.generate_plots
        assert (tmp_path / "standard").exists()

        exp_quick = FlagshipConsortiumExperiment(
            output_dir=tmp_path / "quick",
            generate_plots=False,
            quick_mode=True,
        )
        assert exp_quick.quick_mode is True
        assert exp_quick.n_transactions == 2000
        assert exp_quick.rounds == 2
        assert exp_quick.local_epochs == 3

    def test_default_consortium_nodes(self) -> None:
        """Verify the 3 default consortium bank institutions and their operational attributes."""
        assert len(DEFAULT_CONSORTIUM_NODES) == 3
        bank_ids = [node.bank_id for node in DEFAULT_CONSORTIUM_NODES]
        assert "bank_a" in bank_ids
        assert "bank_b" in bank_ids
        assert "bank_c" in bank_ids

        total_volume = sum(node.volume_share for node in DEFAULT_CONSORTIUM_NODES)
        assert pytest.approx(total_volume, rel=1e-5) == 1.0

        for node in DEFAULT_CONSORTIUM_NODES:
            assert len(node.name) > 0
            assert node.account_count > 0
            assert node.positive_prevalence >= 0.0

    def test_scenario_definitions_coverage(self) -> None:
        """Verify all 7 canonical cross-bank fraud scenarios are defined."""
        assert len(SCENARIO_DEFINITIONS) == 7
        expected_scenarios = [f"SCENARIO_{i}" for i in range(1, 8)]
        for sc in expected_scenarios:
            assert sc in SCENARIO_DEFINITIONS
            item = SCENARIO_DEFINITIONS[sc]
            assert len(item.title) > 0
            assert len(item.description) > 0
            assert len(item.risk_typology) > 0
            assert len(item.participating_banks) > 0

    def test_flagship_mlp_forward_and_predict_proba(self) -> None:
        """Verify FlagshipMLPClassifier forward logits and probabilistic outputs."""
        model = FlagshipMLPClassifier(input_dim=len(FEATURE_COLUMNS))
        x_dummy = np.random.randn(8, len(FEATURE_COLUMNS)).astype(np.float32)

        # Forward logits
        logits = model(torch.tensor(x_dummy))
        assert logits.shape == (8, 1)

        # Probabilities
        probs = model.predict_proba(x_dummy)
        assert probs.shape == (8,)
        assert np.all(probs >= 0.0) and np.all(probs <= 1.0)

    def test_calculate_recall_at_fpr(self) -> None:
        """Verify statutory Recall@FPR calculation across positive and negative splits."""
        y_true = np.array([0, 0, 0, 0, 0, 1, 1, 1, 1, 1])
        y_scores = np.array([0.1, 0.2, 0.15, 0.05, 0.3, 0.8, 0.85, 0.9, 0.75, 0.95])

        recall = calculate_recall_at_fpr(y_true, y_scores, target_fpr=0.20)
        assert recall > 0.0

        # Edge cases: no positives
        zero_pos = calculate_recall_at_fpr(np.zeros(10), y_scores)
        assert zero_pos == 0.0

        # Edge cases: empty negatives
        empty_neg = calculate_recall_at_fpr(np.ones(10), y_scores)
        assert empty_neg == 0.0

    def test_isolated_vs_federated_evaluation(self, tmp_path: Path) -> None:
        """Verify isolated vs. federated collaborative consensus evaluation on benchmark dataset."""
        exp = FlagshipConsortiumExperiment(
            n_transactions=2000,
            rounds=2,
            local_epochs=3,
            seed=42,
            output_dir=tmp_path / "exp_eval",
            generate_plots=False,
            quick_mode=True,
        )
        results = exp.run()

        assert "overall_isolated_detection_rate" in results
        assert "overall_federated_detection_rate" in results
        assert "collaborative_uplift" in results
        assert "overall_delta_detection_rate" in results
        assert "scenarios" in results

        fed_rate = results["overall_federated_detection_rate"]
        iso_rate = results["overall_isolated_detection_rate"]
        assert fed_rate >= iso_rate

    def test_scenario_3_cyclic_ring_collaborative_uplift(self, tmp_path: Path) -> None:
        """Verify Scenario 3 (Cyclic Ring Transfer) demonstrates high collaborative detection."""
        exp = FlagshipConsortiumExperiment(
            n_transactions=2000,
            rounds=2,
            local_epochs=3,
            seed=42,
            output_dir=tmp_path / "exp_sc3",
            generate_plots=False,
            quick_mode=True,
        )
        results = exp.run()
        sc3 = results["scenarios"]["SCENARIO_3"]

        assert sc3["federated_detection_rate"] >= 0.90
        assert sc3["delta_detection_rate"] >= 0.0

    def test_scenario_7_cold_start_transfer_rescue(self, tmp_path: Path) -> None:
        """Verify Scenario 7 (Cold-Start Bank Transfer) rescues Bank Gamma from 0% isolated detection."""
        exp = FlagshipConsortiumExperiment(
            n_transactions=2000,
            rounds=2,
            local_epochs=3,
            seed=42,
            output_dir=tmp_path / "exp_sc7",
            generate_plots=False,
            quick_mode=True,
        )
        results = exp.run()
        sc7 = results["scenarios"]["SCENARIO_7"]

        # Isolated Bank Gamma has 0% detection due to zero historical positive cases
        assert sc7["isolated_detection_rate"] == 0.0
        # Federated consensus rescues Bank Gamma to >= 90% detection
        assert sc7["federated_detection_rate"] >= 0.90
        assert sc7["delta_detection_rate"] >= 0.90

    def test_financial_var_and_communication_overhead(self, tmp_path: Path) -> None:
        """Verify financial VaR impact quantification and 5-protocol communication profiles."""
        exp = FlagshipConsortiumExperiment(
            n_transactions=2000,
            rounds=2,
            local_epochs=3,
            seed=42,
            output_dir=tmp_path / "exp_var",
            generate_plots=False,
            quick_mode=True,
        )
        results = exp.run()

        financial = results["financial_impact"]
        assert financial["total_incremental_averted_volume_usd"] > 0
        assert financial["consortium_prevention_rate_uplift_pct"] > 0

        comm = results["communication_overhead"]
        assert "uncompressed_fp32" in comm
        assert "quantized_fp16" in comm
        assert "topk_sparsified_90" in comm
        assert "pqc_secagg" in comm
        assert "ckks_homomorphic" in comm

    def test_artifact_generation_hierarchy(self, tmp_path: Path) -> None:
        """Verify that all required experiment artifacts are generated with valid structure."""
        out_dir = tmp_path / "exp_artifacts"
        exp = FlagshipConsortiumExperiment(
            n_transactions=2000,
            rounds=2,
            local_epochs=3,
            seed=42,
            output_dir=out_dir,
            generate_plots=False,
            quick_mode=True,
        )
        exp.run()

        config_file = out_dir / "config.json"
        results_file = out_dir / "results.json"
        metrics_file = out_dir / "metrics.csv"
        report_file = out_dir / "report.md"

        assert config_file.exists()
        assert results_file.exists()
        assert metrics_file.exists()
        assert report_file.exists()

        with open(results_file, encoding="utf-8") as f:
            res_data = json.load(f)
        assert res_data["benchmark_id"] == "CFI-CrossBank-01"
        assert res_data["experiment_id"] == "CFI-CrossBank-01"
        assert len(res_data["scenarios"]) == 7

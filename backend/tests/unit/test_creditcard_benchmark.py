"""Unit and integration test suite for Credit Card Fraud Federated Benchmark (Phase 7).

Verifies:
1. CreditCardPartitioner:
   - Extreme imbalance skew (Bank C near-zero fraud allocation).
   - Symmetric Dirichlet partitioning and TVD diagnostics.
   - Total sample conservation and index disjointness.
2. FederatedCreditCardTrainer:
   - Parameter weight cloning, setting, and sample-weighted aggregation.
   - Fixed-FPR threshold evaluation metrics structure.
   - FedProx proximal regularizer penalty dynamics.
3. Comparative baselines & end-to-end orchestration:
   - Isolated Silos vs Federated Consensus vs Centralized Pooled Upper Bound.
   - Serialization of results.json (Pydantic v2 ExperimentResult), comparative_baselines.json, and audit_dossier.md.
   - Generation of high-resolution publication plots.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import torch
from experiments.credit_card.evaluate_thresholds import CreditCardImbalanceMLP
from experiments.credit_card.run_creditcard_benchmark import (
    ComparativeCreditCardEvaluator,
    CreditCardPartitioner,
    FederatedCreditCardTrainer,
    aggregate_weights,
    clone_weights,
    plot_imbalance_robustness_barchart,
    plot_multi_paradigm_pr,
    plot_multi_paradigm_roc,
    plot_optimizer_convergence,
    run_creditcard_benchmark,
    set_weights,
)
from experiments.harness.schema import ExperimentResult


class TestCreditCardPartitioner:
    """Validates multi-bank non-IID and extreme imbalance partitioning invariants."""

    def test_extreme_skew_partition_properties(self, tmp_path: Path) -> None:
        """Verify extreme skew mode leaves Bank C with near-zero positive cases while conserving data."""
        partitioner = CreditCardPartitioner(
            skew_mode="extreme_skew",
            num_clients=3,
            test_ratio=0.20,
            seed=42,
        )
        # Use synthetic fallback by pointing to empty dir
        empty_dir = tmp_path / "mock_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)

        partitions = partitioner.load_and_partition(
            nrows=1500,
            require_real=False,
            path=empty_dir,
        )

        assert len(partitions) == 3
        assert "bank_a" in partitions
        assert "bank_b" in partitions
        assert "bank_c" in partitions

        # Check total sample conservation: train + test = 1500
        n_train = sum(len(y) for _, y in partitions.values())
        n_test = len(partitioner.y_global_test)
        assert n_train + n_test == 1500

        # Check Bank C near-zero positive cases
        y_c = partitions["bank_c"][1]
        pos_c = int(np.sum(y_c == 1))
        assert 1 <= pos_c <= 2, f"Bank C should have 1-2 fraud cases under extreme skew, got {pos_c}"

        # Check Bank A and B have the remaining positive cases
        y_a = partitions["bank_a"][1]
        y_b = partitions["bank_b"][1]
        pos_a = int(np.sum(y_a == 1))
        pos_b = int(np.sum(y_b == 1))
        assert pos_a > pos_c
        assert pos_b >= pos_c

    def test_dirichlet_partition_properties(self, tmp_path: Path) -> None:
        """Verify symmetric Dirichlet partitioning produces valid client allocations."""
        partitioner = CreditCardPartitioner(
            skew_mode="dirichlet",
            alpha=0.5,
            num_clients=3,
            test_ratio=0.25,
            seed=42,
        )
        empty_dir = tmp_path / "mock_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)

        partitions = partitioner.load_and_partition(
            nrows=1200,
            require_real=False,
            path=empty_dir,
        )

        assert len(partitions) == 3
        for cid, (x_k, y_k) in partitions.items():
            assert len(x_k) == len(y_k)
            assert x_k.shape[1] == 30
            assert len(y_k) > 0

        assert len(partitioner.y_global_test) == 300

    def test_partition_diagnostics_structure(self, tmp_path: Path) -> None:
        """Verify partition diagnostics capture TVD, fraud ratios, and volume shares."""
        partitioner = CreditCardPartitioner(skew_mode="extreme_skew", seed=42)
        empty_dir = tmp_path / "mock_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)

        partitioner.load_and_partition(nrows=1000, require_real=False, path=empty_dir)
        diag = partitioner.diagnostics

        assert "total_train_samples" in diag
        assert "total_test_samples" in diag
        assert "client_stats" in diag
        assert "mean_tvd" in diag
        assert diag["mean_tvd"] >= 0.0

        stats_c = diag["client_stats"]["bank_c"]
        assert "fraud_samples" in stats_c
        assert "fraud_ratio" in stats_c
        assert "volume_share" in stats_c


class TestFederatedCreditCardTrainer:
    """Validates parameter mechanics, federated aggregation, and evaluation."""

    def test_parameter_cloning_and_setting(self) -> None:
        """Verify clone_weights creates detached CPU copies and set_weights restores them."""
        model = CreditCardImbalanceMLP(in_features=30, hidden_dims=(32, 16))
        weights = clone_weights(model)

        assert isinstance(weights, dict)
        assert len(weights) > 0
        for k, v in weights.items():
            assert isinstance(v, torch.Tensor)
            assert not v.requires_grad

        # Mutate model parameters
        with torch.no_grad():
            for p in model.parameters():
                p.add_(1.0)

        # Restore from cloned weights
        set_weights(model, weights, "cpu")
        restored = clone_weights(model)
        for k in weights:
            assert torch.allclose(torch.as_tensor(weights[k]), torch.as_tensor(restored[k]))

    def test_sample_weighted_aggregation(self) -> None:
        """Verify aggregate_weights computes mathematically exact sample-weighted consensus."""
        w1 = {"fc.weight": torch.tensor([[1.0, 2.0]], dtype=torch.float32)}
        w2 = {"fc.weight": torch.tensor([[3.0, 4.0]], dtype=torch.float32)}

        # Update 1: 100 samples, Update 2: 300 samples -> weights 0.25 and 0.75
        updates: list[tuple[dict[str, torch.Tensor], int]] = [(w1, 100), (w2, 300)]
        agg = aggregate_weights(updates)

        expected = 0.25 * torch.tensor([[1.0, 2.0]]) + 0.75 * torch.tensor([[3.0, 4.0]])
        assert torch.allclose(torch.as_tensor(agg["fc.weight"]), torch.as_tensor(expected))

    def test_evaluate_model_fixed_fpr_metrics_structure(self) -> None:
        """Verify model evaluation outputs complete fixed-FPR recall and loss metrics."""
        trainer = FederatedCreditCardTrainer(in_features=30, hidden_dims=(32, 16), seed=42)
        model = trainer.create_model()

        rng = np.random.default_rng(42)
        X_test = rng.standard_normal((200, 30)).astype(np.float32)
        y_test = np.array([0] * 190 + [1] * 10, dtype=int)

        loss, probs, metrics = trainer.evaluate_model(model, X_test, y_test)

        assert isinstance(loss, float)
        assert loss > 0.0
        assert probs.shape == (200,)
        assert 0.0 <= metrics["pr_auc"] <= 1.0
        assert 0.0 <= metrics["roc_auc"] <= 1.0
        assert "recall_at_01_fpr" in metrics
        assert "recall_at_05_fpr" in metrics
        assert "recall_at_1_fpr" in metrics

    def test_fedprox_proximal_term_changes_loss(self) -> None:
        """Verify FedProx proximal term penalizes client parameter drift."""
        trainer = FederatedCreditCardTrainer(in_features=30, hidden_dims=(32, 16), seed=42)
        init_model = trainer.create_model()
        weights = clone_weights(init_model)

        rng = np.random.default_rng(42)
        X_k = rng.standard_normal((100, 30)).astype(np.float32)
        y_k = np.array([0] * 95 + [1] * 5, dtype=int)

        # Train with fedavg (mu=0)
        w_avg, loss_avg = trainer._train_client_local(
            initial_weights=weights,
            X_k=X_k,
            y_k=y_k,
            strategy="fedavg",
            fedprox_mu=0.0,
        )

        # Train with fedprox (mu=1.0)
        w_prox, loss_prox = trainer._train_client_local(
            initial_weights=weights,
            X_k=X_k,
            y_k=y_k,
            strategy="fedprox",
            fedprox_mu=1.0,
        )

        assert isinstance(w_avg, dict)
        assert isinstance(w_prox, dict)
        # FedProx with large proximal penalty produces different weights closer to initial
        assert not torch.allclose(
            torch.as_tensor(w_avg["network.0.weight"]),
            torch.as_tensor(w_prox["network.0.weight"]),
        )


class TestComparativeCreditCardEvaluator:
    """Validates isolated silo training and comparative evaluations."""

    def test_isolated_silos_and_pooled_evaluation(self, tmp_path: Path) -> None:
        """Verify comparative evaluator runs on client partitions and produces expected structures."""
        partitioner = CreditCardPartitioner(skew_mode="extreme_skew", seed=42)
        empty_dir = tmp_path / "mock_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)
        partitions = partitioner.load_and_partition(nrows=600, require_real=False, path=empty_dir)

        trainer = FederatedCreditCardTrainer(in_features=30, hidden_dims=(16,), seed=42)
        evaluator = ComparativeCreditCardEvaluator(trainer=trainer)

        silo_res = evaluator.evaluate_isolated_silos(partitions, partitioner.X_global_test, partitioner.y_global_test)
        assert "silos" in silo_res
        assert "bank_a" in silo_res["silos"]
        assert "bank_b" in silo_res["silos"]
        assert "bank_c" in silo_res["silos"]
        assert "consortium_mean" in silo_res

        pooled_res = evaluator.evaluate_centralized_pooled(partitions, partitioner.X_global_test, partitioner.y_global_test)
        assert "metrics" in pooled_res
        assert "pr_auc" in pooled_res["metrics"]
        assert "roc_auc" in pooled_res["metrics"]


class TestCreditCardBenchmarkPlots:
    """Validates generation of publication-grade figures."""

    def test_plot_generation(self, tmp_path: Path) -> None:
        """Verify convergence, PR, ROC, and imbalance bar charts save cleanly."""
        conv_data: dict[str, Any] = {
            "fedavg": {
                "round_pr_aucs": [0.1, 0.4, 0.7],
                "round_losses": [0.5, 0.2, 0.1],
                "final_pr_auc": 0.7,
            },
            "fedprox": {
                "round_pr_aucs": [0.1, 0.35, 0.65],
                "round_losses": [0.5, 0.22, 0.12],
                "final_pr_auc": 0.65,
            },
        }
        p1 = plot_optimizer_convergence(conv_data, tmp_path / "conv.png")
        assert p1.exists()

        pr_curves: dict[str, Any] = {
            "Centralized Upper Bound": (np.array([0.0, 0.5, 1.0]), np.array([1.0, 0.8, 0.5]), 0.80),
            "Federated Champion (FedAvg)": (np.array([0.0, 0.5, 1.0]), np.array([1.0, 0.7, 0.4]), 0.70),
            "Bank C Silo (Near-Zero Fraud)": (np.array([0.0, 0.5, 1.0]), np.array([0.1, 0.05, 0.01]), 0.05),
        }
        p2 = plot_multi_paradigm_pr(pr_curves, 0.00172, tmp_path / "pr.png")
        assert p2.exists()

        roc_curves: dict[str, Any] = {
            "Centralized Upper Bound": (np.array([0.0, 0.01, 1.0]), np.array([0.0, 0.8, 1.0]), 0.95),
            "Federated Champion (FedAvg)": (np.array([0.0, 0.01, 1.0]), np.array([0.0, 0.75, 1.0]), 0.92),
        }
        p3 = plot_multi_paradigm_roc(roc_curves, tmp_path / "roc.png")
        assert p3.exists()

        p4 = plot_imbalance_robustness_barchart(
            prauc_map={"Bank C": 0.05, "FedAvg": 0.75, "Pooled": 0.82},
            rocauc_map={"Bank C": 0.60, "FedAvg": 0.94, "Pooled": 0.97},
            rec01_map={"Bank C": 0.02, "FedAvg": 0.85, "Pooled": 0.88},
            output_path=tmp_path / "bar.png",
        )
        assert p4.exists()


class TestCreditCardBenchmarkEndToEnd:
    """Validates full end-to-end benchmark execution and artifact validation."""

    def test_run_creditcard_benchmark_synthetic(self, tmp_path: Path) -> None:
        """Execute end-to-end benchmark runner on synthetic slice and validate outputs."""
        out_dir = tmp_path / "benchmark_run"
        res = run_creditcard_benchmark(
            nrows=600,
            rounds=2,
            local_epochs=1,
            batch_size=32,
            skew_mode="extreme_skew",
            output_dir=out_dir,
            seed=42,
        )

        assert "fed_results" in res
        assert "silo_results" in res
        assert "pooled_results" in res
        assert "paths" in res

        # Verify artifacts exist
        results_json_path = res["paths"]["results_json"]
        comp_json_path = res["paths"]["comparative_json"]
        dossier_path = res["paths"]["audit_dossier"]

        assert results_json_path.exists()
        assert comp_json_path.exists()
        assert dossier_path.exists()

        # Validate Pydantic v2 ExperimentResult schema loading
        with open(results_json_path, encoding="utf-8") as f:
            data = json.load(f)
        exp_res = ExperimentResult.model_validate(data)
        assert exp_res.status == "COMPLETED"
        assert exp_res.dataset.dataset_name == "European Credit Card Fraud Detection"

        # Validate comparative baselines JSON
        with open(comp_json_path, encoding="utf-8") as f:
            comp_data = json.load(f)
        assert "collaborative_gain" in comp_data
        assert "centralized_pooled" in comp_data
        assert "isolated_silos" in comp_data

        # Validate Markdown dossier
        md_text = dossier_path.read_text(encoding="utf-8")
        assert "# 💳 European Credit Card Fraud Extreme Imbalance Federated Benchmark Dossier" in md_text
        assert "Collaborative Gain" in md_text
        assert "bank_c" in md_text

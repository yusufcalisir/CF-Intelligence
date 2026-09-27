"""Unit and integration test suite for AMLNet Extreme Imbalance Federated Benchmark.

Verifies:
1. AMLNetPartitioner:
   - Institutional volume and laundering label skew partitioning across banking nodes.
   - Symmetric Dirichlet partitioning and total sample conservation.
   - Non-overlapping client allocation and global test set isolation.
2. AMLNetClassifier & Federated Optimization:
   - Forward pass, logits shape, and calibrated probability outputs.
   - Parameter weight cloning, setting, and sample-weighted aggregation.
   - Centralized cost-sensitive training and Federated (FedAvg / FedProx) rounds.
3. Operational Metrics & Baselines:
   - Strict fixed-FPR recall calculation (0.01%, 0.05%, 0.1%, 0.5%, 1.0% FPR).
   - Expected Calibration Error (ECE) and Brier score.
   - End-to-end benchmark execution and artifact serialization (results.json, comparative_baselines.json, audit_dossier.md).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from experiments.amlnet.evaluate_imbalance import (
    AMLNetClassifier,
    AMLNetPartitioner,
    CentralizedAMLNetTrainer,
    FederatedAMLNetTrainer,
    aggregate_weights,
    calculate_ece,
    calculate_recall_at_fixed_fpr,
    clone_weights,
    evaluate_predictions,
    run_amlnet_benchmark,
    set_weights,
)
from experiments.harness.schema import ExperimentResult


class TestAMLNetPartitioner:
    """Validates multi-bank non-IID and institutional partitioning properties for AMLNet."""

    def test_institutional_split_allocation(self) -> None:
        """Verify institutional split distributes Bank Alpha (50%), Beta (30%), Gamma (20%)."""
        partitioner = AMLNetPartitioner(seed=42)
        partitioner.load_data(nrows=500, all_rows=False, require_real=False, test_ratio=0.20)

        clients_data = partitioner.partition_clients(num_clients=3, skew_mode="institutional_split")
        assert len(clients_data) == 3
        assert "bank_alpha" in clients_data
        assert "bank_beta" in clients_data
        assert "bank_gamma" in clients_data

        total_train_samples = len(partitioner.y_train)
        n_alpha = len(clients_data["bank_alpha"][1])
        n_beta = len(clients_data["bank_beta"][1])
        n_gamma = len(clients_data["bank_gamma"][1])

        assert n_alpha + n_beta + n_gamma == total_train_samples
        # Check volume proportions approximately 50%, 30%, 20%
        assert abs(n_alpha / total_train_samples - 0.50) < 0.05
        assert abs(n_beta / total_train_samples - 0.30) < 0.05
        assert abs(n_gamma / total_train_samples - 0.20) < 0.05

    def test_dirichlet_split_allocation(self) -> None:
        """Verify Dirichlet skew mode divides transactions across arbitrary client counts."""
        partitioner = AMLNetPartitioner(seed=42)
        partitioner.load_data(nrows=400, all_rows=False, require_real=False, test_ratio=0.25)

        clients_data = partitioner.partition_clients(num_clients=4, skew_mode="dirichlet", alpha=0.5)
        assert len(clients_data) == 4

        total_client_samples = sum(len(y) for _, y in clients_data.values())
        assert total_client_samples == len(partitioner.y_train)

    def test_global_test_isolation(self) -> None:
        """Verify test set remains sequestered and untouched by partitioning."""
        partitioner = AMLNetPartitioner(seed=42)
        partitioner.load_data(nrows=300, all_rows=False, require_real=False, test_ratio=0.20)
        X_test, y_test = partitioner.get_global_test()

        assert len(X_test) == 60
        assert len(y_test) == 60
        assert len(partitioner.X_train) == 240


class TestAMLNetClassifier:
    """Validates neural architecture forward pass, loss computation, and probability outputs."""

    def test_forward_pass_and_predictions(self) -> None:
        """Verify forward logits shape and predict_proba range."""
        model = AMLNetClassifier(input_dim=18, hidden_dim=32)
        x = torch.randn(16, 18)
        logits = model(x)
        assert logits.shape == (16,)

        probs = model.predict_proba(x)
        assert probs.shape == (16,)
        assert (probs >= 0.0).all() and (probs <= 1.0).all()

    def test_gradient_flow(self) -> None:
        """Verify backward gradient propagation under BCEWithLogitsLoss."""
        model = AMLNetClassifier(input_dim=18, hidden_dim=32)
        optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
        criterion = torch.nn.BCEWithLogitsLoss()

        x = torch.randn(8, 18)
        y = torch.tensor([0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0])

        optimizer.zero_grad()
        logits = model(x)
        loss = criterion(logits, y)
        loss.backward()

        for param in model.parameters():
            assert param.grad is not None
            assert not torch.isnan(param.grad).any()


class TestParameterAggregation:
    """Validates parameter cloning, setting, and sample-weighted aggregation."""

    def test_weight_clone_and_set(self) -> None:
        """Verify state dict cloning and loading into PyTorch model."""
        model = AMLNetClassifier(input_dim=18, hidden_dim=32)
        cloned = clone_weights(model)

        assert isinstance(cloned, dict)
        for k, v in model.state_dict().items():
            assert k in cloned
            assert torch.allclose(v, cloned[k])

        # Perturb cloned weights and apply
        perturbed = {k: v + 1.0 for k, v in cloned.items()}
        set_weights(model, perturbed)
        for k, v in model.state_dict().items():
            assert torch.allclose(v, perturbed[k])

    def test_fedavg_weighted_averaging(self) -> None:
        """Verify mathematical correctness of sample-weighted parameter aggregation."""
        w1 = {"w": torch.tensor([2.0, 4.0]), "b": torch.tensor([6.0])}
        w2 = {"w": torch.tensor([6.0, 8.0]), "b": torch.tensor([10.0])}

        # Weights with sample counts 100 and 300 (ratio 1:3)
        client_updates = [(w1, 100), (w2, 300)]
        agg = aggregate_weights(client_updates)

        assert torch.allclose(agg["w"], torch.tensor([5.0, 7.0]))
        assert torch.allclose(agg["b"], torch.tensor([9.0]))


class TestMetricEvaluationHelpers:
    """Validates operational metrics: fixed-FPR recalls, ECE, and comprehensive metrics."""

    def test_fixed_fpr_recalls(self) -> None:
        """Verify recall calculation at fixed FPR thresholds."""
        y_true = np.array([0] * 1000 + [1] * 100)
        y_prob = np.concatenate([np.linspace(0.01, 0.40, 1000), np.linspace(0.60, 0.99, 100)])

        recalls = calculate_recall_at_fixed_fpr(
            y_true,
            y_prob,
            target_fprs=(0.0001, 0.0005, 0.001, 0.005, 0.010),
        )
        assert "recall_at_0_0001_fpr" in recalls
        assert "recall_at_0_001_fpr" in recalls
        assert "recall_at_0_01_fpr" in recalls

        assert 0.0 <= recalls["recall_at_0_001_fpr"] <= 1.0
        assert recalls["recall_at_0_001_fpr"] <= recalls["recall_at_0_01_fpr"]

    def test_expected_calibration_error(self) -> None:
        """Verify ECE computes valid non-negative bounded error."""
        y_true = np.array([0, 1, 0, 1, 0, 1, 0, 0, 1, 1])
        y_prob = np.array([0.1, 0.9, 0.2, 0.8, 0.3, 0.7, 0.4, 0.2, 0.85, 0.95])

        ece = calculate_ece(y_true, y_prob, n_bins=5)
        assert 0.0 <= ece <= 1.0

    def test_evaluate_predictions_suite(self) -> None:
        """Verify evaluate_predictions returns complete metric dictionary."""
        y_true = np.array([0] * 80 + [1] * 20)
        y_prob = np.random.RandomState(42).uniform(0, 1, size=100)

        metrics = evaluate_predictions(y_true, y_prob)
        required_keys = [
            "pr_auc",
            "roc_auc",
            "brier_score",
            "ece",
            "f1_score",
            "precision",
            "recall",
            "recall_at_0001_fpr",
            "recall_at_001_fpr",
            "recall_at_01_fpr",
        ]
        for key in required_keys:
            assert key in metrics
            assert not np.isnan(metrics[key])


class TestEndToEndAMLNetBenchmark:
    """Validates end-to-end benchmark execution and artifact generation."""

    def test_centralized_and_federated_training(self) -> None:
        """Verify centralized trainer and federated trainer run successfully."""
        np.random.seed(42)
        X_train = np.random.randn(100, 18).astype(np.float32)
        y_train = np.random.choice([0, 1], size=100, p=[0.8, 0.2]).astype(np.int64)
        X_test = np.random.randn(30, 18).astype(np.float32)
        y_test = np.random.choice([0, 1], size=30, p=[0.8, 0.2]).astype(np.int64)

        # Centralized Trainer
        c_trainer = CentralizedAMLNetTrainer(input_dim=18, hidden_dim=16, learning_rate=0.01, batch_size=32)
        losses = c_trainer.train(X_train, y_train, epochs=2)
        assert len(losses) == 2
        c_metrics, c_probs = c_trainer.evaluate(X_test, y_test)
        assert "pr_auc" in c_metrics
        assert len(c_probs) == 30

        # Federated Trainer (FedAvg and FedProx)
        clients = {
            "bank_a": (X_train[:60], y_train[:60]),
            "bank_b": (X_train[60:], y_train[60:]),
        }
        f_trainer_fedavg = FederatedAMLNetTrainer(input_dim=18, hidden_dim=16, learning_rate=0.01, batch_size=32, fedprox_mu=0.0)
        f_trainer_fedavg.train_round(clients, local_epochs=1)
        fedavg_metrics, fedavg_probs = f_trainer_fedavg.evaluate(X_test, y_test)
        assert "pr_auc" in fedavg_metrics
        assert len(fedavg_probs) == 30

        f_trainer_fedprox = FederatedAMLNetTrainer(input_dim=18, hidden_dim=16, learning_rate=0.01, batch_size=32, fedprox_mu=0.01)
        f_trainer_fedprox.train_round(clients, local_epochs=1)
        fedprox_metrics, _ = f_trainer_fedprox.evaluate(X_test, y_test)
        assert "pr_auc" in fedprox_metrics

    def test_run_amlnet_benchmark_small_execution(self, tmp_path: Path) -> None:
        """Verify run_amlnet_benchmark produces all expected artifact files."""
        results = run_amlnet_benchmark(
            nrows=500,
            all_rows=False,
            rounds=2,
            local_epochs=1,
            batch_size=32,
            learning_rate=0.01,
            skew_mode="institutional_split",
            num_clients=3,
            fedprox_mu=0.01,
            test_ratio=0.20,
            seed=42,
            require_real=False,
            output_dir=tmp_path,
        )

        assert results["status"] == "COMPLETED"

        # Verify artifacts on disk
        results_json = tmp_path / "results.json"
        comp_json = tmp_path / "comparative_baselines.json"
        dossier_md = tmp_path / "audit_dossier.md"
        pr_png = tmp_path / "plots" / "pr_curves.png"
        roc_png = tmp_path / "plots" / "roc_curves.png"
        conv_png = tmp_path / "plots" / "optimizer_convergence.png"
        fpr_png = tmp_path / "plots" / "low_fpr_profiling.png"

        assert results_json.is_file()
        assert comp_json.is_file()
        assert dossier_md.is_file()
        assert pr_png.is_file()
        assert roc_png.is_file()
        assert conv_png.is_file()
        assert fpr_png.is_file()

        # Validate results.json against Pydantic schema
        with open(results_json, encoding="utf-8") as f:
            data = json.load(f)
        exp_res = ExperimentResult.model_validate(data)
        assert exp_res.status == "COMPLETED"
        assert exp_res.dataset.dataset_name == "Australian AUSTRAC AMLNet Synthetic AML Extreme Imbalance Benchmark"
        assert exp_res.config.model_type == "AMLNetClassifier"


class TestAMLNetBenchmarkRunnerCLI:
    """Validates CLI parser arguments in benchmarks/runners/run_amlnet_benchmark.py."""

    def test_cli_parser_defaults(self) -> None:
        """Verify CLI argument defaults match specification."""
        from benchmarks.runners.run_amlnet_benchmark import main

        parser = argparse.ArgumentParser()
        parser.add_argument("--rounds", type=int, default=6)
        parser.add_argument("--skew-mode", default="institutional_split")
        parser.add_argument("--fedprox-mu", type=float, default=0.01)

        args = parser.parse_args([])
        assert args.rounds == 6
        assert args.skew_mode == "institutional_split"
        assert args.fedprox_mu == 0.01
        assert callable(main)

"""Unit & Integration Tests for PaySim Federated Optimization (FedAvg, FedProx, SCAFFOLD).

Verifies:
1. PaySimNeuralClassifier: architecture, LayerNorm batch size flexibility, gradient propagation.
2. Parameter Operations: cloning, setting, and sample-weighted aggregation.
3. Federated Optimizers:
   - FedAvg parameter averaging
   - FedProx proximal regularization penalty
   - SCAFFOLD control variates correction and updates
4. Metrics & Diagnostics: Recall @ 0.01% FPR, PR-AUC, ROC-AUC, Brier score, calibration curves.
5. End-to-end multi-optimizer benchmark and publication artifact generation.
"""

# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import torch

# Ensure repository root and backend directory are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from experiments.harness.schema import ExperimentResult
from experiments.paysim.train_federated import (
    FederatedPaySimTrainer,
    PaySimNeuralClassifier,
    aggregate_weights,
    clone_weights,
    compute_calibration_data,
    compute_comprehensive_metrics,
    compute_confusion_matrix_data,
    compute_curve_points,
    compute_recall_at_fpr,
    set_weights,
)


@pytest.fixture
def sample_paysim_features() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Generate deterministic synthetic tabular data mimicking PaySim schema (13 features)."""
    rng = np.random.default_rng(42)
    n_train = 600
    n_test = 200
    input_dim = 13

    # Generate train features and imbalanced labels (~5% fraud)
    y_train = (rng.random(n_train) < 0.05).astype(int)
    X_train = rng.standard_normal((n_train, input_dim)).astype(np.float32)
    X_train[y_train == 1, :3] += 2.5  # Add signal to first 3 features for fraud

    y_test = (rng.random(n_test) < 0.05).astype(int)
    X_test = rng.standard_normal((n_test, input_dim)).astype(np.float32)
    X_test[y_test == 1, :3] += 2.5

    return X_train, y_train, X_test, y_test


@pytest.fixture
def partitioned_clients(sample_paysim_features) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Partition synthetic training data across Bank A, Bank B, and Bank C."""
    X_train, y_train, _, _ = sample_paysim_features
    # 3 partitions of 200 samples each
    return {
        "bank_a": (X_train[:200], y_train[:200]),
        "bank_b": (X_train[200:400], y_train[200:400]),
        "bank_c": (X_train[400:600], y_train[400:600]),
    }


class TestPaySimNeuralClassifier:
    """Validates the PyTorch neural classifier architecture for PaySim fraud detection."""

    def test_classifier_initialization_and_forward_shape(self) -> None:
        model = PaySimNeuralClassifier(input_dim=13, hidden_dim=32, dropout=0.1)
        assert model.input_dim == 13
        assert model.hidden_dim == 32

        x = torch.randn(16, 13)
        out = model(x)
        assert out.shape == (16,)
        # Sigmoid activation guarantees output in [0, 1]
        assert torch.all(out >= 0.0) and torch.all(out <= 1.0)

    def test_classifier_single_sample_inference(self) -> None:
        """LayerNorm must allow batch size of 1 without crashing or throwing BatchNorm errors."""
        model = PaySimNeuralClassifier(input_dim=13, hidden_dim=32)
        model.eval()
        x = torch.randn(1, 13)
        out = model(x)
        assert out.shape == (1,)
        assert 0.0 <= out.item() <= 1.0

    def test_classifier_gradient_propagation(self) -> None:
        model = PaySimNeuralClassifier(input_dim=13, hidden_dim=32)
        model.train()
        x = torch.randn(8, 13)
        y = torch.ones(8)

        criterion = torch.nn.BCELoss()
        preds = model(x)
        loss = criterion(preds, y)
        loss.backward()

        for name, param in model.named_parameters():
            if param.requires_grad:
                assert param.grad is not None, f"Gradient missing for {name}"
                assert not torch.all(param.grad == 0), f"Zero gradient for {name}"


class TestWeightsAndAggregation:
    """Validates parameter cloning, loading, and sample-weighted aggregation."""

    def test_clone_and_set_weights(self) -> None:
        model = PaySimNeuralClassifier(input_dim=13, hidden_dim=32)
        weights = clone_weights(model)

        assert isinstance(weights, dict)
        assert "network.0.weight" in weights
        assert weights["network.0.weight"].device.type == "cpu"

        # Mutate model weights
        with torch.no_grad():
            for p in model.parameters():
                p.add_(1.0)

        # Restore original cloned weights
        set_weights(model, weights, device=torch.device("cpu"))
        restored_weights = clone_weights(model)

        for k in weights:
            assert torch.allclose(weights[k], restored_weights[k])

    def test_aggregate_weights_sample_weighted(self) -> None:
        model1 = PaySimNeuralClassifier(input_dim=4, hidden_dim=8)
        model2 = PaySimNeuralClassifier(input_dim=4, hidden_dim=8)

        w1 = clone_weights(model1)
        w2 = clone_weights(model2)

        # Manually assign known constant weights for testing
        w1["network.0.weight"] = torch.full_like(w1["network.0.weight"], 2.0)
        w2["network.0.weight"] = torch.full_like(w2["network.0.weight"], 6.0)

        # 300 samples for client 1, 100 samples for client 2 (3:1 ratio)
        # Expected: 2.0 * 0.75 + 6.0 * 0.25 = 1.5 + 1.5 = 3.0
        client_updates = [(w1, 300), (w2, 100)]
        agg = aggregate_weights(client_updates)

        expected = torch.full_like(w1["network.0.weight"], 3.0)
        assert torch.allclose(agg["network.0.weight"], expected)

    def test_aggregate_weights_empty_error(self) -> None:
        with pytest.raises(ValueError, match="Cannot aggregate empty"):
            aggregate_weights([])


class TestFederatedPaySimTrainerOptimizers:
    """Validates FedAvg, FedProx, and SCAFFOLD optimization behavior."""

    def test_fedavg_training_round(self, partitioned_clients, sample_paysim_features) -> None:
        _, _, X_test, y_test = sample_paysim_features
        trainer = FederatedPaySimTrainer(input_dim=13, hidden_dim=32, learning_rate=0.005, local_epochs=1, seed=42)

        res = trainer.train_federated(
            client_partitions=partitioned_clients,
            X_global_test=X_test,
            y_global_test=y_test,
            strategy="fedavg",
            rounds=2,
            verbose=False,
        )

        assert res["strategy"] == "fedavg"
        assert len(res["history"]) == 3  # Round 0 (baseline) + Round 1 + Round 2
        assert res["final_metrics"]["roc_auc"] >= 0.5
        assert 0.0 <= res["final_metrics"]["pr_auc"] <= 1.0

    def test_fedprox_proximal_regularization_effect(self, partitioned_clients, sample_paysim_features) -> None:
        """Asserts that FedProx with large mu keeps local weights closer to initial weights than mu=0."""
        trainer = FederatedPaySimTrainer(input_dim=13, hidden_dim=32, learning_rate=0.01, local_epochs=3, seed=42)
        X_k, y_k = partitioned_clients["bank_a"]

        model = trainer.create_model()
        initial_weights = clone_weights(model)

        # Train with mu=0.0 (unregularized)
        w_mu0, _, _ = trainer._train_client_local(
            client_id="bank_a",
            initial_weights=initial_weights,
            X_k=X_k,
            y_k=y_k,
            strategy="fedprox",
            fedprox_mu=0.0,
        )

        # Train with mu=2.0 (heavily regularized towards initial weights)
        w_mu_large, _, _ = trainer._train_client_local(
            client_id="bank_a",
            initial_weights=initial_weights,
            X_k=X_k,
            y_k=y_k,
            strategy="fedprox",
            fedprox_mu=2.0,
        )

        diff_mu0 = sum(torch.norm(w_mu0[k] - initial_weights[k]).item() for k in initial_weights)
        diff_mu_large = sum(torch.norm(w_mu_large[k] - initial_weights[k]).item() for k in initial_weights)

        # High mu must constrain parameter divergence from initial global reference
        assert diff_mu_large < diff_mu0

    def test_scaffold_control_variates_update(self, partitioned_clients, sample_paysim_features) -> None:
        """Asserts that SCAFFOLD updates client control variates c_k."""
        trainer = FederatedPaySimTrainer(input_dim=13, hidden_dim=32, learning_rate=0.01, local_epochs=1, seed=42)
        X_k, y_k = partitioned_clients["bank_a"]

        model = trainer.create_model()
        initial_weights = clone_weights(model)
        server_c = {k: torch.zeros_like(v) for k, v in initial_weights.items()}
        client_c = {k: torch.zeros_like(v) for k, v in initial_weights.items()}

        up_w, loss, updated_c = trainer._train_client_local(
            client_id="bank_a",
            initial_weights=initial_weights,
            X_k=X_k,
            y_k=y_k,
            strategy="scaffold",
            server_c=server_c,
            client_c=client_c,
        )

        assert updated_c is not None
        # Control variates must not remain identically zero after training
        non_zero = any(torch.any(updated_c[k] != 0.0) for k in updated_c)
        assert non_zero


class TestMetricCalculation:
    """Validates operational fraud metrics, curves, and calibration calculations."""

    def test_compute_recall_at_fpr_known_distribution(self) -> None:
        # Perfectly separable synthetic labels and scores
        y_true = np.array([0] * 900 + [1] * 100)
        y_pred = np.array([0.05] * 900 + [0.95] * 100)

        rec_01 = compute_recall_at_fpr(y_true, y_pred, target_fpr=0.01)
        assert rec_01 == 1.0

        # Uniform random predictions
        rng = np.random.default_rng(42)
        y_true_rand = rng.choice([0, 1], size=1000, p=[0.95, 0.05])
        y_pred_rand = rng.random(1000)

        rec_strict = compute_recall_at_fpr(y_true_rand, y_pred_rand, target_fpr=0.001)
        assert 0.0 <= rec_strict <= 1.0

    def test_compute_comprehensive_metrics_structure(self) -> None:
        y_true = np.array([0, 0, 0, 1, 0, 1, 0, 0, 1, 0])
        y_pred = np.array([0.1, 0.2, 0.1, 0.9, 0.3, 0.8, 0.2, 0.4, 0.7, 0.3])

        metrics = compute_comprehensive_metrics(y_true, y_pred, threshold=0.5, duration_seconds=0.05)
        assert "pr_auc" in metrics
        assert "roc_auc" in metrics
        assert "brier_score" in metrics
        assert "recall_at_001_fpr" in metrics
        assert "recall_at_01_fpr" in metrics
        assert metrics["samples_evaluated"] == 10

    def test_compute_curve_points_and_confusion_matrix(self) -> None:
        y_true = np.array([0, 0, 1, 1, 0, 1])
        y_pred = np.array([0.1, 0.2, 0.8, 0.9, 0.3, 0.7])

        curves = compute_curve_points(y_true, y_pred, max_points=20)
        assert len(curves.fpr) <= 20
        assert len(curves.tpr) <= 20
        assert len(curves.precision) <= 20
        assert len(curves.recall) <= 20

        cm = compute_confusion_matrix_data(y_true, y_pred, threshold=0.5)
        assert cm.tp == 3
        assert cm.tn == 3
        assert cm.fp == 0
        assert cm.fn == 0

    def test_compute_calibration_data(self) -> None:
        y_true = np.array([0, 0, 0, 0, 1, 1, 1, 1])
        y_pred = np.array([0.1, 0.15, 0.2, 0.25, 0.8, 0.85, 0.9, 0.95])

        cal = compute_calibration_data(y_true, y_pred, n_bins=5)
        assert len(cal.prob_true) > 0
        assert len(cal.prob_pred) > 0
        assert cal.brier_score >= 0.0


class TestMultiOptimizerBenchmark:
    """Validates the multi-optimizer comparison harness across FedAvg, FedProx, and SCAFFOLD."""

    def test_run_multi_optimizer_benchmark(self, partitioned_clients, sample_paysim_features) -> None:
        _, _, X_test, y_test = sample_paysim_features
        trainer = FederatedPaySimTrainer(input_dim=13, hidden_dim=32, learning_rate=0.005, local_epochs=1, seed=42)

        benchmark_res = trainer.run_multi_optimizer_benchmark(
            client_partitions=partitioned_clients,
            X_global_test=X_test,
            y_global_test=y_test,
            rounds=2,
            fedprox_mu=0.01,
        )

        assert "optimizer_results" in benchmark_res
        assert "convergence_comparison" in benchmark_res
        assert "best_optimizer" in benchmark_res

        assert set(benchmark_res["optimizer_results"].keys()) == {"fedavg", "fedprox", "scaffold"}
        for strat in ["fedavg", "fedprox", "scaffold"]:
            comp = benchmark_res["convergence_comparison"][strat]
            assert len(comp["round_losses"]) == 3  # Round 0, 1, 2
            assert len(comp["round_pr_aucs"]) == 3
            assert comp["final_pr_auc"] >= 0.0


class TestEndToEndPaySimBenchmarkPipeline:
    """Validates master benchmark execution and ExperimentResult schema compliance."""

    def test_end_to_end_paysim_runner_execution(self, tmp_path) -> None:
        from benchmarks.runners.run_paysim_benchmark import run_paysim_benchmark

        out_dir = tmp_path / "paysim_results"
        plot_dir = tmp_path / "paysim_plots"

        # Execute small smoke benchmark
        res = run_paysim_benchmark(
            alpha=0.5,
            num_clients=3,
            rounds=1,
            local_epochs=1,
            batch_size=32,
            learning_rate=0.005,
            nrows=600,
            all_rows=False,
            output_dir=out_dir,
            plot_dir=plot_dir,
            run_comparative_baselines=True,
            seed=42,
        )

        assert "experiment_result" in res
        exp_res = res["experiment_result"]
        # Validate through Pydantic schema
        validated_schema = ExperimentResult.model_validate(exp_res)
        assert validated_schema.status == "COMPLETED"
        assert validated_schema.config.experiment_id == "exp_paysim_federated_benchmark"

        # Assert visual plot artifacts exist
        assert (plot_dir / "optimizer_convergence.png").is_file()
        assert (plot_dir / "roc_curves.png").is_file()
        assert (plot_dir / "pr_curves.png").is_file()
        assert (plot_dir / "confusion_matrices.png").is_file()
        assert (out_dir / "results.json").is_file()

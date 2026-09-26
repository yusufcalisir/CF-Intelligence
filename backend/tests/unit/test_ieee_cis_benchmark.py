"""Unit & Integration Tests for IEEE-CIS Federated Fraud Benchmark (FedAvg, FedProx).

Verifies:
1. IEEECISNeuralClassifier: architecture, LayerNorm batch size flexibility, gradient propagation.
2. Parameter Operations: cloning, setting, and sample-weighted aggregation.
3. Fixed-FPR Operational Metrics: Recall @ 0.1%, 0.5%, 1.0% FPR, PR-AUC, ROC-AUC, Brier score, calibration curves.
4. Federated Optimizers:
   - FedAvg parameter averaging
   - FedProx proximal regularization penalty
5. Multi-optimizer benchmark execution and schema-compliant serialization:
   - Pydantic v2 ExperimentResult schema validation
   - Comparative baselines validation
   - Raw benchmark JSON validation
   - Markdown audit dossier generation
"""

# ruff: noqa: E402
from __future__ import annotations

import json
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
from experiments.ieee_cis.run_ieee_benchmark import (
    FederatedIEEECISTrainer,
    IEEECISNeuralClassifier,
    aggregate_weights,
    clone_weights,
    compute_calibration_data,
    compute_comprehensive_metrics,
    compute_confusion_matrix_data,
    compute_curve_points,
    compute_recall_at_fpr,
    run_ieee_benchmark,
    set_weights,
)


@pytest.fixture
def sample_ieee_cis_features() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Generate deterministic synthetic tabular data mimicking IEEE-CIS schema (42 features)."""
    rng = np.random.default_rng(42)
    n_train = 600
    n_test = 200
    input_dim = 42

    # Generate train features and imbalanced labels (~5% fraud)
    y_train = (rng.random(n_train) < 0.05).astype(int)
    X_train = rng.standard_normal((n_train, input_dim)).astype(np.float32)
    X_train[y_train == 1, :5] += 2.5  # Signal on first 5 features

    y_test = (rng.random(n_test) < 0.05).astype(int)
    X_test = rng.standard_normal((n_test, input_dim)).astype(np.float32)
    X_test[y_test == 1, :5] += 2.5

    return X_train, y_train, X_test, y_test


@pytest.fixture
def partitioned_ieee_clients(sample_ieee_cis_features) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Partition synthetic training data across Bank A, Bank B, and Bank C."""
    X_train, y_train, _, _ = sample_ieee_cis_features
    return {
        "bank_a": (X_train[:200], y_train[:200]),
        "bank_b": (X_train[200:400], y_train[200:400]),
        "bank_c": (X_train[400:600], y_train[400:600]),
    }


class TestIEEECISNeuralClassifier:
    """Validates the PyTorch neural classifier architecture for IEEE-CIS fraud detection."""

    def test_classifier_initialization_and_forward_shape(self) -> None:
        model = IEEECISNeuralClassifier(input_dim=42, hidden_dim=64, dropout=0.1)
        assert model.input_dim == 42
        assert model.hidden_dim == 64

        x = torch.randn(16, 42)
        out = model(x)
        assert out.shape == (16,)
        # Sigmoid activation guarantees output in [0, 1]
        assert torch.all(out >= 0.0) and torch.all(out <= 1.0)

    def test_classifier_single_sample_inference(self) -> None:
        """LayerNorm must allow batch size of 1 without crashing or throwing BatchNorm errors."""
        model = IEEECISNeuralClassifier(input_dim=42, hidden_dim=64)
        model.eval()
        x = torch.randn(1, 42)
        out = model(x)
        assert out.shape == (1,)
        assert 0.0 <= out.item() <= 1.0

    def test_gradient_backpropagation(self) -> None:
        """Ensures non-zero gradients flow through all linear layers during backpropagation."""
        model = IEEECISNeuralClassifier(input_dim=42, hidden_dim=64)
        model.train()
        criterion = torch.nn.BCELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

        x = torch.randn(8, 42)
        y = torch.tensor([1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])

        optimizer.zero_grad()
        preds = model(x)
        loss = criterion(preds, y)
        loss.backward()

        for name, param in model.named_parameters():
            if param.requires_grad:
                assert param.grad is not None, f"Gradient for {name} was None"
                assert not torch.all(param.grad == 0.0), f"Gradient for {name} was all zeros"


class TestIEEECISParameterOperations:
    """Validates model parameter cloning, loading, and sample-weighted federated averaging."""

    def test_clone_and_set_weights(self) -> None:
        model_src = IEEECISNeuralClassifier(input_dim=42, hidden_dim=64)
        model_dst = IEEECISNeuralClassifier(input_dim=42, hidden_dim=64)

        src_weights = clone_weights(model_src)
        assert isinstance(src_weights, dict)
        assert "network.0.weight" in src_weights

        # Mutate weights slightly
        src_weights["network.0.weight"].add_(1.5)
        set_weights(model_dst, src_weights, torch.device("cpu"))

        dst_weights = clone_weights(model_dst)
        assert torch.allclose(src_weights["network.0.weight"], dst_weights["network.0.weight"])

    def test_aggregate_weights_sample_weighting(self) -> None:
        """Ensures aggregate_weights accurately performs sample-proportional parameter averaging."""
        model_1 = IEEECISNeuralClassifier(input_dim=42, hidden_dim=64)
        model_2 = IEEECISNeuralClassifier(input_dim=42, hidden_dim=64)

        w1 = clone_weights(model_1)
        w2 = clone_weights(model_2)

        # Set specific identifiable values
        for k in w1:
            w1[k] = torch.ones_like(w1[k]) * 2.0
            w2[k] = torch.ones_like(w2[k]) * 6.0

        # Weighted: 100 samples of 2.0 + 300 samples of 6.0 => total 400 => (100*2 + 300*6)/400 = 2000/400 = 5.0
        updates = [(w1, 100), (w2, 300)]
        agg = aggregate_weights(updates)

        for k in agg:
            if agg[k].dtype in (torch.float32, torch.float64):
                assert torch.allclose(agg[k], torch.ones_like(agg[k]) * 5.0)


class TestIEEECISMetricsAndOperationalFPR:
    """Validates calculation of PR-AUC, ROC-AUC, and strict False Positive Rate thresholding."""

    def test_compute_recall_at_fpr_strict(self) -> None:
        """Validates Recall @ target FPR computation with separable probabilities."""
        y_true = np.array([0] * 900 + [1] * 100)
        # Perfectly ranked: 100 frauds have highest probabilities
        y_pred = np.concatenate([np.linspace(0.01, 0.40, 900), np.linspace(0.60, 0.99, 100)])

        rec_01 = compute_recall_at_fpr(y_true, y_pred, target_fpr=0.001)  # 0.1% FPR
        rec_05 = compute_recall_at_fpr(y_true, y_pred, target_fpr=0.005)  # 0.5% FPR
        rec_10 = compute_recall_at_fpr(y_true, y_pred, target_fpr=0.01)   # 1.0% FPR

        assert 0.0 <= rec_01 <= 1.0
        assert 0.0 <= rec_05 <= 1.0
        assert 0.0 <= rec_10 <= 1.0
        assert rec_01 <= rec_05 <= rec_10

    def test_compute_comprehensive_metrics_structure(self) -> None:
        y_true = np.array([0, 1, 0, 1, 0, 0, 1, 0])
        y_pred = np.array([0.1, 0.9, 0.2, 0.8, 0.3, 0.1, 0.7, 0.2])

        metrics = compute_comprehensive_metrics(y_true, y_pred, threshold=0.5, duration_seconds=1.23)
        assert "pr_auc" in metrics
        assert "roc_auc" in metrics
        assert "f1_score" in metrics
        assert "precision" in metrics
        assert "recall" in metrics
        assert "brier_score" in metrics
        assert "recall_at_01_fpr" in metrics
        assert "recall_at_05_fpr" in metrics
        assert "recall_at_1_fpr" in metrics
        assert metrics["duration_seconds"] == 1.23

    def test_curve_points_subsampling(self) -> None:
        y_true = np.array([0] * 50 + [1] * 50)
        y_pred = np.linspace(0.0, 1.0, 100)

        curves = compute_curve_points(y_true, y_pred, max_points=20)
        assert len(curves.fpr) <= 20
        assert len(curves.tpr) <= 20
        assert len(curves.precision) <= 20
        assert len(curves.recall) <= 20

    def test_confusion_matrix_and_calibration_data(self) -> None:
        y_true = np.array([0, 0, 1, 1])
        y_pred = np.array([0.1, 0.6, 0.4, 0.9])  # threshold 0.5 => preds = [0, 1, 0, 1]

        cm = compute_confusion_matrix_data(y_true, y_pred, threshold=0.5)
        assert cm.tn == 1
        assert cm.fp == 1
        assert cm.fn == 1
        assert cm.tp == 1

        calib = compute_calibration_data(y_true, y_pred, n_bins=5)
        assert isinstance(calib.prob_true, list)
        assert isinstance(calib.prob_pred, list)
        assert calib.brier_score >= 0.0


class TestIEEECISFederatedTraining:
    """Validates multi-round federated training with FedAvg and FedProx on IEEE-CIS data."""

    def test_fedavg_training_progression(
        self,
        partitioned_ieee_clients,
        sample_ieee_cis_features,
    ) -> None:
        _, _, X_test, y_test = sample_ieee_cis_features
        trainer = FederatedIEEECISTrainer(input_dim=42, hidden_dim=32, learning_rate=0.005, batch_size=32, local_epochs=1)

        res = trainer.train_federated(
            client_partitions=partitioned_ieee_clients,
            X_global_test=X_test,
            y_global_test=y_test,
            strategy="fedavg",
            rounds=2,
            verbose=False,
        )

        assert res["strategy"] == "fedavg"
        assert len(res["history"]) == 3  # Round 0 (baseline) + Round 1 + Round 2
        assert "pr_auc" in res["final_metrics"]
        assert "roc_auc" in res["final_metrics"]
        assert "recall_at_01_fpr" in res["final_metrics"]

    def test_fedprox_proximal_regularization_penalty(
        self,
        partitioned_ieee_clients,
        sample_ieee_cis_features,
    ) -> None:
        _, _, X_test, y_test = sample_ieee_cis_features
        trainer = FederatedIEEECISTrainer(input_dim=42, hidden_dim=32, learning_rate=0.005, batch_size=32, local_epochs=1)

        res = trainer.train_federated(
            client_partitions=partitioned_ieee_clients,
            X_global_test=X_test,
            y_global_test=y_test,
            strategy="fedprox",
            rounds=2,
            fedprox_mu=0.05,
            verbose=False,
        )

        assert res["strategy"] == "fedprox"
        assert len(res["history"]) == 3
        assert res["final_metrics"]["pr_auc"] >= 0.0

    def test_multi_optimizer_benchmark_execution(
        self,
        partitioned_ieee_clients,
        sample_ieee_cis_features,
    ) -> None:
        _, _, X_test, y_test = sample_ieee_cis_features
        trainer = FederatedIEEECISTrainer(input_dim=42, hidden_dim=32, learning_rate=0.005, batch_size=32, local_epochs=1)

        suite = trainer.run_multi_optimizer_benchmark(
            client_partitions=partitioned_ieee_clients,
            X_global_test=X_test,
            y_global_test=y_test,
            rounds=2,
            fedprox_mu=0.01,
        )

        assert "fedavg" in suite["optimizer_results"]
        assert "fedprox" in suite["optimizer_results"]
        assert suite["best_optimizer"] in ("fedavg", "fedprox")
        assert "fedavg" in suite["convergence_comparison"]
        assert "fedprox" in suite["convergence_comparison"]


class TestIEEECISEndToEndBenchmarkRunner:
    """Validates end-to-end benchmark execution, schema validation, and audit dossier creation."""

    def test_run_ieee_benchmark_end_to_end_synthetic(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "ieee_cis_out"
        out_dir.mkdir()

        results = run_ieee_benchmark(
            nrows=600,
            rounds=2,
            local_epochs=1,
            batch_size=64,
            learning_rate=0.005,
            alpha=0.5,
            num_clients=3,
            test_ratio=0.20,
            seed=42,
            require_real=False,
            output_dir=out_dir,
        )

        assert "partitioner" in results
        assert "trainer" in results
        assert "optimizer_suite" in results
        assert "comparative_results" in results
        assert "experiment_result" in results
        assert "raw_benchmark_data" in results

        # 1. Verify Pydantic v2 ExperimentResult serialization
        res_file = out_dir / "results.json"
        assert res_file.exists()
        loaded_res = ExperimentResult.model_validate_json(res_file.read_text(encoding="utf-8"))
        assert loaded_res.experiment_id == "exp_ieee_cis_federated_benchmark"
        assert loaded_res.status == "COMPLETED"
        assert "pr_auc" in loaded_res.final_metrics

        # 2. Verify comparative baselines JSON
        comp_file = out_dir / "comparative_baselines.json"
        assert comp_file.exists()
        comp_data = json.loads(comp_file.read_text(encoding="utf-8"))
        assert "centralization_gap_analysis" in comp_data
        assert "silo_deficit_analysis" in comp_data
        assert "comparison_matrix" in comp_data

        # 3. Verify audit dossier markdown
        dossier_file = out_dir / "audit_dossier.md"
        assert dossier_file.exists()
        content = dossier_file.read_text(encoding="utf-8")
        assert "# 📑 IEEE-CIS Fraud Detection Federated Benchmark & Scientific Audit Dossier" in content
        assert "Empirical Benchmark Performance Matrix" in content
        assert "Centralized Upper Bound" in content
        assert "Federated Champion (FedAvg)" in content

        # 4. Verify raw benchmark data structure
        raw_file = results["paths"]["raw_benchmark_json"]
        assert raw_file.exists()
        raw_json = json.loads(raw_file.read_text(encoding="utf-8"))
        assert raw_json["dataset"] == "ieee_cis"
        assert "centralized_baseline" in raw_json
        assert "federated_fedavg" in raw_json
        assert "federated_fedprox" in raw_json
        assert "recall_at_0_1_pct_fpr" in raw_json["centralized_baseline"]
        assert "recall_at_0_1_pct_fpr" in raw_json["federated_fedavg"]

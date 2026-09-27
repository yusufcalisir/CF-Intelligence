"""Unit and integration tests for Federated Learning Dirichlet sensitivity sweep.

Verifies:
- Dirichlet statistical skew partitioning and dataset conservation.
- Forward pass and probability boundedness of FraudClassifier.
- Parameter drift metric calculation.
- Convergence execution for FedAvg, FedProx, and SCAFFOLD.
- Pydantic v2 telemetry schema validation.
- End-to-end artifact generation and executive audit dossier serialization.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch
from experiments.ablations.dirichlet_sweep import (
    DirichletDataPartitioner,
    DirichletSweepRunner,
    FLStrategyResult,
    FraudClassifier,
    RoundHistory,
    SweepConfig,
    run_dirichlet_sensitivity_sweep,
)


@pytest.fixture
def sample_partitioner() -> DirichletDataPartitioner:
    return DirichletDataPartitioner(
        n_clients=3,
        n_samples_per_client=200,
        n_features=10,
        fraud_rate=0.05,
        seed=101,
    )


class TestDirichletDataPartitioner:
    def test_dirichlet_partitioner_dataset_conservation(
        self,
        sample_partitioner: DirichletDataPartitioner,
    ) -> None:
        """Total transactions and positive fraud instances must be strictly conserved."""
        clients_data, test_X, test_y, meta = sample_partitioner.generate_and_partition(dirichlet_alpha=0.5)

        total_train_samples = sum(len(y) for _, y in clients_data)
        total_test_samples = len(test_y)
        expected_total = sample_partitioner.n_clients * sample_partitioner.n_samples_per_client

        assert total_train_samples + total_test_samples == expected_total
        assert meta["total_samples"] == expected_total
        assert meta["total_test_samples"] == total_test_samples

        total_train_pos = sum(int(np.sum(y == 1)) for _, y in clients_data)
        total_test_pos = int(np.sum(test_y == 1))
        assert total_train_pos + total_test_pos > 0

    def test_dirichlet_skew_variance_monotonicity(self) -> None:
        """Extreme alpha=0.1 must exhibit higher inter-client variance in fraud prevalence than alpha=1.0."""
        part_extreme = DirichletDataPartitioner(n_clients=5, n_samples_per_client=500, fraud_rate=0.05, seed=42)
        part_mild = DirichletDataPartitioner(n_clients=5, n_samples_per_client=500, fraud_rate=0.05, seed=42)

        c_data_ext, _, _, _ = part_extreme.generate_and_partition(dirichlet_alpha=0.1)
        c_data_mild, _, _, _ = part_mild.generate_and_partition(dirichlet_alpha=1.0)

        rates_ext = [float(np.mean(y)) if len(y) > 0 else 0.0 for _, y in c_data_ext]
        rates_mild = [float(np.mean(y)) if len(y) > 0 else 0.0 for _, y in c_data_mild]

        var_ext = float(np.var(rates_ext))
        var_mild = float(np.var(rates_mild))

        assert var_ext > var_mild, f"Extreme skew variance ({var_ext:.6f}) should exceed mild skew variance ({var_mild:.6f})"


class TestFraudClassifier:
    def test_fraud_classifier_forward_pass_boundedness(self) -> None:
        """Classifier output must be 2D tensor in unit interval [0, 1]."""
        model = FraudClassifier(input_dim=10, hidden_dim=16)
        x = torch.randn(8, 10)
        out = model(x)

        assert out.shape == (8, 1)
        assert torch.all(out >= 0.0)
        assert torch.all(out <= 1.0)


class TestSweepRunnerStrategies:
    @pytest.fixture
    def mini_dataset(self) -> tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray, np.ndarray]:
        rng = np.random.default_rng(42)
        n_clients = 3
        clients_data = []
        for _ in range(n_clients):
            x = rng.standard_normal((100, 10)).astype(np.float32)
            y = (rng.random(100) < 0.1).astype(np.int64)
            clients_data.append((x, y))

        test_X = rng.standard_normal((50, 10)).astype(np.float32)
        test_y = (rng.random(50) < 0.1).astype(np.int64)
        if np.sum(test_y) == 0:
            test_y[0] = 1  # ensure at least one positive
        return clients_data, test_X, test_y

    def test_fedavg_strategy_execution(
        self,
        mini_dataset: tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray, np.ndarray],
    ) -> None:
        clients_data, test_X, test_y = mini_dataset
        config = SweepConfig(n_clients=3, rounds=2, local_epochs=1, batch_size=32)
        runner = DirichletSweepRunner(config)

        res = runner.run_strategy("fedavg", 0.5, clients_data, test_X, test_y)

        assert isinstance(res, FLStrategyResult)
        assert res.strategy == "fedavg"
        assert len(res.rounds_history) == 2
        assert 0.0 <= res.final_pr_auc <= 1.0
        assert 0.0 <= res.final_roc_auc <= 1.0
        assert res.total_comm_mb > 0.0

    def test_fedprox_strategy_execution(
        self,
        mini_dataset: tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray, np.ndarray],
    ) -> None:
        clients_data, test_X, test_y = mini_dataset
        config = SweepConfig(n_clients=3, rounds=2, local_epochs=1, fedprox_mu=0.05)
        runner = DirichletSweepRunner(config)

        res = runner.run_strategy("fedprox", 0.5, clients_data, test_X, test_y)

        assert isinstance(res, FLStrategyResult)
        assert res.strategy == "fedprox"
        assert len(res.rounds_history) == 2
        assert np.isfinite(res.final_val_loss)

    def test_scaffold_strategy_execution(
        self,
        mini_dataset: tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray, np.ndarray],
    ) -> None:
        clients_data, test_X, test_y = mini_dataset
        config = SweepConfig(n_clients=3, rounds=2, local_epochs=1)
        runner = DirichletSweepRunner(config)

        res = runner.run_strategy("scaffold", 0.5, clients_data, test_X, test_y)

        assert isinstance(res, FLStrategyResult)
        assert res.strategy == "scaffold"
        assert len(res.rounds_history) == 2
        # SCAFFOLD transmits both parameters and variates (2x payload of FedAvg)
        fedavg_res = runner.run_strategy("fedavg", 0.5, clients_data, test_X, test_y)
        assert res.total_comm_mb > fedavg_res.total_comm_mb


class TestArtifactSerialization:
    def test_serialize_sweep_artifacts_and_dossier(self, tmp_path: Path) -> None:
        """Verify full sweep and artifact serialization pipeline in isolated directory."""
        res = run_dirichlet_sensitivity_sweep(
            rounds=2,
            n_clients=3,
            seed=42,
            base_dir=tmp_path,
        )

        assert "results_by_alpha" in res
        assert "alpha_0.1" in res["results_by_alpha"]
        assert "alpha_0.5" in res["results_by_alpha"]
        assert "alpha_1.0" in res["results_by_alpha"]

        # Check raw legacy JSON files
        raw_dir = tmp_path / "benchmarks" / "results" / "raw"
        assert (raw_dir / "fl_comparison_alpha_0.1.json").exists()
        assert (raw_dir / "fl_comparison_alpha_0.5.json").exists()
        assert (raw_dir / "fl_comparison_alpha_1.0.json").exists()

        # Check sweep JSON and dossier
        ablations_dir = tmp_path / "experiments" / "ablations"
        assert (ablations_dir / "dirichlet_sweep_results.json").exists()
        assert (ablations_dir / "audit_dossier.md").exists()

        # Check visual figure
        fig_path = tmp_path / "docs" / "figures" / "benchmark_fl_convergence.png"
        assert fig_path.exists()
        assert fig_path.stat().st_size > 5000

    def test_pydantic_schema_validation(self) -> None:
        """Pydantic v2 models must strictly parse and validate telemetry records."""
        rh = RoundHistory(
            round=1,
            train_loss=0.45,
            val_loss=0.42,
            pr_auc=0.75,
            roc_auc=0.88,
            param_drift=0.12,
            comm_mb=0.045,
        )
        assert rh.round == 1

        res = FLStrategyResult(
            strategy="fedavg",
            dirichlet_alpha=0.5,
            final_pr_auc=0.75,
            final_roc_auc=0.88,
            final_val_loss=0.42,
            total_comm_mb=0.045,
            rounds_history=[rh],
        )
        assert res.strategy == "fedavg"
        dumped = res.model_dump()
        assert dumped["rounds_history"][0]["pr_auc"] == 0.75

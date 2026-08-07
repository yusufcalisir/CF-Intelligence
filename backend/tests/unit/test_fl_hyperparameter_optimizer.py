"""Unit tests for Non-IID Dirichlet Partitioner and Optuna Bayesian FL Hyperparameter Optimizer."""

from __future__ import annotations

import numpy as np
import pytest

from app.application.services.fl_dirichlet_partitioner import DirichletPartitioner
from app.application.services.fl_hyperparameter_optimizer import FLHyperparameterOptimizer
from app.presentation.routers.optimization import TuneRequest, trigger_hyperparameter_tuning


class TestDirichletPartitioner:
    """TestSuite verifying Dirichlet Dir(alpha) partitioning properties."""

    def test_dirichlet_partition_indices(self):
        """Verify partition_indices generates non-empty splits for all clients."""
        labels = np.array([0] * 100 + [1] * 100)
        num_clients = 3
        indices_map = DirichletPartitioner.partition_indices(
            labels=labels,
            num_clients=num_clients,
            alpha=0.5,
            min_size=10,
            seed=42,
        )

        assert len(indices_map) == num_clients
        total_partitioned = sum(len(idx) for idx in indices_map.values())
        assert total_partitioned == len(labels)
        for client_idx in range(num_clients):
            assert len(indices_map[client_idx]) >= 10

    def test_dirichlet_partition_dataset(self):
        """Verify dataset feature and label arrays split correctly."""
        X = np.random.randn(200, 5)
        y = np.array([0] * 150 + [1] * 50)
        num_clients = 4

        datasets = DirichletPartitioner.partition_dataset(
            features=X,
            labels=y,
            num_clients=num_clients,
            alpha=0.1,  # Extreme Non-IID
            min_size=5,
            seed=123,
        )

        assert len(datasets) == num_clients
        for X_i, y_i in datasets:
            assert len(X_i) == len(y_i)
            assert len(X_i) >= 5

    def test_invalid_parameters_raise_value_error(self):
        """Verify ValueError raised for invalid num_clients or alpha."""
        labels = np.array([0, 1])
        with pytest.raises(ValueError, match="num_clients must be greater than 0"):
            DirichletPartitioner.partition_indices(labels, num_clients=0)

        with pytest.raises(ValueError, match="alpha concentration parameter must be greater than 0"):
            DirichletPartitioner.partition_indices(labels, num_clients=2, alpha=-0.5)


class TestFLHyperparameterOptimizer:
    """TestSuite verifying Optuna Bayesian TPE hyperparameter optimization loop."""

    def test_optuna_study_execution_and_results(self):
        """Verify Optuna study runs n_trials and extracts best hyperparameter profile."""
        optimizer = FLHyperparameterOptimizer(
            study_name="test_optuna_study",
            dirichlet_alpha=0.5,
            num_clients=3,
            num_rounds=3,
            seed=999,
        )

        results = optimizer.run_optimization(n_trials=3, timeout=30.0)

        assert results["study_name"] == "test_optuna_study"
        assert results["dirichlet_alpha"] == 0.5
        assert results["total_trials"] == 3
        assert 0.5 <= results["best_value"] <= 1.0
        assert "learning_rate" in results["best_params"]
        assert "local_epochs" in results["best_params"]
        assert "dp_clip_norm" in results["best_params"]
        assert "fedprox_mu" in results["best_params"]

    @pytest.mark.asyncio
    async def test_optimization_api_router_endpoint(self):
        """Verify trigger_hyperparameter_tuning REST API endpoint."""
        req = TuneRequest(
            study_name="api_opt_study",
            dirichlet_alpha=0.8,
            num_clients=3,
            num_rounds=2,
            n_trials=2,
            timeout_seconds=10.0,
        )

        res = await trigger_hyperparameter_tuning(req)
        assert res.study_name == "api_opt_study"
        assert res.dirichlet_alpha == 0.8
        assert res.total_trials == 2
        assert res.best_value >= 0.5
        assert len(res.best_params) > 0

"""Unit tests for Non-IID Dirichlet Partitioner and Optuna Bayesian FL Hyperparameter Optimizer."""

from __future__ import annotations

import numpy as np
import optuna
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
        """Verify Optuna study runs n_trials with real PyTorch training and extracts best parameters."""
        optimizer = FLHyperparameterOptimizer(
            study_name="test_optuna_study",
            dirichlet_alpha=0.5,
            num_clients=3,
            num_rounds=2,
            seed=999,
        )

        results = optimizer.run_optimization(n_trials=2, timeout=30.0)

        assert results["study_name"] == "test_optuna_study"
        assert results["dirichlet_alpha"] == 0.5
        assert results["total_trials"] == 2
        assert 0.5 <= results["best_value"] <= 1.0
        assert "learning_rate" in results["best_params"]
        assert "local_epochs" in results["best_params"]
        assert "dp_clip_norm" in results["best_params"]
        assert "fedprox_mu" in results["best_params"]
        assert "batch_size" in results["best_params"]
        assert results["duration_ms"] > 0.0

    def test_empty_or_pruned_trials_graceful_recovery(self):
        """Verify run_optimization handles studies where trials are pruned without crashing."""
        optimizer = FLHyperparameterOptimizer(
            study_name="pruned_recovery_study",
            dirichlet_alpha=0.5,
            num_clients=2,
            num_rounds=2,
            seed=42,
        )
        # Mock a trial that was pruned
        trial = optimizer.study.ask()
        optimizer.study.tell(trial, state=optuna.trial.TrialState.PRUNED)

        # Call run_optimization with 0 additional trials
        results = optimizer.run_optimization(n_trials=0)
        assert results["study_name"] == "pruned_recovery_study"
        assert results["total_trials"] >= 1
        assert "learning_rate" in results["best_params"]

    @pytest.mark.asyncio
    async def test_optimization_api_router_lifecycle(self):
        """Verify trigger_hyperparameter_tuning REST API endpoint lifecycle (tune -> list -> get -> delete)."""
        from fastapi import HTTPException

        from app.presentation.routers.optimization import (
            delete_study,
            get_study_details,
            list_optimization_studies,
        )

        study_id = "lifecycle_opt_study"
        req = TuneRequest(
            study_name=study_id,
            dirichlet_alpha=0.8,
            num_clients=2,
            num_rounds=2,
            n_trials=2,
            timeout_seconds=15.0,
        )

        # 1. Trigger tuning
        res = await trigger_hyperparameter_tuning(req)
        assert res.study_name == study_id
        assert res.dirichlet_alpha == 0.8
        assert res.total_trials == 2
        assert res.best_value >= 0.5
        assert len(res.best_params) > 0

        # 2. List studies
        studies = await list_optimization_studies()
        assert study_id in studies

        # 3. Get study details
        details = await get_study_details(study_id)
        assert details.study_name == study_id
        assert details.best_trial_number == res.best_trial_number

        # 4. Delete study
        del_res = await delete_study(study_id)
        assert del_res.status_code == 204

        # 5. Verify 404 after deletion
        with pytest.raises(HTTPException) as exc_info:
            await get_study_details(study_id)
        assert exc_info.value.status_code == 404

    def test_thread_safe_bounded_study_storage(self):
        """Verify thread-safety and FIFO eviction of stored studies at capacity."""
        import concurrent.futures

        from app.presentation.routers.optimization import (
            MAX_STORED_STUDIES,
            get_stored_study,
            list_stored_study_names,
            remove_stored_study,
            store_study_result,
        )

        # Concurrently store 120 studies across 8 worker threads
        def store_item(i: int):
            store_study_result(f"concurrent_study_{i}", {"val": i, "status": "done"})

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(store_item, range(120)))

        all_names = list_stored_study_names()
        # Should be capped at MAX_STORED_STUDIES (100)
        assert len(all_names) <= MAX_STORED_STUDIES
        # Check that latest study exists
        assert get_stored_study("concurrent_study_119") is not None

        # Clean up test items
        for i in range(120):
            remove_stored_study(f"concurrent_study_{i}")


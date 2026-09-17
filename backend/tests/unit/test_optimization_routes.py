"""Unit and contract tests for Federated Hyperparameter Optimization & Pareto Tuning API."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.application.schemas.optimization import ParetoPoint
from app.main import app
from app.presentation.routers.optimization import (
    MAX_STORED_STUDIES,
    STORED_STUDIES,
    _compute_pareto_dominance,
    get_stored_study,
    list_stored_study_names,
    store_study_result,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup_stored_studies():
    """Ensure clean study storage before and after each test."""
    STORED_STUDIES.clear()
    yield
    STORED_STUDIES.clear()


class TestOptimizationRoutingPrefixes:
    """Validate zero-breakage multi-prefix routing parity across all four router mounting endpoints."""

    @pytest.mark.parametrize(
        "prefix",
        [
            "/api/v1/optimization",
            "/v1/optimization",
            "/v1/admin/optimization",
            "/api/v1/admin/optimization",
        ],
    )
    def test_hyperparameters_prefix_parity(self, prefix: str):
        """Verify GET /hyperparameters responds consistently across all 4 route prefixes."""
        resp = client.get(f"{prefix}/hyperparameters")
        assert resp.status_code == 200
        data = resp.json()
        assert "search_space" in data
        assert "learning_rate" in data["search_space"]
        assert "fedprox_mu" in data["search_space"]
        assert "dp_noise_multiplier" in data["search_space"]
        assert data["default_objective"] == "validation_auc_roc (maximize)"

    @pytest.mark.parametrize(
        "prefix",
        [
            "/api/v1/optimization",
            "/v1/optimization",
            "/v1/admin/optimization",
            "/api/v1/admin/optimization",
        ],
    )
    def test_pareto_prefix_parity(self, prefix: str):
        """Verify GET /pareto responds consistently across all 4 route prefixes."""
        resp = client.get(f"{prefix}/pareto")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_evaluated_points"] > 0
        assert data["pareto_optimal_count"] > 0
        assert "recommendation" in data

    @pytest.mark.parametrize(
        "prefix",
        [
            "/api/v1/optimization",
            "/v1/optimization",
            "/v1/admin/optimization",
            "/api/v1/admin/optimization",
        ],
    )
    def test_studies_prefix_parity(self, prefix: str):
        """Verify GET /studies responds consistently across all 4 route prefixes."""
        resp = client.get(f"{prefix}/studies")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestHyperparameterSearchSpace:
    """Verify completeness and mathematical boundary conditions of the FL hyperparameter search space."""

    def test_search_space_parameters(self):
        resp = client.get("/api/v1/optimization/hyperparameters")
        assert resp.status_code == 200
        data = resp.json()

        search_space = data["search_space"]
        expected_params = {
            "learning_rate",
            "local_epochs",
            "dp_clip_norm",
            "dp_noise_multiplier",
            "staleness_gamma",
            "fedprox_mu",
            "batch_size",
            "dirichlet_alpha",
        }
        assert set(search_space.keys()) == expected_params

        # Validate bounded numerical attributes
        lr = search_space["learning_rate"]
        assert lr["type"] == "float"
        assert lr["range"] == [0.0001, 0.1]
        assert lr["scale"] == "log"

        bs = search_space["batch_size"]
        assert bs["type"] == "categorical"
        assert bs["choices"] == [16, 32, 64]

        alpha = search_space["dirichlet_alpha"]
        assert alpha["scale"] == "log"
        assert alpha["default_value"] == 0.5


class TestParetoFrontCalculation:
    """Verify multi-objective Pareto dominance calculation (AUC vs DP budget vs Latency)."""

    def test_pareto_frontier_structure_and_dominance(self):
        resp = client.get("/api/v1/optimization/pareto")
        assert resp.status_code == 200
        data = resp.json()

        points = data["pareto_points"]
        assert len(points) == data["total_evaluated_points"]

        optimal_points = [p for p in points if p["is_pareto_optimal"]]
        assert len(optimal_points) == data["pareto_optimal_count"]
        assert data["pareto_optimal_count"] > 0

        # Verify that trial 8 (dominated by trial 2 and 6) is NOT marked optimal
        trial_8 = next(p for p in points if p["trial_id"] == 8)
        assert trial_8["is_pareto_optimal"] is False

        # Recommendation must be non-null and must be one of the Pareto optimal points
        rec = data["recommendation"]
        assert rec is not None
        assert rec["is_pareto_optimal"] is True

    def test_pareto_frontier_with_stored_study(self):
        store_study_result(
            "study_alpha",
            {
                "study_name": "study_alpha",
                "dirichlet_alpha": 0.5,
                "best_trial_number": 3,
                "best_value": 0.965,
                "best_params": {
                    "learning_rate": 0.015,
                    "batch_size": 32,
                    "fedprox_mu": 0.02,
                    "dp_noise_multiplier": 0.8,
                },
                "param_importances": {"learning_rate": 0.6},
                "total_trials": 5,
                "completed_trials": 5,
                "pruned_trials": 0,
                "duration_ms": 500.0,
            },
        )

        resp = client.get("/api/v1/optimization/pareto?study_name=study_alpha")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_evaluated_points"] == 9  # 8 raw + 1 from study
        assert any(p["trial_id"] == 99 for p in data["pareto_points"])

    def test_compute_pareto_dominance_logic(self):
        """Direct unit test of _compute_pareto_dominance function."""
        # Point A: high AUC (0.95), low eps (1.0), low latency (20) -> strictly dominates B
        # Point B: lower AUC (0.90), higher eps (2.0), higher latency (30)
        p_a = ParetoPoint(trial_id=1, learning_rate=0.01, batch_size=32, fedprox_mu=0.01, dp_epsilon=1.0, auc_roc=0.95, latency_ms=20.0)
        p_b = ParetoPoint(trial_id=2, learning_rate=0.01, batch_size=32, fedprox_mu=0.01, dp_epsilon=2.0, auc_roc=0.90, latency_ms=30.0)
        p_c = ParetoPoint(trial_id=3, learning_rate=0.01, batch_size=32, fedprox_mu=0.01, dp_epsilon=0.5, auc_roc=0.88, latency_ms=40.0)  # Lower eps, trade-off

        results = _compute_pareto_dominance([p_a, p_b, p_c])
        assert results[0].is_pareto_optimal is True   # p_a
        assert results[1].is_pareto_optimal is False  # p_b dominated by p_a
        assert results[2].is_pareto_optimal is True   # p_c not dominated (lowest eps)


class TestStudiesManagementAndCRUD:
    """Validate study lifecycle: listing, retrieval, deletion, and 404 handling."""

    def test_studies_lifecycle(self):
        # 1. Initial list empty
        resp = client.get("/api/v1/optimization/studies")
        assert resp.status_code == 200
        assert resp.json() == []

        # 2. Add study
        sample_study = {
            "study_name": "benchmark_study_v1",
            "dirichlet_alpha": 0.5,
            "best_trial_number": 2,
            "best_value": 0.945,
            "best_params": {"learning_rate": 0.02, "batch_size": 32},
            "param_importances": {"learning_rate": 0.8},
            "total_trials": 3,
            "completed_trials": 3,
            "pruned_trials": 0,
            "duration_ms": 120.5,
        }
        store_study_result("benchmark_study_v1", sample_study)

        # 3. List should include it
        resp = client.get("/api/v1/optimization/studies")
        assert resp.status_code == 200
        assert "benchmark_study_v1" in resp.json()

        # 4. Get details
        resp = client.get("/api/v1/optimization/studies/benchmark_study_v1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["study_name"] == "benchmark_study_v1"
        assert data["best_value"] == 0.945

        # 5. Delete study
        resp = client.delete("/api/v1/optimization/studies/benchmark_study_v1")
        assert resp.status_code == 204

        # 6. Subsequent get returns 404
        resp = client.get("/api/v1/optimization/studies/benchmark_study_v1")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

        # 7. Subsequent delete returns 404
        resp = client.delete("/api/v1/optimization/studies/benchmark_study_v1")
        assert resp.status_code == 404

    def test_cache_bounded_fifo_eviction(self):
        """Validate thread-safe FIFO eviction at MAX_STORED_STUDIES limit."""
        for i in range(MAX_STORED_STUDIES + 10):
            store_study_result(f"study_{i}", {"study_name": f"study_{i}"})

        assert len(STORED_STUDIES) == MAX_STORED_STUDIES
        # Earliest 10 studies should have been evicted
        for i in range(10):
            assert get_stored_study(f"study_{i}") is None
        # Latest studies must exist
        for i in range(10, MAX_STORED_STUDIES + 10):
            assert get_stored_study(f"study_{i}") is not None


class TestTuneEndpointValidation:
    """Validate POST /tune schema validation and execution handling."""

    def test_tune_invalid_parameters_return_422(self):
        """Pydantic v2 rejects out-of-range parameters with 422 Unprocessable Entity."""
        # Dirichlet alpha <= 0
        resp = client.post("/api/v1/optimization/tune", json={"dirichlet_alpha": 0.0})
        assert resp.status_code == 422

        # num_clients < 1
        resp = client.post("/api/v1/optimization/tune", json={"num_clients": 0})
        assert resp.status_code == 422

        # num_rounds < 1
        resp = client.post("/api/v1/optimization/tune", json={"num_rounds": 0})
        assert resp.status_code == 422

        # study_name too short
        resp = client.post("/api/v1/optimization/tune", json={"study_name": "a"})
        assert resp.status_code == 422

    @patch("app.presentation.routers.optimization.FLHyperparameterOptimizer")
    def test_tune_mocked_execution_success(self, mock_optimizer_cls):
        mock_instance = MagicMock()
        mock_instance.run_optimization.return_value = {
            "study_name": "test_optuna_run",
            "dirichlet_alpha": 0.5,
            "best_trial_number": 1,
            "best_value": 0.932,
            "best_params": {"learning_rate": 0.01},
            "param_importances": {"learning_rate": 1.0},
            "total_trials": 2,
            "completed_trials": 2,
            "pruned_trials": 0,
            "duration_ms": 340.2,
        }
        mock_optimizer_cls.return_value = mock_instance

        resp = client.post(
            "/api/v1/optimization/tune",
            json={
                "study_name": "test_optuna_run",
                "dirichlet_alpha": 0.5,
                "num_clients": 3,
                "num_rounds": 2,
                "n_trials": 2,
                "timeout_seconds": 30.0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["study_name"] == "test_optuna_run"
        assert data["best_value"] == 0.932
        assert "test_optuna_run" in list_stored_study_names()

    @patch("app.presentation.routers.optimization.FLHyperparameterOptimizer")
    def test_tune_internal_error_returns_500(self, mock_optimizer_cls):
        mock_instance = MagicMock()
        mock_instance.run_optimization.side_effect = RuntimeError("GPU out of memory")
        mock_optimizer_cls.return_value = mock_instance

        resp = client.post(
            "/api/v1/optimization/tune",
            json={"study_name": "error_study", "num_clients": 3},
        )
        assert resp.status_code == 500
        assert "Hyperparameter tuning error" in resp.json()["detail"]

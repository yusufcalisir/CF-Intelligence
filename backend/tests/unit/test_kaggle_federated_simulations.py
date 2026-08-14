"""Unit and integration tests for Real Kaggle Benchmark Federated Learning simulations.

Validates end-to-end execution, dynamic PyTorch input_dim sizing, Non-IID Dirichlet
partitioning, and empirical metrics across PaySim, IEEE-CIS, Elliptic, and Credit Card.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.application.services.data_generator import DataGenerator
from app.application.services.dataloader import (
    DATASET_REGISTRY,
    load_dataset,
    partition_dataset_non_iid,
)
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.metrics_service import MetricsService
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.application.services.simulation_service import SimulationService
from app.config import get_settings
from app.domain.enums import SimulationStatus
from app.domain.value_objects import SimulationConfig


@pytest.fixture
def simulation_service() -> SimulationService:
    settings = get_settings()
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    fl_engine = FederatedLearningEngine(settings, model_service, privacy_service)
    data_generator = DataGenerator()
    metrics_service = MetricsService()

    return SimulationService(
        settings=settings,
        simulation_repo=None,
        bank_repo=None,
        metrics_repo=None,
        data_generator=data_generator,
        fl_engine=fl_engine,
        metrics_service=metrics_service,
        model_service=model_service,
    )


def test_dataset_registry_definitions():
    """Ensure all 4 benchmark datasets are registered in DATASET_REGISTRY."""
    assert "paysim" in DATASET_REGISTRY
    assert "ieee_cis" in DATASET_REGISTRY
    assert "elliptic" in DATASET_REGISTRY
    assert "creditcard" in DATASET_REGISTRY

    assert callable(DATASET_REGISTRY["paysim"])
    assert callable(DATASET_REGISTRY["ieee_cis"])
    assert callable(DATASET_REGISTRY["elliptic"])
    assert callable(DATASET_REGISTRY["creditcard"])


def test_dirichlet_partitioning_stability():
    """Verify that Non-IID Dirichlet partitioning allocates valid data to all 3 banks without empty partitions."""
    X = np.random.randn(1000, 10)
    y = np.random.choice([0, 1], size=1000, p=[0.95, 0.05])

    partitions = partition_dataset_non_iid(X, y, num_banks=3, alpha=0.5, seed=42)
    assert len(partitions) == 3
    for p in partitions:
        assert p["n_samples"] > 0
        assert len(p["X"]) == p["n_samples"]
        assert len(p["y"]) == p["n_samples"]
        assert "fraud_ratio" in p


@pytest.mark.parametrize("dataset_name", ["paysim", "ieee_cis", "elliptic", "creditcard"])
def test_real_dataset_loader_and_shapes(dataset_name: str):
    """Verify load_dataset returns dict with valid X and y arrays."""
    data = load_dataset(dataset_name)
    assert "X" in data
    assert "y" in data
    X = data["X"]
    y = data["y"]
    assert len(X) > 0
    assert len(y) == len(X)
    assert X.ndim == 2
    assert y.ndim == 1


@pytest.mark.parametrize("dataset_name", ["paysim", "ieee_cis", "elliptic", "creditcard"])
def test_end_to_end_federated_simulation_real_benchmarks(
    simulation_service: SimulationService, dataset_name: str
):
    """Run full 2-round federated simulation on each real Kaggle benchmark dataset."""
    config = SimulationConfig(
        num_rounds=2,
        local_epochs=1,
        batch_size=32,
        dataset=dataset_name,
        bank_a_transactions=300,
        bank_b_transactions=300,
        bank_c_transactions=300,
    )

    result = simulation_service.run_simulation(config)
    assert result.status == SimulationStatus.COMPLETED
    assert len(result.rounds) == 2
    assert len(result.banks) == 3

    last_round = result.rounds[-1]
    assert last_round.global_loss is not None
    assert last_round.global_loss > 0.0

    for bank in result.banks:
        assert bank.federated_metrics is not None
        assert 0.0 <= bank.federated_metrics.auc_roc <= 1.0

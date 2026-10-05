"""Unit and integration tests for Real Kaggle Benchmark Federated Learning simulations.

Validates end-to-end execution, dynamic PyTorch input_dim sizing, Non-IID Dirichlet
partitioning, and empirical metrics across PaySim, IEEE-CIS, Elliptic, and Credit Card.
"""

from pathlib import Path

import numpy as np
import pytest

from app.application.services.data_generator import DataGenerator
from app.application.services.dataloader import (
    DATASET_REGISTRY,
    load_dataset,
    partition_dataset_non_iid,
    resolve_dataset_dir,
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


def _has_real_benchmark_files(dataset_name: str) -> bool:
    ds_dir = resolve_dataset_dir(dataset_name)
    if not ds_dir.exists():
        return False
    if dataset_name == "paysim":
        return any(
            (ds_dir / f).exists()
            for f in ["paysim.parquet", "PS_20174392719_1491204439457_log.csv", "paysim.csv"]
        )
    if dataset_name == "ieee_cis":
        return (ds_dir / "train_transaction.csv").exists() or (ds_dir / "ieee_cis.parquet").exists()
    if dataset_name == "elliptic":
        return (ds_dir / "elliptic_cache.parquet").exists() or (
            (ds_dir / "elliptic_txs_features.csv").exists()
            and (ds_dir / "elliptic_txs_classes.csv").exists()
        )
    if dataset_name == "creditcard":
        return (ds_dir / "creditcard.parquet").exists() or (ds_dir / "creditcard.csv").exists()
    return False


@pytest.mark.parametrize("dataset_name", ["paysim", "ieee_cis", "elliptic", "creditcard"])
def test_real_dataset_loader_and_shapes(dataset_name: str):
    """Verify load_dataset returns dict with valid X and y arrays from real datasets."""
    if not _has_real_benchmark_files(dataset_name):
        pytest.skip(
            f"Physical files for real benchmark '{dataset_name}' not found on disk; failing closed without silent synthetic substitution."
        )
    data = load_dataset(dataset_name)
    assert "X" in data
    assert "y" in data
    X = data["X"]
    y = data["y"]
    assert len(X) > 0
    assert len(y) == len(X)
    assert X.ndim == 2
    assert y.ndim == 1
    assert data.get("is_synthetic") is False
    assert data.get("provenance") in ("EMPIRICAL_EXTERNAL_DATA", "PUBLIC_SIMULATED_DATASET")


@pytest.mark.parametrize("dataset_name", ["paysim", "ieee_cis", "elliptic", "creditcard"])
def test_end_to_end_federated_simulation_real_benchmarks(
    simulation_service: SimulationService, dataset_name: str
):
    """Run full 2-round federated simulation on each real Kaggle benchmark dataset."""
    if not _has_real_benchmark_files(dataset_name):
        pytest.skip(
            f"Physical files for real benchmark '{dataset_name}' not found on disk; failing closed without silent synthetic substitution."
        )
    config = SimulationConfig(
        num_rounds=2,
        local_epochs=1,
        batch_size=32,
        dataset=dataset_name,
        dataset_mode="real",
        bank_a_transactions=300,
        bank_b_transactions=300,
        bank_c_transactions=300,
    )

    result = simulation_service.run_simulation(config)
    assert result.status == SimulationStatus.COMPLETED
    assert result.dataset_mode == "real"
    assert result.dataset_provenance in ("EMPIRICAL_EXTERNAL_DATA", "PUBLIC_SIMULATED_DATASET")
    assert len(result.rounds) == 2
    assert len(result.banks) == 3

    last_round = result.rounds[-1]
    assert last_round.global_loss is not None
    assert last_round.global_loss > 0.0

    for bank in result.banks:
        assert bank.federated_metrics is not None
        assert 0.0 <= bank.federated_metrics.auc_roc <= 1.0


def test_end_to_end_federated_simulation_synthetic_contract(
    simulation_service: SimulationService,
):
    """Run simulation with explicit synthetic dataset mode and verify contract invariants."""
    config = SimulationConfig(
        num_rounds=2,
        local_epochs=1,
        batch_size=32,
        dataset="synthetic",
        dataset_mode="synthetic",
        bank_a_transactions=100,
        bank_b_transactions=100,
        bank_c_transactions=100,
    )
    result = simulation_service.run_simulation(config)
    assert result.status == SimulationStatus.COMPLETED
    assert result.dataset_mode == "synthetic"
    assert result.dataset_provenance in ("DEMO_DATA", "SYNTHETIC_EVIDENCE")
    assert len(result.rounds) == 2
    assert len(result.banks) == 3


@pytest.mark.parametrize("dataset_name", ["paysim", "ieee_cis", "elliptic", "creditcard"])
def test_real_mode_fails_closed_when_files_missing(tmp_path: Path, dataset_name: str):
    """Section 30: When real benchmark files are missing, loader fails closed with FileNotFoundError and NEVER falls back silently to synthetic."""
    fake_empty_dir = tmp_path / "empty_dataset_dir"
    fake_empty_dir.mkdir(parents=True, exist_ok=True)
    loader_fn = DATASET_REGISTRY[dataset_name]
    with pytest.raises(FileNotFoundError):
        loader_fn(path=fake_empty_dir)


@pytest.mark.parametrize("gen_name", ["paysim", "ieee_cis", "elliptic", "creditcard"])
def test_synthetic_mode_is_explicit_and_labeled(gen_name: str):
    """Section 31: Synthetic benchmark data must be explicitly requested and truthfully labeled with provenance."""
    from app.application.services import synthetic_dataset_generators as sdg

    generators = {
        "paysim": sdg.generate_synthetic_paysim,
        "ieee_cis": sdg.generate_synthetic_ieee_cis,
        "elliptic": sdg.generate_synthetic_elliptic,
        "creditcard": sdg.generate_synthetic_creditcard,
    }
    data = generators[gen_name]()
    assert data.get("is_synthetic") is True
    assert data.get("provenance") in ("TEST_FIXTURE", "CONTROLLED_PROJECT_SYNTHETIC")
    assert len(data["X"]) > 0
    assert len(data["y"]) == len(data["X"])


def test_provenance_survives_pipeline(simulation_service: SimulationService):
    """Section 32: Truthful provenance metadata is preserved from dataset loader through SimulationRun."""
    if not _has_real_benchmark_files("creditcard"):
        pytest.skip("Real creditcard dataset not found on disk")
    config = SimulationConfig(
        num_rounds=1,
        local_epochs=1,
        batch_size=32,
        dataset="creditcard",
        dataset_mode="real",
        bank_a_transactions=100,
        bank_b_transactions=100,
        bank_c_transactions=100,
    )
    result = simulation_service.run_simulation(config)
    assert result.dataset_mode == "real"
    assert result.dataset_provenance == "EMPIRICAL_EXTERNAL_DATA"

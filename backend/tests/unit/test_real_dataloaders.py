"""Unit tests for Real-World AML/Fraud Benchmark Dataset Loaders & LEAF Non-IID Partitioning."""

import numpy as np
import pytest

from app.application.services.dataloader import (
    DATASET_REGISTRY,
    load_amlnet,
    load_amlsim,
    load_creditcard_fraud,
    load_elliptic,
    load_ieee_cis,
    load_paysim,
    load_synthaml,
    partition_dataset_non_iid,
)
from app.application.services.synthetic_dataset_generators import (
    generate_synthetic_creditcard,
    generate_synthetic_elliptic,
    generate_synthetic_ieee_cis,
    generate_synthetic_paysim,
)


def test_dataset_registry_contains_all_targets():
    expected = {"elliptic", "amlsim", "paysim", "ieee_cis", "creditcard", "synthaml", "amlnet"}
    assert expected.issubset(set(DATASET_REGISTRY.keys()))


def test_load_paysim_mock_structure():
    data = generate_synthetic_paysim(n_mock_txns=2000)
    assert "X" in data
    assert "y" in data
    assert len(data["X"]) > 0
    assert len(data["y"]) == len(data["X"])
    assert data["X"].shape[1] >= 5
    assert np.sum(data["y"] == 1) >= 0


def test_load_ieee_cis_mock_structure():
    data = generate_synthetic_ieee_cis(n_mock_txns=1500)
    assert "X" in data
    assert "y" in data
    assert len(data["X"]) > 0
    assert len(data["y"]) == len(data["X"])
    assert data["X"].shape[1] >= 40
    assert np.sum(data["y"] == 1) > 0


def test_load_elliptic_mock_structure():
    data = generate_synthetic_elliptic(n_mock_nodes=1000)
    assert "X" in data
    assert "y" in data
    assert "edges" in data
    assert len(data["X"]) > 0
    assert len(data["y"]) == len(data["X"])
    assert data["X"].shape[1] == 166
    assert len(data["edges"]) > 0


def test_load_creditcard_mock_structure():
    data = generate_synthetic_creditcard(n_mock_txns=1200)
    assert len(data["X"]) > 0
    assert len(data["y"]) == len(data["X"])
    assert data["X"].shape[1] == 29


@pytest.mark.real_data
def test_load_amlsim_structure():
    data = load_amlsim(nrows=1000)
    assert "X" in data
    assert "y" in data
    assert len(data["X"]) > 0
    assert len(data["y"]) == len(data["X"])
    assert data["X"].shape[1] == 6
    assert "edges" in data
    assert "edge_index" in data
    assert "to_pyg_data" in data
    assert "to_networkx" in data
    assert callable(data["to_pyg_data"])
    assert callable(data["to_networkx"])
    assert data["source"] in ("real", "real_csv", "real_parquet", "mock")


@pytest.mark.real_data
def test_load_amlsim_pyg_and_networkx():
    data = load_amlsim(nrows=500)
    pyg_data = data["to_pyg_data"]()
    assert pyg_data is not None
    if hasattr(pyg_data, "x"):
        assert pyg_data.edge_index.shape[0] == 2
    else:
        assert "x" in pyg_data
        assert "edge_index" in pyg_data

    nx_graph = data["to_networkx"](max_edges=100)
    assert nx_graph is not None
    assert nx_graph.number_of_edges() <= 100


def test_load_amlsim_fails_closed_when_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="Real AMLSim dataset export not found"):
        load_amlsim(path=tmp_path / "nonexistent")


def test_generate_synthetic_amlsim():
    from app.application.services.synthetic_dataset_generators import generate_synthetic_amlsim

    data = generate_synthetic_amlsim(n_mock_txns=300)
    assert data["is_synthetic"] is True
    assert data["source"] == "mock"
    assert data["X"].shape == (300, 6)
    assert len(data["y"]) == 300
    assert len(data["edges"]) == 300
    assert data["edge_index"].shape == (2, 300)


def test_load_amlsim_require_real_raises_on_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="Real AMLSim dataset export not found"):
        load_amlsim(path=tmp_path / "nonexistent", require_real=True)


@pytest.mark.real_data
def test_load_synthaml_structure():
    data = load_synthaml(nrows=200)
    assert "X" in data
    assert "y" in data
    assert len(data["X"]) > 0
    assert len(data["y"]) == len(data["X"])
    assert data["X"].shape[1] == 14
    assert "alerts_df" in data
    assert "transactions_df" in data
    assert data["source"] in ("real_parquet", "real_csv", "synthetic_fallback")


@pytest.mark.real_data
def test_load_amlnet_structure():
    data = load_amlnet(nrows=200)
    assert "X" in data
    assert "y" in data
    assert len(data["X"]) > 0
    assert len(data["y"]) == len(data["X"])
    assert data["X"].shape[1] == 18
    assert "raw_df" in data
    assert "typologies" in data
    assert data["source"] in ("real_parquet", "real_csv", "synthetic_fallback")


def test_leaf_non_iid_dirichlet_partitioning():
    # Generate mock dataset
    rng = np.random.default_rng(42)
    X = rng.standard_normal((3000, 10))
    y = (rng.random(3000) < 0.05).astype(int)

    partitions = partition_dataset_non_iid(X, y, num_banks=3, alpha=0.5, seed=42)
    assert len(partitions) == 3
    total_samples = sum(p["n_samples"] for p in partitions)
    assert total_samples == 3000

    for p in partitions:
        assert "bank_id" in p
        assert len(p["X"]) == p["n_samples"]
        assert len(p["y"]) == p["n_samples"]
        assert p["n_samples"] > 0


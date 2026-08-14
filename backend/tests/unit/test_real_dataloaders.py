"""Unit tests for Real-World AML/Fraud Benchmark Dataset Loaders & LEAF Non-IID Partitioning."""

import numpy as np
import pytest

from app.application.services.dataloader import (
    DATASET_REGISTRY,
    load_creditcard_fraud,
    load_dataset,
    load_elliptic,
    load_ieee_cis,
    load_paysim,
    partition_dataset_non_iid,
)


def test_dataset_registry_contains_all_targets():
    expected = {"elliptic", "amlsim", "paysim", "ieee_cis", "creditcard"}
    assert expected.issubset(set(DATASET_REGISTRY.keys()))


def test_load_paysim_mock_structure():
    data = load_paysim(n_mock_txns=2000)
    assert "X" in data
    assert "y" in data
    assert len(data["X"]) > 0
    assert len(data["y"]) == len(data["X"])
    assert data["X"].shape[1] >= 5
    assert np.sum(data["y"] == 1) >= 0


def test_load_ieee_cis_mock_structure():
    data = load_ieee_cis(n_mock_txns=1500)
    assert "X" in data
    assert "y" in data
    assert len(data["X"]) == 1500
    assert len(data["y"]) == 1500
    assert data["X"].shape[1] == 40
    assert np.sum(data["y"] == 1) > 0


def test_load_elliptic_mock_structure():
    data = load_elliptic(n_mock_nodes=1000)
    assert "X" in data
    assert "y" in data
    assert "edges" in data
    assert len(data["X"]) == 1000
    assert len(data["y"]) == 1000
    assert data["X"].shape[1] == 166
    assert len(data["edges"]) > 0


def test_load_creditcard_mock_structure():
    data = load_creditcard_fraud(n_mock_txns=1200)
    assert len(data["X"]) == 1200
    assert len(data["y"]) == 1200
    assert data["X"].shape[1] == 29


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

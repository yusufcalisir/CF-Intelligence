"""Integration test suite for physical real-world and benchmark datasets.

Gate B: Real-dataset integration tests.
These tests exercise authentic physical dataset files located on disk.
They are gated with `@pytest.mark.real_data` and are skipped by default in standard CI
unless `--require-real-data` or `--include-real-data` is supplied.
When `--require-real-data` is enabled, missing files or broken schemas fail immediately.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.application.services.dataloader import (
    DatasetProvenance,
    load_amlnet,
    load_amlsim,
    load_creditcard_fraud,
    load_elliptic,
    load_ieee_cis,
    load_paysim,
    load_synthaml,
    resolve_dataset_dir,
)

pytestmark = pytest.mark.real_data


def _verify_dataset_file_presence(name: str, candidate_patterns: list[str], require_real_data: bool) -> bool:
    """Check if physical dataset files exist; fail closed if require_real_data is True."""
    root = resolve_dataset_dir(name)
    if not root.exists():
        if require_real_data:
            pytest.fail(f"Mandatory physical dataset directory missing: {root}")
        pytest.skip(f"Physical dataset directory missing: {root}")
        return False

    found = False
    for pat in candidate_patterns:
        if list(root.glob(pat)):
            found = True
            break

    if not found:
        if require_real_data:
            pytest.fail(f"Mandatory physical dataset files matching {candidate_patterns} missing in {root}")
        pytest.skip(f"Physical dataset files matching {candidate_patterns} missing in {root}")
        return False

    return True


class TestRealDatasetIntegration:
    """Validates physical loading, parsing, and provenance for all 7 benchmark datasets."""

    def test_real_dataset_paysim(self, require_real_data: bool) -> None:
        """Verify authentic PaySim dataset ingestion, 13 features, and temporal sequence."""
        _verify_dataset_file_presence("paysim", ["*log.csv", "*.csv", "*.parquet"], require_real_data)
        data = load_paysim(nrows=100, require_real=True)

        assert data["is_synthetic"] is False
        assert data["source"] in ("real_csv", "real_parquet")
        assert data["provenance"] == DatasetProvenance.PUBLIC_SIMULATED_DATASET.value
        assert data["artifact_origin"] == "external_physical_file"
        assert data["scientific_origin"] == "simulated"
        assert data["X"].shape == (100, 13)
        assert len(data["y"]) == 100
        assert set(np.unique(data["y"])).issubset({0, 1})
        assert "steps" in data and len(data["steps"]) == 100
        assert 0.0 <= data["fraud_ratio"] <= 1.0

    def test_real_dataset_ieee_cis(self, require_real_data: bool) -> None:
        """Verify authentic IEEE-CIS dataset ingestion, identity left-join, and non-NaN features."""
        _verify_dataset_file_presence("ieee_cis", ["train_transaction.csv", "*.csv", "*.parquet"], require_real_data)
        data = load_ieee_cis(nrows=100, require_real=True, join_identity=True)

        assert data["is_synthetic"] is False
        assert data["source"] in ("real_csv", "real_parquet")
        assert data["provenance"] == DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value
        assert data["scientific_origin"] == "empirical"
        assert data["X"].shape[0] == 100
        assert data["X"].shape[1] >= 40
        assert len(data["y"]) == 100
        assert not np.isnan(data["X"]).any()
        assert "has_identity" in data["feature_names"]
        assert "transaction_dt" in data and len(data["transaction_dt"]) == 100

    def test_real_dataset_creditcard(self, require_real_data: bool) -> None:
        """Verify authentic European Credit Card Fraud ingestion, 30 features, and scaling."""
        _verify_dataset_file_presence("creditcard", ["creditcard.csv", "*.parquet"], require_real_data)
        data = load_creditcard_fraud(nrows=100, require_real=True, include_time=True)

        assert data["is_synthetic"] is False
        assert data["source"] in ("real_csv", "real_parquet")
        assert data["provenance"] == DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value
        assert data["scientific_origin"] == "empirical"
        assert data["X"].shape == (100, 30)
        assert len(data["y"]) == 100
        assert not np.isnan(data["X"]).any()
        assert data["feature_names"][0] == "Time"
        assert data["feature_names"][-1] == "Amount"

    def test_real_dataset_elliptic(self, require_real_data: bool) -> None:
        """Verify authentic Elliptic Bitcoin Graph ingestion, 166 features, and PyG edge index."""
        _verify_dataset_file_presence(
            "elliptic",
            ["elliptic_cache.parquet", "*features*.csv", "*.parquet"],
            require_real_data,
        )
        data = load_elliptic(nrows=100, require_real=True, include_unknown=True)

        assert data["is_synthetic"] is False
        assert "real" in data["source"]
        assert data["provenance"] == DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value
        assert data["scientific_origin"] == "empirical"
        assert data["X"].shape == (100, 166)
        assert len(data["y"]) == 100
        assert "edges" in data and len(data["edges"]) > 0
        assert "edge_index" in data and data["edge_index"].shape[0] == 2
        assert callable(data["to_pyg_data"])
        assert callable(data["to_networkx"])

    def test_real_dataset_amlsim(self, require_real_data: bool) -> None:
        """Verify authentic IBM AMLSim dataset ingestion, topology edges, and PyG representation."""
        _verify_dataset_file_presence("amlsim", ["transactions.csv", "*.parquet"], require_real_data)
        data = load_amlsim(nrows=100, require_real=True)

        assert data["is_synthetic"] is False
        assert "real" in data["source"]
        assert data["provenance"] == DatasetProvenance.PUBLIC_SIMULATED_DATASET.value
        assert data["scientific_origin"] == "simulated"
        assert data["X"].shape == (100, 6)
        assert len(data["y"]) == 100
        assert "edges" in data
        assert "edge_index" in data and data["edge_index"].shape[0] == 2
        assert callable(data["to_pyg_data"])

    def test_real_dataset_synthaml(self, require_real_data: bool) -> None:
        """Verify authentic SynthAML dataset ingestion and controlled project synthetic classification."""
        _verify_dataset_file_presence("synthaml", ["alerts.csv", "*.parquet"], require_real_data)
        data = load_synthaml(nrows=100)

        assert data["is_synthetic"] is False
        assert "real" in data["source"]
        assert data["provenance"] == DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value
        assert data["X"].shape == (100, 14)
        assert len(data["y"]) == 100
        assert "alerts_df" in data
        assert "transactions_df" in data

    def test_real_dataset_amlnet(self, require_real_data: bool) -> None:
        """Verify authentic AMLNet dataset ingestion and controlled project synthetic classification."""
        _verify_dataset_file_presence("amlnet", ["transactions.csv", "*.parquet"], require_real_data)
        data = load_amlnet(nrows=100)

        assert data["is_synthetic"] is False
        assert "real" in data["source"]
        assert data["provenance"] == DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value
        assert data["X"].shape == (100, 18)
        assert len(data["y"]) == 100
        assert "raw_df" in data
        assert "typologies" in data

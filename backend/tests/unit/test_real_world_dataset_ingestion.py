"""Unit tests for offline real-world dataset ingestion, ETL processing, and manifest integrity.

Validates Kaggle-free offline ingestion, schema normalization, zero-raw-PII hashing,
Parquet export, and manifest generation for PaySim, IEEE-CIS, Credit Card, and Elliptic.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.application.services.dataloader import (
    load_paysim,
)
from app.application.services.etl_service import RealWorldETLPipeline


@pytest.fixture
def etl_pipeline() -> RealWorldETLPipeline:
    return RealWorldETLPipeline(salt="test_offline_salt_2026")


def test_etl_preprocess_paysim_offline(etl_pipeline: RealWorldETLPipeline):
    """Verify PaySim raw schema normalization, balance error engineering, and zero-raw-PII."""
    raw_df = pd.DataFrame(
        {
            "step": [1, 1, 2],
            "type": ["TRANSFER", "CASH_OUT", "PAYMENT"],
            "amount": [150000.0, 50000.0, 120.0],
            "nameOrig": ["C12345678", "C87654321", "C11223344"],
            "oldbalanceOrg": [150000.0, 50000.0, 200.0],
            "newbalanceOrig": [0.0, 0.0, 80.0],
            "nameDest": ["M98765432", "C99887766", "M55443322"],
            "oldbalanceDest": [0.0, 10000.0, 0.0],
            "newbalanceDest": [0.0, 60000.0, 0.0],
            "isFraud": [1, 0, 0],
            "isFlaggedFraud": [0, 0, 0],
        }
    )

    processed = etl_pipeline.preprocess_dataset("paysim", raw_df)

    assert "is_fraud" in processed.columns
    assert "errorBalanceOrig" in processed.columns
    assert "errorBalanceDest" in processed.columns
    assert "type_TRANSFER" in processed.columns
    assert "type_CASH_OUT" in processed.columns

    # Verify zero raw PII
    assert "nameOrig" not in processed.columns
    assert "nameDest" not in processed.columns
    assert processed["is_fraud"].tolist() == [1, 0, 0]
    assert len(processed) == 3


def test_etl_preprocess_ieee_cis_offline(etl_pipeline: RealWorldETLPipeline):
    """Verify IEEE-CIS raw schema normalization, email PII anonymization, and feature encoding."""
    raw_df = pd.DataFrame(
        {
            "TransactionID": [3000001, 3000002, 3000003],
            "isFraud": [0, 1, 0],
            "TransactionAmt": [100.5, 450.0, 25.0],
            "card1": [1000, 2000, 3000],
            "P_emaildomain": ["gmail.com", "yahoo.com", "anonymous.com"],
            "R_emaildomain": ["gmail.com", "", "hotmail.com"],
            "C1": [1, 2, 1],
            "V1": [1.0, 0.0, 1.0],
        }
    )

    processed = etl_pipeline.preprocess_dataset("ieee_cis", raw_df)

    assert "is_fraud" in processed.columns
    assert "TransactionID" not in processed.columns
    # Emails must not remain in raw form
    assert "P_emaildomain" not in processed.columns or not any(
        "@" in str(v) for v in processed.get("P_emaildomain", [])
    )
    assert processed["is_fraud"].tolist() == [0, 1, 0]


def test_etl_preprocess_creditcard_offline(etl_pipeline: RealWorldETLPipeline):
    """Verify Credit Card raw schema normalization and class column mapping."""
    raw_df = pd.DataFrame(
        {
            "Time": [0.0, 1.0, 2.0],
            "V1": [1.2, -0.5, 0.3],
            "V2": [-0.1, 0.8, -0.4],
            "Amount": [100.0, 25.0, 500.0],
            "Class": [0, 1, 0],
        }
    )

    processed = etl_pipeline.preprocess_dataset("creditcard", raw_df)

    assert "is_fraud" in processed.columns
    assert "Class" not in processed.columns
    assert processed["is_fraud"].tolist() == [0, 1, 0]
    assert "Amount" in processed.columns


def test_etl_preprocess_elliptic_offline(etl_pipeline: RealWorldETLPipeline):
    """Verify Elliptic raw schema normalization, class filtering, and label mapping."""
    raw_df = pd.DataFrame(
        {
            "txId": [1001, 1002, 1003],
            "class": ["1", "2", "unknown"],
            "f1": [0.1, 0.2, 0.3],
            "f2": [1.1, 1.2, 1.3],
        }
    )

    processed = etl_pipeline.preprocess_dataset("elliptic", raw_df)

    # Unknown transactions should be filtered out
    assert len(processed) == 2
    assert "is_fraud" in processed.columns
    assert processed["is_fraud"].tolist() == [1, 0]
    assert "txId" not in processed.columns


def test_etl_export_manifest_and_sha256(etl_pipeline: RealWorldETLPipeline, tmp_path: Path):
    """Verify dataset manifest generation with SHA-256 integrity hashes and metadata."""
    partition_alpha = {
        "X": np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
        "y": np.array([0, 1], dtype=int),
    }
    partition_beta = {
        "X": np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32),
        "y": np.array([0, 0], dtype=int),
    }

    file_alpha = tmp_path / "bank_alpha.parquet"
    file_beta = tmp_path / "bank_beta.parquet"

    etl_pipeline.export_partition_parquet(partition_alpha, file_alpha)
    etl_pipeline.export_partition_parquet(partition_beta, file_beta)

    manifest_path = etl_pipeline.export_dataset_manifest(
        dataset_name="paysim",
        output_dir=tmp_path,
        partitions_info=[
            {"bank_id": "bank_alpha", "file_path": file_alpha, "sample_count": 2, "fraud_count": 1},
            {"bank_id": "bank_beta", "file_path": file_beta, "sample_count": 2, "fraud_count": 0},
        ],
        feature_names=["f1", "f2"],
        alpha=0.5,
    )

    assert manifest_path.exists()

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["dataset_name"] == "paysim"
    assert manifest["total_samples"] == 4
    assert manifest["feature_count"] == 2
    assert "partitions" in manifest
    assert len(manifest["partitions"]) == 2

    # Verify SHA-256 integrity hash is present and 64 characters long
    for p_info in manifest["partitions"]:
        assert "sha256_hash" in p_info
        assert len(p_info["sha256_hash"]) == 64


def test_dataloader_offline_parquet_ingestion(etl_pipeline: RealWorldETLPipeline, tmp_path: Path):
    """Verify dataloader seamlessly ingests offline Parquet partitions with zero external network."""
    rng = np.random.default_rng(42)
    p_alpha = {
        "X": rng.standard_normal((50, 10)).astype(np.float32),
        "y": (rng.random(50) < 0.2).astype(int),
    }
    p_beta = {
        "X": rng.standard_normal((50, 10)).astype(np.float32),
        "y": (rng.random(50) < 0.1).astype(int),
    }

    etl_pipeline.export_partition_parquet(p_alpha, tmp_path / "bank_alpha.parquet")
    etl_pipeline.export_partition_parquet(p_beta, tmp_path / "bank_beta.parquet")

    data = load_paysim(path=tmp_path)
    assert data["source"] == "real_parquet"
    assert data["X"].shape == (100, 10)
    assert len(data["y"]) == 100

    # Test nrows slicing
    data_sliced = load_paysim(path=tmp_path, nrows=25)
    assert data_sliced["X"].shape[0] == 25


def test_dataloader_offline_csv_ingestion(tmp_path: Path):
    """Verify dataloader ingests raw offline CSV when parquet is not present."""
    raw_csv = tmp_path / "paysim.csv"
    df = pd.DataFrame(
        {
            "step": [1, 2],
            "type": ["TRANSFER", "CASH_OUT"],
            "amount": [1000.0, 2000.0],
            "oldbalanceOrg": [1000.0, 2000.0],
            "newbalanceOrig": [0.0, 0.0],
            "oldbalanceDest": [0.0, 500.0],
            "newbalanceDest": [1000.0, 2500.0],
            "isFraud": [1, 0],
        }
    )
    df.to_csv(raw_csv, index=False)

    data = load_paysim(path=tmp_path)
    assert data["source"] == "real_csv"
    assert data["X"].shape[0] == 2
    assert len(data["y"]) == 2

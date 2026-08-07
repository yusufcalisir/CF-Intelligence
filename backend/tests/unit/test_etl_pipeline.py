"""Unit tests for RealWorldETLPipeline and dataset ETL script."""

import numpy as np
import pandas as pd

from app.application.services.etl_service import RealWorldETLPipeline


def test_etl_anonymize_identifier():
    etl = RealWorldETLPipeline(salt="test_salt_123")
    hash1 = etl.anonymize_identifier("ACC_8849201")
    hash2 = etl.anonymize_identifier("ACC_8849201")

    assert len(hash1) == 64
    assert hash1 == hash2
    assert etl.anonymize_identifier("") == ""


def test_etl_anonymize_dataframe():
    etl = RealWorldETLPipeline(salt="test_salt_123")
    df = pd.DataFrame(
        {
            "account_id": ["ACC_1", "ACC_2"],
            "counterparty_account_id": ["ACC_99", "ACC_100"],
            "amount": [500.0, 1200.0],
        }
    )

    df_anon = etl.anonymize_dataframe(df)

    assert df_anon["amount"].tolist() == [500.0, 1200.0]
    assert df_anon["account_id"].iloc[0] != "ACC_1"
    assert len(df_anon["account_id"].iloc[0]) == 64
    assert df_anon["counterparty_account_id"].iloc[0] != "ACC_99"


def test_etl_partition_dirichlet():
    etl = RealWorldETLPipeline(salt="test_salt_123")
    rng = np.random.default_rng(42)

    X = rng.standard_normal((1000, 10))
    y = (rng.random(1000) < 0.1).astype(int)

    num_banks = 3
    partitions = etl.partition_dirichlet(X, y, num_banks=num_banks, alpha=0.5, seed=42)

    assert len(partitions) == num_banks

    total_partitioned_samples = sum(len(p["y"]) for p in partitions)
    assert total_partitioned_samples == 1000

    # Ensure each bank partition receives features and labels matching their sample count
    for p in partitions:
        assert p["X"].shape[0] == len(p["y"])
        assert p["X"].shape[1] == 10


def test_etl_export_parquet(tmp_path):
    etl = RealWorldETLPipeline(salt="test_salt_123")
    rng = np.random.default_rng(42)

    partition = {
        "X": rng.standard_normal((100, 5)).astype(np.float32),
        "y": rng.integers(0, 2, size=100),
    }

    output_file = tmp_path / "bank_alpha.parquet"
    exported_path = etl.export_partition_parquet(partition, output_file)

    assert exported_path.exists()

    df_loaded = pd.read_parquet(exported_path)
    assert len(df_loaded) == 100
    assert "is_fraud" in df_loaded.columns
    assert df_loaded.shape[1] == 6  # 5 features + 1 label

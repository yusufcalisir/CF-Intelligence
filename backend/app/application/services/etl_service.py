"""Real-World Financial Fraud Dataset Ingestion, Anonymization & Dirichlet Partitioning Engine."""

from __future__ import annotations

import hashlib
import hmac
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

import numpy as np
import pandas as pd

try:
    import pyarrow as pa  # type: ignore[import-not-found]
    import pyarrow.parquet as pq  # type: ignore[import-not-found]
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

logger = logging.getLogger(__name__)


class RealWorldETLPipeline:
    """ETL Pipeline for ingesting, anonymizing, and Non-IID Dirichlet partitioning financial datasets."""

    def __init__(self, salt: str = "cfi_network_master_salt_2026") -> None:
        self.salt = salt.encode("utf-8")

    def anonymize_identifier(self, identifier: str) -> str:
        """Computes HMAC-SHA256 hash for sensitive PII identity attributes."""
        if not identifier or pd.isna(identifier):
            return ""
        return hmac.new(self.salt, identifier.encode("utf-8"), hashlib.sha256).hexdigest()

    def anonymize_dataframe(
        self,
        df: pd.DataFrame,
        pii_columns: Sequence[str] = ("account_id", "counterparty_account_id", "ip_address", "device_id"),
    ) -> pd.DataFrame:
        """Anonymizes specified PII columns in a pandas DataFrame."""
        df_anon = df.copy()
        for col in pii_columns:
            if col in df_anon.columns:
                df_anon[col] = df_anon[col].apply(self.anonymize_identifier)
        return df_anon

    def partition_dirichlet(
        self,
        X: Any,
        y: Any,
        num_banks: int = 3,
        alpha: float = 0.5,
        seed: int = 42,
    ) -> list[dict[str, Any]]:
        """Partitions feature matrix X and labels y across K banks using Dirichlet Non-IID distribution."""
        rng = np.random.default_rng(seed)
        classes = np.unique(y)

        client_indices: list[list[int]] = [[] for _ in range(num_banks)]

        for c in classes:
            idx_c = np.where(y == c)[0]
            rng.shuffle(idx_c)

            # Draw proportions from Dirichlet distribution
            proportions = rng.dirichlet(np.repeat(alpha, num_banks))
            # Normalize and convert to split counts
            proportions = proportions / proportions.sum()
            split_points = (np.cumsum(proportions) * len(idx_c)).astype(int)[:-1]

            splits = np.split(idx_c, split_points)
            for i, split in enumerate(splits):
                client_indices[i].extend(split.tolist())

        partitions: list[dict[str, np.ndarray]] = []
        for i in range(num_banks):
            indices = np.array(client_indices[i], dtype=int)
            rng.shuffle(indices)
            partitions.append({"X": X[indices], "y": y[indices], "indices": indices})

        return partitions

    def export_partition_parquet(
        self,
        partition_data: dict[str, np.ndarray],
        output_filepath: Path | str,
        feature_names: Sequence[str] | None = None,
    ) -> Path:
        """Exports a single bank partition to compressed Parquet format."""
        out_path = Path(output_filepath)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        X = partition_data["X"]
        y = partition_data["y"]

        if feature_names is None:
            feature_names = [f"f_{i}" for i in range(X.shape[1])]

        data_dict: dict[str, Any] = {name: X[:, i] for i, name in enumerate(feature_names)}
        data_dict["is_fraud"] = y
        df = pd.DataFrame(data_dict)

        if HAS_PYARROW:
            table = pa.Table.from_pandas(df)
            pq.write_table(table, out_path, compression="snappy")
        else:
            df.to_parquet(out_path, index=False)

        logger.info("Exported %d partition samples to %s", len(df), out_path)
        return out_path

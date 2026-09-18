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


DEFAULT_PII_COLUMNS: tuple[str, ...] = (
    "account_id",
    "counterparty_account_id",
    "ip_address",
    "device_id",
    "nameOrig",
    "nameDest",
    "customer_id",
    "merchant_id",
    "email",
    "phone",
    "DeviceInfo",
    "P_emaildomain",
    "R_emaildomain",
    "card1",
    "card2",
)


class RealWorldETLPipeline:
    """ETL Pipeline for ingesting, anonymizing, and Non-IID Dirichlet partitioning financial datasets."""

    def __init__(self, salt: str = "cfi_network_master_salt_2026") -> None:
        self.salt = salt.encode("utf-8")

    def anonymize_identifier(self, identifier: Any) -> str:
        """Computes HMAC-SHA256 hash for sensitive PII identity attributes."""
        if identifier is None or pd.isna(identifier) or identifier == "":
            return ""
        ident_str = str(identifier).strip()
        if not ident_str:
            return ""
        return hmac.new(self.salt, ident_str.encode("utf-8"), hashlib.sha256).hexdigest()

    def anonymize_dataframe(
        self,
        df: pd.DataFrame,
        pii_columns: Sequence[str] = DEFAULT_PII_COLUMNS,
    ) -> pd.DataFrame:
        """Anonymizes specified PII columns in a pandas DataFrame."""
        df_anon = df.copy()
        for col in pii_columns:
            if col in df_anon.columns:
                df_anon[col] = df_anon[col].apply(self.anonymize_identifier)
        return df_anon

    def preprocess_dataset(
        self,
        dataset_or_df: str | pd.DataFrame | None = None,
        df_or_dataset: pd.DataFrame | str | None = None,
        anonymize_pii: bool = True,
        *,
        dataset_name: str | None = None,
        df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Preprocesses raw fraud dataset into clean DataFrame with normalized 'is_fraud' label.

        Supports both positional and keyword calling conventions:
        - `preprocess_dataset(dataset_name, df)`
        - `preprocess_dataset(df, dataset_name)`
        - `preprocess_dataset(df=df, dataset_name=dataset_name)`

        Handles:
        1. HMAC-SHA256 identity anonymization for PII columns.
        2. Schema-specific categorical encoding and feature engineering.
        3. String/identifier column filtering and null imputation.
        4. Normalized binary target column 'is_fraud'.
        """
        resolved_name: str = "paysim"
        resolved_df: pd.DataFrame = pd.DataFrame()

        if dataset_name is not None:
            resolved_name = dataset_name
        if df is not None:
            resolved_df = df

        if dataset_or_df is not None:
            if isinstance(dataset_or_df, str):
                resolved_name = dataset_or_df
                if df_or_dataset is not None and isinstance(df_or_dataset, pd.DataFrame):
                    resolved_df = df_or_dataset
            elif isinstance(dataset_or_df, pd.DataFrame):
                resolved_df = dataset_or_df
                if df_or_dataset is not None and isinstance(df_or_dataset, str):
                    resolved_name = df_or_dataset

        clean_name = resolved_name.lower().replace("-", "_").strip()
        df_work = self.anonymize_dataframe(resolved_df) if anonymize_pii else resolved_df.copy()

        if clean_name == "paysim":
            if "isFraud" in df_work.columns:
                df_work["is_fraud"] = df_work["isFraud"].astype(int)
                df_work.drop(columns=["isFraud"], inplace=True)
            elif "is_fraud" not in df_work.columns:
                df_work["is_fraud"] = 0

            if "isFlaggedFraud" in df_work.columns:
                df_work.drop(columns=["isFlaggedFraud"], inplace=True)

            if "type" in df_work.columns:
                for t in ["TRANSFER", "CASH_OUT", "PAYMENT", "DEBIT", "CASH_IN"]:
                    df_work[f"type_{t}"] = (df_work["type"] == t).astype(np.float32)
                df_work.drop(columns=["type"], inplace=True)
            else:
                for t in ["TRANSFER", "CASH_OUT", "PAYMENT", "DEBIT", "CASH_IN"]:
                    if f"type_{t}" not in df_work.columns:
                        df_work[f"type_{t}"] = 0.0

            if (
                "errorBalanceOrig" not in df_work.columns
                and "oldbalanceOrg" in df_work.columns
                and "newbalanceOrig" in df_work.columns
                and "amount" in df_work.columns
            ):
                df_work["errorBalanceOrig"] = df_work["newbalanceOrig"] + df_work["amount"] - df_work["oldbalanceOrg"]
            if (
                "errorBalanceDest" not in df_work.columns
                and "oldbalanceDest" in df_work.columns
                and "newbalanceDest" in df_work.columns
                and "amount" in df_work.columns
            ):
                df_work["errorBalanceDest"] = df_work["oldbalanceDest"] + df_work["amount"] - df_work["newbalanceDest"]

            for col in ["nameOrig", "nameDest"]:
                if col in df_work.columns:
                    df_work.drop(columns=[col], inplace=True)

            return df_work

        elif clean_name == "ieee_cis":
            if "isFraud" in df_work.columns:
                df_work["is_fraud"] = df_work["isFraud"].astype(int)
                df_work.drop(columns=["isFraud"], inplace=True)
            elif "label" in df_work.columns:
                df_work["is_fraud"] = df_work["label"].astype(int)
                df_work.drop(columns=["label"], inplace=True)
            elif "is_fraud" not in df_work.columns:
                df_work["is_fraud"] = 0

            for col in ["TransactionID", "DeviceInfo", "DeviceType"]:
                if col in df_work.columns:
                    df_work.drop(columns=[col], inplace=True)

            for email_col in ["P_emaildomain", "R_emaildomain"]:
                if email_col in df_work.columns:
                    df_work[email_col] = df_work[email_col].apply(
                        lambda v: self.anonymize_identifier(str(v)) if v and not pd.isna(v) else ""
                    )

            return df_work

        elif clean_name in ("creditcard", "creditcard_fraud"):
            if "Class" in df_work.columns:
                df_work["is_fraud"] = df_work["Class"].astype(int)
                df_work.drop(columns=["Class"], inplace=True)
            elif "is_fraud" not in df_work.columns:
                df_work["is_fraud"] = 0

            return df_work

        elif clean_name == "elliptic":
            if "class" in df_work.columns:
                df_work = df_work[df_work["class"].astype(str).isin(["1", "2"])].copy()
                df_work["is_fraud"] = (df_work["class"].astype(str) == "1").astype(int)
                df_work.drop(columns=["class"], inplace=True)
            elif "is_fraud" not in df_work.columns:
                df_work["is_fraud"] = 0

            for col in ["txId", "tx_id"]:
                if col in df_work.columns:
                    df_work.drop(columns=[col], inplace=True)

            return df_work

        else:
            if "isFraud" in df_work.columns:
                df_work["is_fraud"] = df_work["isFraud"].astype(int)
                df_work.drop(columns=["isFraud"], inplace=True)
            elif "Class" in df_work.columns:
                df_work["is_fraud"] = df_work["Class"].astype(int)
                df_work.drop(columns=["Class"], inplace=True)
            elif "is_fraud" not in df_work.columns:
                df_work["is_fraud"] = 0

            return df_work

    def partition_dirichlet(
        self,
        X: Any,
        y: Any,
        num_banks: int = 3,
        alpha: float = 0.5,
        rng: np.random.Generator | None = None,
        seed: int | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Partitions feature matrix X and labels y across num_banks using a Dirichlet distribution."""
        if rng is None:
            effective_seed = seed if seed is not None else kwargs.get("seed", 42)
            rng = np.random.default_rng(effective_seed)
        classes = np.unique(y)

        client_indices: list[list[int]] = [[] for _ in range(num_banks)]

        for c in classes:
            idx_c = np.where(y == c)[0]
            rng.shuffle(idx_c)

            proportions = rng.dirichlet(np.repeat(alpha, num_banks))
            proportions = proportions / proportions.sum()
            splits = np.split(idx_c, (np.cumsum(proportions)[:-1] * len(idx_c)).astype(int))

            for i, split in enumerate(splits):
                client_indices[i].extend(split.tolist())

        partitions: list[dict[str, Any]] = []
        for i in range(num_banks):
            indices = np.array(client_indices[i], dtype=int)
            rng.shuffle(indices)
            partitions.append(
                {
                    "bank_id": f"bank_{chr(ord('a') + i)}",
                    "X": X[indices],
                    "y": y[indices],
                    "indices": indices,
                }
            )

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

    def export_dataset_manifest(
        self,
        output_dir: Path | str | None = None,
        dataset_name: str = "paysim",
        partitions: list[dict[str, Any]] | None = None,
        partitions_info: list[dict[str, Any]] | None = None,
        feature_names: Sequence[str] | None = None,
        dirichlet_alpha: float = 0.5,
        alpha: float | None = None,
        **kwargs: Any,
    ) -> Path:
        """Generates institutional dataset_manifest.json with integrity hashes and partition metadata."""
        import datetime
        import json

        raw_out = output_dir or kwargs.get("output_dir")
        if raw_out is None and "dataset_name" in kwargs:
            raw_out = kwargs.get("output_dir", ".")
        target_dir = Path(raw_out or ".")
        target_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = target_dir / "dataset_manifest.json"
        partition_summaries = []
        effective_alpha = alpha if alpha is not None else dirichlet_alpha

        if partitions_info:
            for p in partitions_info:
                bank_id = p.get("bank_id", "unknown")
                fpath = Path(p.get("file_path", target_dir / f"{bank_id}.parquet"))
                file_hash = ""
                file_size_bytes = 0
                if fpath.exists():
                    file_size_bytes = fpath.stat().st_size
                    with open(fpath, "rb") as f:
                        file_hash = hashlib.sha256(f.read()).hexdigest()
                sample_count = p.get("sample_count", 0)
                fraud_count = p.get("fraud_count", 0)
                partition_summaries.append(
                    {
                        "bank_id": bank_id,
                        "file_name": fpath.name,
                        "sample_count": sample_count,
                        "fraud_count": fraud_count,
                        "fraud_ratio": float(fraud_count / sample_count) if sample_count > 0 else 0.0,
                        "sha256": file_hash,
                        "sha256_hash": file_hash,
                        "file_size_bytes": file_size_bytes,
                    }
                )
        elif partitions:
            for i, p in enumerate(partitions):
                bank_id = p.get("bank_id", f"bank_{chr(ord('a') + i)}")
                file_name = f"{bank_id}.parquet"
                file_path = target_dir / file_name
                file_hash = ""
                file_size_bytes = 0
                if file_path.exists():
                    file_size_bytes = file_path.stat().st_size
                    with open(file_path, "rb") as f:
                        file_hash = hashlib.sha256(f.read()).hexdigest()

                y_arr = p["y"]
                sample_count = len(y_arr)
                fraud_count = int(np.sum(y_arr == 1))
                partition_summaries.append(
                    {
                        "bank_id": bank_id,
                        "file_name": file_name,
                        "sample_count": sample_count,
                        "fraud_count": fraud_count,
                        "fraud_ratio": float(fraud_count / sample_count) if sample_count > 0 else 0.0,
                        "sha256": file_hash,
                        "sha256_hash": file_hash,
                        "file_size_bytes": file_size_bytes,
                    }
                )

        feats = list(feature_names) if feature_names else []
        salt_fingerprint = hashlib.sha256(self.salt).hexdigest()[:16]
        manifest_data = {
            "schema_version": "2.0.0",
            "dataset_name": dataset_name,
            "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "num_banks": len(partition_summaries),
            "dirichlet_alpha": effective_alpha,
            "alpha": effective_alpha,
            "salt_fingerprint": salt_fingerprint,
            "feature_dim": len(feats),
            "feature_count": len(feats),
            "feature_names": feats,
            "total_samples": sum(p["sample_count"] for p in partition_summaries),
            "total_frauds": sum(p["fraud_count"] for p in partition_summaries),
            "partitions": partition_summaries,
        }

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)

        logger.info("Generated dataset manifest at %s", manifest_path)
        return manifest_path

#!/usr/bin/env python
"""Command Line Tool for Real-World Fraud Dataset Ingestion, Anonymization & Dirichlet Partitioning ETL Pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add backend directory to sys.path
backend_path = Path(__file__).resolve().parent.parent / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from app.application.services.dataloader import load_dataset  # noqa: E402
from app.application.services.etl_service import RealWorldETLPipeline  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("etl_pipeline")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ETL Pipeline for Financial Fraud Dataset Ingestion, Anonymization, and Non-IID Dirichlet Partitioning"
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="paysim",
        choices=["paysim", "amlsim", "elliptic", "ieee_cis", "creditcard"],
        help="Target dataset to process (default: paysim)",
    )
    parser.add_argument(
        "--input-file",
        type=str,
        default="",
        help="Optional input CSV/Parquet path (uses dataloader synthetic mock or local directory if omitted)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="",
        help="Storage path for output Parquet bank partitions",
    )
    parser.add_argument(
        "--num-banks",
        type=int,
        default=3,
        help="Number of consortium bank nodes to partition data across (default: 3)",
    )
    parser.add_argument(
        "--dirichlet-alpha",
        type=float,
        default=0.5,
        help="Dirichlet Non-IID concentration parameter alpha (default: 0.5)",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional row limit for large datasets during processing",
    )
    parser.add_argument(
        "--salt",
        type=str,
        default="cfi_consortium_master_salt_2026",
        help="HMAC-SHA256 salt for identity anonymization",
    )
    parser.add_argument(
        "--mock-demo",
        action="store_true",
        help="Run ETL pipeline using synthetic mock generation",
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else Path(f"storage/datasets/{args.dataset}")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("==================================================================")
    logger.info("Starting Real-World Fraud Dataset ETL Pipeline")
    logger.info("Target Dataset:    %s", args.dataset)
    logger.info("Bank Partitions:   %d", args.num_banks)
    logger.info("Dirichlet Alpha:   %.2f", args.dirichlet_alpha)
    logger.info("Output Directory:  %s", output_dir)
    logger.info("==================================================================")

    etl = RealWorldETLPipeline(salt=args.salt)
    feature_names: list[str] | None = None

    if args.input_file and Path(args.input_file).exists():
        logger.info("Loading input dataset from %s", args.input_file)
        if args.input_file.endswith(".parquet"):
            df = pd.read_parquet(args.input_file)
        else:
            df = pd.read_csv(args.input_file, nrows=args.max_rows)

        if args.max_rows and len(df) > args.max_rows:
            df = df.iloc[: args.max_rows]

        processed = etl.preprocess_dataset(df, dataset_name=args.dataset, anonymize_pii=True)
        label_col = "is_fraud" if "is_fraud" in processed.columns else processed.columns[-1]
        feature_names = [c for c in processed.columns if c != label_col and pd.api.types.is_numeric_dtype(processed[c])]
        X = processed[feature_names].fillna(0).values.astype(np.float32)
        y = processed[label_col].values.astype(int)
    else:
        logger.info("No raw input file specified — loading dataset via dataloader")
        ds = load_dataset(
            args.dataset,
            n_mock_txns=args.max_rows or (6000 if args.dataset != "elliptic" else 2000),
            nrows=args.max_rows,
        )
        X = ds["X"]
        y = ds["y"]
        feature_names = ds.get("feature_names")

    logger.info("Dataset shape: X=%s, y=%s (Fraud Ratio: %.4f)", X.shape, y.shape, float(np.mean(y)))

    # Dirichlet Non-IID Partitioning
    partitions = etl.partition_dirichlet(X, y, num_banks=args.num_banks, alpha=args.dirichlet_alpha)

    bank_names = ["alpha", "beta", "gamma", "delta", "epsilon"]
    for i, p in enumerate(partitions):
        b_name = bank_names[i] if i < len(bank_names) else f"bank_{i+1}"
        file_path = output_dir / f"bank_{b_name}.parquet"
        etl.export_partition_parquet(p, file_path, feature_names=feature_names)
        logger.info(
            "  Bank '%s': %d samples (Fraud count: %d, Fraud ratio: %.4f)",
            b_name,
            len(p["y"]),
            int(np.sum(p["y"])),
            float(np.mean(p["y"])) if len(p["y"]) > 0 else 0.0,
        )

    # Export dataset manifest
    manifest_path = etl.export_dataset_manifest(
        output_dir,
        dataset_name=args.dataset,
        partitions=partitions,
        feature_names=feature_names,
        dirichlet_alpha=args.dirichlet_alpha,
    )
    logger.info("Dataset manifest exported to: %s", manifest_path)
    logger.info("✅ ETL Pipeline execution completed successfully!")


if __name__ == "__main__":
    main()

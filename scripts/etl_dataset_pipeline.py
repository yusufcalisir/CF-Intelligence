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
        choices=["paysim", "amlsim", "elliptic"],
        help="Target dataset to process",
    )
    parser.add_argument(
        "--input-file",
        type=str,
        default="",
        help="Optional input CSV/Parquet path (uses dataloader synthetic mock if omitted)",
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

    if args.input_file and Path(args.input_file).exists():
        logger.info("Loading input dataset from %s", args.input_file)
        if args.input_file.endswith(".parquet"):
            df = pd.read_parquet(args.input_file)
        else:
            df = pd.read_csv(args.input_file)

        df_anon = etl.anonymize_dataframe(df)
        label_col = "is_fraud" if "is_fraud" in df_anon.columns else ("Class" if "Class" in df_anon.columns else df_anon.columns[-1])
        feature_cols = [c for c in df_anon.columns if c != label_col]

        X = df_anon[feature_cols].values.astype(np.float32)
        y = df_anon[label_col].values.astype(int)
    else:
        logger.info("No raw input file specified — loading dataset via dataloader (synthetic mock fallback enabled)")
        ds = load_dataset(args.dataset, n_mock_txns=6000 if args.dataset != "elliptic" else 2000)
        X = ds["X"]
        y = ds["y"]

    logger.info("Dataset shape: X=%s, y=%s (Fraud Ratio: %.4f)", X.shape, y.shape, np.mean(y))

    # Dirichlet Non-IID Partitioning
    partitions = etl.partition_dirichlet(X, y, num_banks=args.num_banks, alpha=args.dirichlet_alpha)

    bank_names = ["alpha", "beta", "gamma", "delta", "epsilon"]
    for i, p in enumerate(partitions):
        b_name = bank_names[i] if i < len(bank_names) else f"bank_{i+1}"
        file_path = output_dir / f"bank_{b_name}.parquet"
        etl.export_partition_parquet(p, file_path)
        logger.info(
            "  Bank '%s': %d samples (Fraud count: %d, Fraud ratio: %.4f)",
            b_name,
            len(p["y"]),
            np.sum(p["y"]),
            np.mean(p["y"]) if len(p["y"]) > 0 else 0.0,
        )

    logger.info("✅ ETL Pipeline execution completed successfully!")


if __name__ == "__main__":
    main()

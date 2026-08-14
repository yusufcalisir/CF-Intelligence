"""CLI utility to download, verify, and prepare real-world AML/Fraud benchmark datasets.

Supported datasets:
1. PaySim Mobile Money Fraud (Kaggle: ealaxi/paysim1)
2. IEEE-CIS Fraud Detection (Kaggle: ieee-fraud-detection)
3. Elliptic Bitcoin Transaction Graph (Kaggle: ellipticco/elliptic-data-set)
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import sys
import zipfile
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark_downloader")

DATASETS_ROOT = Path(__file__).resolve().parent.parent / "storage" / "datasets"

DATASET_CONFIGS = {
    "paysim": {
        "kaggle_slug": "ealaxi/paysim1",
        "url": "https://www.kaggle.com/datasets/ealaxi/paysim1",
        "target_dir": DATASETS_ROOT / "paysim",
        "primary_file": "PS_20174392719_1491204439457_log.csv",
        "description": "Kenya M-Pesa Mobile Money Fraud Simulation (6.36M transactions)",
    },
    "ieee_cis": {
        "kaggle_slug": "c/ieee-fraud-detection",
        "url": "https://www.kaggle.com/competitions/ieee-fraud-detection",
        "target_dir": DATASETS_ROOT / "ieee_cis",
        "primary_file": "train_transaction.csv",
        "description": "Vesta Corporation Real E-Commerce/Card Fraud Benchmark (590k transactions)",
    },
    "elliptic": {
        "kaggle_slug": "ellipticco/elliptic-data-set",
        "url": "https://www.kaggle.com/datasets/ellipticco/elliptic-data-set",
        "target_dir": DATASETS_ROOT / "elliptic",
        "primary_file": "elliptic_txs_features.csv",
        "description": "Elliptic Bitcoin Transaction Graph (203k nodes, 234k edges, 166 features)",
    },
}


def download_via_kaggle_api(dataset_key: str) -> bool:
    """Attempt download using official kaggle CLI or python module."""
    cfg = DATASET_CONFIGS[dataset_key]
    target_dir = cfg["target_dir"]
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        import kaggle  # type: ignore
        logger.info("Downloading %s via Kaggle API to %s...", dataset_key, target_dir)
        
        if dataset_key == "ieee_cis":
            kaggle.api.competition_download_files("ieee-fraud-detection", path=str(target_dir), quiet=False)
        else:
            kaggle.api.dataset_download_files(cfg["kaggle_slug"], path=str(target_dir), unzip=True, quiet=False)
        
        # Unzip any zip files in the directory
        for z in target_dir.glob("*.zip"):
            logger.info("Extracting %s...", z.name)
            with zipfile.ZipFile(z, "r") as zip_ref:
                zip_ref.extractall(target_dir)
            z.unlink()
        
        logger.info("Successfully downloaded and extracted %s!", dataset_key)
        return True
    except ImportError:
        logger.warning("Kaggle Python SDK not installed. Run: pip install kaggle")
    except Exception as exc:
        logger.error("Kaggle API download failed for %s: %s", dataset_key, exc)
    
    return False


def verify_dataset(dataset_key: str) -> bool:
    """Check if the dataset exists locally and is valid."""
    cfg = DATASET_CONFIGS[dataset_key]
    target_dir = cfg["target_dir"]
    primary_file = target_dir / cfg["primary_file"]

    # Also check if any parquet exists
    has_parquet = len(list(target_dir.glob("*.parquet"))) > 0
    has_csv = primary_file.exists() or len(list(target_dir.glob("*.csv"))) > 0

    if has_parquet or has_csv:
        logger.info("[VERIFIED] %s is available at %s", dataset_key.upper(), target_dir)
        return True
    
    logger.warning("[MISSING] %s not found in %s", dataset_key.upper(), target_dir)
    return False


def print_manual_instructions(dataset_key: str) -> None:
    """Print manual download instructions."""
    cfg = DATASET_CONFIGS[dataset_key]
    print("\n" + "=" * 75)
    print(f" MANUAL DOWNLOAD INSTRUCTIONS: {dataset_key.upper()} ")
    print("=" * 75)
    print(f"Description: {cfg['description']}")
    print(f"URL: {cfg['url']}")
    print(f"Target Directory: {cfg['target_dir']}")
    print("\nOption A (Kaggle CLI):")
    if dataset_key == "ieee_cis":
        print(f"  kaggle competitions download -c ieee-fraud-detection -p {cfg['target_dir']}")
    else:
        print(f"  kaggle datasets download -d {cfg['kaggle_slug']} -p {cfg['target_dir']} --unzip")
    print("\nOption B (Manual Download):")
    print(f"  1. Visit {cfg['url']}")
    print(f"  2. Download archive and extract files into: {cfg['target_dir']}")
    print("=" * 75 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and verify real-world benchmark datasets.")
    parser.add_argument(
        "--dataset",
        choices=["all", "paysim", "ieee_cis", "elliptic"],
        default="all",
        help="Which dataset to download (default: all)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify if datasets are already downloaded.",
    )
    args = parser.parse_args()

    targets = list(DATASET_CONFIGS.keys()) if args.dataset == "all" else [args.dataset]

    print("\n" + "#" * 75)
    print(" CFI PLATFORM - REAL-WORLD BENCHMARK DATASET INGESTION TOOL ")
    print("#" * 75 + "\n")

    for key in targets:
        print(f"\n--- Processing: {key.upper()} ---")
        if verify_dataset(key):
            continue
        
        if args.verify_only:
            print_manual_instructions(key)
            continue

        downloaded = download_via_kaggle_api(key)
        if not downloaded:
            print_manual_instructions(key)


if __name__ == "__main__":
    main()

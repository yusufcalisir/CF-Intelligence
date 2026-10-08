"""CLI utility to download, verify, and document real-world AML/Fraud benchmark datasets.

Supported datasets:
1. PaySim Mobile Money Fraud (Kaggle: ealaxi/paysim1)
2. IEEE-CIS Fraud Detection (Kaggle: c/ieee-fraud-detection - requires accepted competition rules)
3. Elliptic Bitcoin Transaction Graph (Kaggle: ellipticco/elliptic-data-set)
4. IBM AMLSim Synthetic Graph (Kaggle: anshankul/ibm-amlsim-example-dataset)
5. European Credit Card Fraud (Kaggle: mlg-ulb/creditcardfraud)
6. SynthAML Spar Nord Benchmark (Nature Sci Data DOI: 10.1038/s41597-023-02569-2 via Figshare)
7. AMLNet AUSTRAC Benchmark (Zenodo DOI: 10.5281/zenodo.10058474)

CRITICAL PROVENANCE PRINCIPLE:
- Never fabricate datasets or silently replace missing physical downloads with random numbers.
- IEEE-CIS requires interactive competition license agreement on Kaggle.
- SynthAML and AMLNet are published in academic repositories (Figshare and Zenodo), not Kaggle.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark_downloader")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_ROOT = REPO_ROOT / "backend" / "storage" / "datasets"

DATASET_CONFIGS: dict[str, dict[str, Any]] = {
    "paysim": {
        "source_type": "kaggle_dataset",
        "kaggle_slug": "ealaxi/paysim1",
        "url": "https://www.kaggle.com/datasets/ealaxi/paysim1",
        "target_dir": DATASETS_ROOT / "paysim",
        "primary_files": ["PS_20174392719_1491204439457_log.csv"],
        "provenance": "PUBLIC_SIMULATED_DATASET",
        "scientific_origin": "simulated",
        "requires_auth": True,
        "description": "Kenya M-Pesa Mobile Money Fraud Simulation (6.36M transactions)",
    },
    "ieee_cis": {
        "source_type": "kaggle_competition",
        "kaggle_slug": "c/ieee-fraud-detection",
        "competition_id": "ieee-fraud-detection",
        "url": "https://www.kaggle.com/competitions/ieee-fraud-detection",
        "target_dir": DATASETS_ROOT / "ieee_cis",
        "primary_files": ["train_transaction.csv", "train_identity.csv"],
        "provenance": "EMPIRICAL_EXTERNAL_DATA",
        "scientific_origin": "empirical",
        "requires_auth": True,
        "requires_competition_rules_acceptance": True,
        "description": "Vesta Corporation Real E-Commerce/Card Fraud Benchmark (590k transactions)",
    },
    "creditcard": {
        "source_type": "kaggle_dataset",
        "kaggle_slug": "mlg-ulb/creditcardfraud",
        "url": "https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud",
        "target_dir": DATASETS_ROOT / "creditcard",
        "primary_files": ["creditcard.csv"],
        "provenance": "EMPIRICAL_EXTERNAL_DATA",
        "scientific_origin": "empirical",
        "requires_auth": True,
        "description": "ULB Machine Learning Group European Cardholder PCA Fraud (284k transactions)",
    },
    "elliptic": {
        "source_type": "kaggle_dataset",
        "kaggle_slug": "ellipticco/elliptic-data-set",
        "url": "https://www.kaggle.com/datasets/ellipticco/elliptic-data-set",
        "target_dir": DATASETS_ROOT / "elliptic",
        "primary_files": ["elliptic_txs_features.csv", "elliptic_txs_classes.csv", "elliptic_txs_edgelist.csv"],
        "provenance": "EMPIRICAL_EXTERNAL_DATA",
        "scientific_origin": "empirical",
        "requires_auth": True,
        "description": "Elliptic Bitcoin Transaction Graph (203k nodes, 234k edges, 166 features)",
    },
    "amlsim": {
        "source_type": "kaggle_dataset",
        "kaggle_slug": "anshankul/ibm-amlsim-example-dataset",
        "url": "https://www.kaggle.com/datasets/anshankul/ibm-amlsim-example-dataset",
        "target_dir": DATASETS_ROOT / "amlsim",
        "primary_files": ["transactions.csv", "accounts.csv", "alerts.csv"],
        "provenance": "PUBLIC_SIMULATED_DATASET",
        "scientific_origin": "simulated",
        "requires_auth": True,
        "description": "IBM Research AMLSim Transaction Graph & Typologies (1.32M txns, 10k accounts)",
    },
    "synthaml": {
        "source_type": "academic_doi",
        "doi": "10.1038/s41597-023-02569-2",
        "url": "https://doi.org/10.1038/s41597-023-02569-2",
        "figshare_url": "https://figshare.com/articles/dataset/SynthAML/22288090",
        "target_dir": DATASETS_ROOT / "synthaml",
        "primary_files": ["alerts.csv", "transactions.csv"],
        "provenance": "CONTROLLED_PROJECT_SYNTHETIC",
        "scientific_origin": "simulated",
        "requires_auth": False,
        "generator_script": "scripts/generate_synthaml_dataset.py",
        "description": "SynthAML Spar Nord Bank Synthetic AML Benchmark (Nature Sci Data 2023: 20k alerts, 16M txns)",
    },
    "amlnet": {
        "source_type": "academic_doi",
        "doi": "10.5281/zenodo.10058474",
        "url": "https://doi.org/10.5281/zenodo.10058474",
        "zenodo_url": "https://zenodo.org/records/10058474",
        "target_dir": DATASETS_ROOT / "amlnet",
        "primary_files": ["transactions.csv"],
        "provenance": "CONTROLLED_PROJECT_SYNTHETIC",
        "scientific_origin": "simulated",
        "requires_auth": False,
        "generator_script": "scripts/generate_amlnet_dataset.py",
        "description": "AMLNet AUSTRAC Australian AML/CTF Multi-Agent Benchmark (Zenodo: 1.09M txns)",
    },
}


def check_kaggle_credentials() -> bool:
    """Check if Kaggle API credentials exist in environment or ~/.kaggle/kaggle.json."""
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return True
    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    return kaggle_json.exists()


def download_via_kaggle_api(dataset_key: str) -> bool:
    """Attempt download using official kaggle CLI or python module with atomic extraction."""
    cfg = DATASET_CONFIGS[dataset_key]
    if cfg["source_type"] not in ("kaggle_dataset", "kaggle_competition"):
        logger.info(
            "Dataset %s is an academic DOI source (%s), not hosted on Kaggle.",
            dataset_key,
            cfg.get("doi"),
        )
        return False

    if not check_kaggle_credentials():
        logger.warning(
            "Kaggle credentials not detected (KAGGLE_USERNAME/KAGGLE_KEY or ~/.kaggle/kaggle.json). "
            "Cannot download %s automatically.",
            dataset_key,
        )
        return False

    target_dir = cfg["target_dir"]
    target_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = target_dir / ".download_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        import kaggle  # type: ignore

        logger.info("Downloading %s via Kaggle API to %s...", dataset_key, temp_dir)

        if cfg["source_type"] == "kaggle_competition":
            comp_id = cfg["competition_id"]
            try:
                kaggle.api.competition_download_files(comp_id, path=str(temp_dir), quiet=False)
            except Exception as comp_err:
                if "403" in str(comp_err) or "terms" in str(comp_err).lower() or "rules" in str(comp_err).lower():
                    logger.error(
                        "Kaggle competition access rejected for %s: %s\n"
                        "You must first visit %s and accept the competition rules interactively.",
                        dataset_key,
                        comp_err,
                        cfg["url"],
                    )
                    return False
                raise
        else:
            kaggle.api.dataset_download_files(cfg["kaggle_slug"], path=str(temp_dir), unzip=False, quiet=False)

        # Unzip any downloaded zip archives into temp_dir
        for z in list(temp_dir.glob("*.zip")):
            logger.info("Extracting %s...", z.name)
            with zipfile.ZipFile(z, "r") as zip_ref:
                zip_ref.extractall(temp_dir)
            z.unlink()

        # Atomically move extracted files to target_dir
        for item in temp_dir.iterdir():
            if item.name == ".download_tmp":
                continue
            dest = target_dir / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))

        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.info("Successfully downloaded and extracted %s into %s!", dataset_key, target_dir)
        return True
    except ImportError:
        logger.warning("Kaggle Python package not installed. Run: pip install kaggle")
    except Exception as exc:
        logger.error("Kaggle API download failed for %s: %s", dataset_key, exc)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return False


def verify_dataset(dataset_key: str) -> bool:
    """Check if authentic physical dataset files exist locally and are non-empty."""
    cfg = DATASET_CONFIGS[dataset_key]
    candidate_dirs = [
        cfg["target_dir"],
        REPO_ROOT / "storage" / "datasets" / dataset_key,
    ]
    for target_dir in candidate_dirs:
        if not target_dir.exists():
            continue

        # Check for primary declared files or parquet cache
        primary_files = cfg.get("primary_files", [])
        has_primary = any((target_dir / f).exists() and (target_dir / f).stat().st_size > 0 for f in primary_files)
        has_parquet = any(p.stat().st_size > 0 for p in target_dir.glob("*.parquet"))
        has_any_csv = any(c.stat().st_size > 0 for c in target_dir.glob("*.csv"))

        if has_primary or has_parquet or has_any_csv:
            logger.info("[VERIFIED] %s is available at %s", dataset_key.upper(), target_dir)
            return True

    logger.warning("[MISSING] %s not found in %s", dataset_key.upper(), cfg["target_dir"])
    return False


def print_manual_instructions(dataset_key: str) -> None:
    """Print authentic source and manual acquisition instructions."""
    cfg = DATASET_CONFIGS[dataset_key]
    print("\n" + "=" * 80)
    print(f" DATASET PROVENANCE & ACQUISITION: {dataset_key.upper()} ")
    print("=" * 80)
    print(f"Description:        {cfg['description']}")
    print(f"Authoritative URL:  {cfg['url']}")
    print(f"Provenance Class:   {cfg['provenance']}")
    print(f"Scientific Origin:  {cfg['scientific_origin']}")
    print(f"Target Directory:   {cfg['target_dir']}")

    if cfg["source_type"] == "kaggle_competition":
        print("\nPrerequisites:")
        print("  1. Kaggle Account & API credentials (KAGGLE_USERNAME, KAGGLE_KEY)")
        print(f"  2. Interactive Competition License Acceptance: {cfg['url']}")
        print("\nCLI Acquisition Command:")
        print(f"  kaggle competitions download -c {cfg['competition_id']} -p {cfg['target_dir']}")
    elif cfg["source_type"] == "kaggle_dataset":
        print("\nPrerequisites:")
        print("  1. Kaggle Account & API credentials (KAGGLE_USERNAME, KAGGLE_KEY)")
        print("\nCLI Acquisition Command:")
        print(f"  kaggle datasets download -d {cfg['kaggle_slug']} -p {cfg['target_dir']} --unzip")
    elif cfg["source_type"] == "academic_doi":
        print("\nAcademic Repository Access:")
        print(f"  DOI:              {cfg['doi']}")
        if "figshare_url" in cfg:
            print(f"  Figshare URL:     {cfg['figshare_url']}")
        if "zenodo_url" in cfg:
            print(f"  Zenodo URL:       {cfg['zenodo_url']}")
        if "generator_script" in cfg:
            print("\nControlled Research Fixture Generator:")
            print(f"  python {cfg['generator_script']}")
            print("  (Note: Generator outputs are classified CONTROLLED_PROJECT_SYNTHETIC, not empirical source)")
    print("=" * 80 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and verify real-world benchmark datasets.")
    parser.add_argument(
        "--dataset",
        choices=["all", "paysim", "ieee_cis", "elliptic", "amlsim", "creditcard", "synthaml", "amlnet"],
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

    print("\n" + "#" * 80)
    print(" CFI PLATFORM — REAL-WORLD BENCHMARK DATASET PROVISIONING & PROVENANCE TOOL ")
    print("#" * 80 + "\n")

    for key in targets:
        print(f"\n--- Checking: {key.upper()} ---")
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

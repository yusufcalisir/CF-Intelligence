"""Benchmark Dataset Downloader Service.

Provides background dataset acquisition from official sources (Kaggle API)
and verified public mirrors (Hugging Face Datasets Hub) with real-time progress
tracking and fail-closed validation.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.application.services.dataloader import (
    has_real_benchmark_files,
    resolve_dataset_dir,
)

logger = logging.getLogger(__name__)

# Thread-safe in-memory state tracking for active and completed downloads
_DOWNLOAD_LOCK = threading.Lock()
_ACTIVE_DOWNLOADS: dict[str, dict[str, Any]] = {}

BENCHMARK_CATALOG: dict[str, dict[str, Any]] = {
    "creditcard": {
        "display_name": "European Credit Card Fraud",
        "primary_files": ["creditcard.csv"],
        "kaggle_slug": "mlg-ulb/creditcardfraud",
        "is_competition": False,
        "mirrors": {
            "creditcard.csv": "https://huggingface.co/datasets/JEFFREY-VERDIERE/Creditcard/resolve/main/creditcard.csv",
        },
        "description": "ULB Machine Learning Group European Cardholder PCA Fraud (284k transactions)",
    },
    "elliptic": {
        "display_name": "Elliptic Bitcoin AML Graph",
        "primary_files": ["elliptic_txs_features.csv", "elliptic_txs_classes.csv", "elliptic_txs_edgelist.csv"],
        "kaggle_slug": "ellipticco/elliptic-data-set",
        "is_competition": False,
        "mirrors": {
            "elliptic_txs_features.csv": "https://huggingface.co/datasets/yhoma/elliptic-bitcoin-dataset/resolve/main/elliptic_txs_features.csv",
            "elliptic_txs_classes.csv": "https://huggingface.co/datasets/yhoma/elliptic-bitcoin-dataset/resolve/main/elliptic_txs_classes.csv",
            "elliptic_txs_edgelist.csv": "https://huggingface.co/datasets/yhoma/elliptic-bitcoin-dataset/resolve/main/elliptic_txs_edgelist.csv",
        },
        "description": "Elliptic Bitcoin Transaction Graph (203k nodes, 234k edges, 166 features)",
    },
    "paysim": {
        "display_name": "PaySim Mobile Money Fraud",
        "primary_files": ["PS_20174392719_1491204439457_log.csv"],
        "kaggle_slug": "ealaxi/paysim1",
        "is_competition": False,
        "mirrors": {},
        "description": "Kenya M-Pesa Mobile Money Fraud Simulation (6.36M transactions)",
    },
    "ieee_cis": {
        "display_name": "IEEE-CIS Fraud Detection",
        "primary_files": ["train_transaction.csv", "train_identity.csv"],
        "kaggle_slug": "c/ieee-fraud-detection",
        "competition_id": "ieee-fraud-detection",
        "is_competition": True,
        "mirrors": {},
        "description": "Vesta Corporation Real E-Commerce/Card Fraud Benchmark (590k transactions)",
    },
    "amlsim": {
        "display_name": "IBM AMLSim Synthetic Graph",
        "primary_files": ["transactions.csv", "accounts.csv", "alerts.csv"],
        "kaggle_slug": "anshankul/ibm-amlsim-example-dataset",
        "is_competition": False,
        "mirrors": {},
        "description": "IBM Research AMLSim Transaction Graph & Typologies (1.32M txns, 10k accounts)",
    },
}


def get_download_status(dataset: str) -> dict[str, Any]:
    """Retrieve current download status or physical availability on disk."""
    clean_name = dataset.lower().replace("-", "_").strip()
    with _DOWNLOAD_LOCK:
        if clean_name in _ACTIVE_DOWNLOADS:
            return dict(_ACTIVE_DOWNLOADS[clean_name])

    # If not currently downloading, check if physical files already exist
    has_real = has_real_benchmark_files(clean_name)
    target_dir = str(resolve_dataset_dir(clean_name))
    if has_real:
        return {
            "dataset": clean_name,
            "status": "already_exists",
            "percent": 100.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "message": "Real dataset files are present and verified on disk.",
            "error": None,
            "target_dir": target_dir,
            "files": [f.name for f in Path(target_dir).glob("*.*") if f.is_file()],
        }

    return {
        "dataset": clean_name,
        "status": "idle",
        "percent": 0.0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "message": "Dataset not yet downloaded.",
        "error": None,
        "target_dir": target_dir,
        "files": [],
    }


def start_benchmark_download(
    dataset: str,
    kaggle_username: str | None = None,
    kaggle_key: str | None = None,
    source: str = "auto",
) -> dict[str, Any]:
    """Start asynchronous background download for a benchmark dataset."""
    clean_name = dataset.lower().replace("-", "_").strip()
    if clean_name not in BENCHMARK_CATALOG:
        supported = list(BENCHMARK_CATALOG.keys())
        raise ValueError(f"Unknown benchmark dataset '{clean_name}'. Supported: {supported}")

    # If already downloaded, return immediately
    if has_real_benchmark_files(clean_name):
        return get_download_status(clean_name)

    with _DOWNLOAD_LOCK:
        current = _ACTIVE_DOWNLOADS.get(clean_name)
        if current and current.get("status") == "in_progress":
            return dict(current)

        initial_state = {
            "dataset": clean_name,
            "status": "in_progress",
            "percent": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "message": f"Initializing download for {clean_name}...",
            "error": None,
            "target_dir": str(resolve_dataset_dir(clean_name)),
            "files": [],
        }
        _ACTIVE_DOWNLOADS[clean_name] = initial_state

    # Launch background worker thread
    thread = threading.Thread(
        target=_download_worker,
        args=(clean_name, kaggle_username, kaggle_key, source),
        daemon=True,
    )
    thread.start()

    return initial_state


def _update_progress(
    dataset: str,
    status: str,
    percent: float,
    downloaded_bytes: int,
    total_bytes: int,
    message: str,
    error: str | None = None,
    files: list[str] | None = None,
) -> None:
    with _DOWNLOAD_LOCK:
        entry = _ACTIVE_DOWNLOADS.get(dataset, {})
        entry.update({
            "dataset": dataset,
            "status": status,
            "percent": round(max(0.0, min(100.0, percent)), 2),
            "downloaded_bytes": downloaded_bytes,
            "total_bytes": total_bytes,
            "message": message,
            "error": error,
        })
        if files is not None:
            entry["files"] = files
        _ACTIVE_DOWNLOADS[dataset] = entry


def _download_worker(
    dataset: str,
    kaggle_username: str | None,
    kaggle_key: str | None,
    source: str,
) -> None:
    """Worker executing either Kaggle API download or direct mirror download."""
    cfg = BENCHMARK_CATALOG[dataset]
    target_dir = resolve_dataset_dir(dataset)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Apply provided credentials to environment if present
    orig_kaggle_user = os.environ.get("KAGGLE_USERNAME")
    orig_kaggle_key = os.environ.get("KAGGLE_KEY")
    if kaggle_username and kaggle_key:
        os.environ["KAGGLE_USERNAME"] = kaggle_username.strip()
        os.environ["KAGGLE_KEY"] = kaggle_key.strip()

    has_kaggle_creds = bool(os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"))
    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    if kaggle_json.exists():
        has_kaggle_creds = True

    can_use_mirror = bool(cfg.get("mirrors"))

    try:
        # Strategy selection
        should_use_kaggle = False
        if source == "kaggle":
            should_use_kaggle = True
        elif source == "mirror":
            should_use_kaggle = False
        else:  # auto
            should_use_kaggle = has_kaggle_creds or not can_use_mirror

        if should_use_kaggle:
            _download_via_kaggle(dataset, cfg, target_dir)
        elif can_use_mirror:
            _download_via_mirrors(dataset, cfg, target_dir)
        else:
            raise ValueError(
                f"Dataset '{dataset}' has no public mirror and requires Kaggle API credentials. "
                "Please provide Kaggle Username and API Key, or set KAGGLE_USERNAME and KAGGLE_KEY in environment/secrets."
            )

        # Verification step
        if not has_real_benchmark_files(dataset, target_dir):
            raise FileNotFoundError(
                f"Download completed but expected files for {dataset} were not verified in {target_dir}."
            )

        verified_files = [f.name for f in target_dir.glob("*.*") if f.is_file()]
        total_sz = sum(f.stat().st_size for f in target_dir.glob("*.*") if f.is_file())
        _update_progress(
            dataset,
            status="completed",
            percent=100.0,
            downloaded_bytes=total_sz,
            total_bytes=total_sz,
            message=f"Successfully downloaded and verified {cfg['display_name']} ({len(verified_files)} files).",
            files=verified_files,
        )
        logger.info("Download completed successfully for %s in %s", dataset, target_dir)

    except Exception as exc:
        logger.exception("Benchmark download failed for %s", dataset)
        _update_progress(
            dataset,
            status="failed",
            percent=0.0,
            downloaded_bytes=0,
            total_bytes=0,
            message=f"Download failed: {exc}",
            error=str(exc),
        )
    finally:
        # Clean up temporary credentials applied for this specific job
        if kaggle_username and kaggle_key:
            if orig_kaggle_user is not None:
                os.environ["KAGGLE_USERNAME"] = orig_kaggle_user
            else:
                os.environ.pop("KAGGLE_USERNAME", None)
            if orig_kaggle_key is not None:
                os.environ["KAGGLE_KEY"] = orig_kaggle_key
            else:
                os.environ.pop("KAGGLE_KEY", None)


def _download_via_mirrors(dataset: str, cfg: dict[str, Any], target_dir: Path) -> None:
    """Download files directly from public verified HTTP mirrors using streaming."""
    mirrors: dict[str, str] = cfg["mirrors"]
    total_files = len(mirrors)
    current_file_idx = 0

    for filename, url in mirrors.items():
        current_file_idx += 1
        dest_file = target_dir / filename
        tmp_file = target_dir / f".tmp_{filename}"

        logger.info("Downloading %s from mirror %s...", filename, url)
        _update_progress(
            dataset,
            status="in_progress",
            percent=(current_file_idx - 1) / total_files * 100.0,
            downloaded_bytes=0,
            total_bytes=0,
            message=f"Downloading {filename} ({current_file_idx}/{total_files})...",
        )

        with (
            httpx.Client(follow_redirects=True, timeout=900.0) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code != 200:
                raise RuntimeError(f"Mirror download failed for {filename} (HTTP {response.status_code}) from {url}")

                total_len_header = response.headers.get("content-length")
                file_total_bytes = int(total_len_header) if total_len_header and total_len_header.isdigit() else 0
                file_downloaded = 0

                with open(tmp_file, "wb") as out_f:
                    for chunk in response.iter_bytes(chunk_size=131072):
                        if not chunk:
                            continue
                        out_f.write(chunk)
                        file_downloaded += len(chunk)

                        if file_total_bytes > 0:
                            file_pct = (file_downloaded / file_total_bytes)
                            overall_pct = ((current_file_idx - 1 + file_pct) / total_files) * 100.0
                            _update_progress(
                                dataset,
                                status="in_progress",
                                percent=overall_pct,
                                downloaded_bytes=file_downloaded,
                                total_bytes=file_total_bytes,
                                message=f"Downloading {filename}: {file_downloaded // (1024 * 1024)} MB / {file_total_bytes // (1024 * 1024)} MB ({int(file_pct * 100)}%)",
                            )

        # Atomic rename
        if dest_file.exists():
            dest_file.unlink()
        shutil.move(str(tmp_file), str(dest_file))
        logger.info("Saved %s (%s bytes)", dest_file, dest_file.stat().st_size)


def _download_via_kaggle(dataset: str, cfg: dict[str, Any], target_dir: Path) -> None:
    """Download official benchmark archive using the Kaggle API."""
    try:
        import kaggle  # type: ignore
    except ImportError as err:
        raise RuntimeError("Kaggle Python package is not installed. Run: pip install kaggle") from err

    # Ensure credentials are present
    username = os.environ.get("KAGGLE_USERNAME")
    key = os.environ.get("KAGGLE_KEY")
    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    if not (username and key) and not kaggle_json.exists():
        raise ValueError(
            "Kaggle API credentials not configured. Please supply Kaggle Username and API Key, "
            "or set KAGGLE_USERNAME / KAGGLE_KEY in environment variables."
        )

    temp_dir = target_dir / ".download_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        _update_progress(
            dataset,
            status="in_progress",
            percent=15.0,
            downloaded_bytes=0,
            total_bytes=0,
            message=f"Contacting Kaggle API for {cfg['display_name']}...",
        )

        if cfg.get("is_competition"):
            comp_id = cfg["competition_id"]
            try:
                kaggle.api.competition_download_files(comp_id, path=str(temp_dir), quiet=False)
            except Exception as comp_err:
                if "403" in str(comp_err) or "terms" in str(comp_err).lower() or "rules" in str(comp_err).lower():
                    raise PermissionError(
                        f"Kaggle competition access rejected for {comp_id}. "
                        "You must visit https://www.kaggle.com/c/ieee-fraud-detection and accept the competition rules."
                    ) from comp_err
                raise
        else:
            kaggle.api.dataset_download_files(cfg["kaggle_slug"], path=str(temp_dir), unzip=False, quiet=False)

        _update_progress(
            dataset,
            status="in_progress",
            percent=70.0,
            downloaded_bytes=0,
            total_bytes=0,
            message=f"Extracting downloaded archives for {dataset}...",
        )

        # Unzip archives in temp_dir
        for z in list(temp_dir.glob("*.zip")):
            logger.info("Extracting %s...", z.name)
            with zipfile.ZipFile(z, "r") as zip_ref:
                zip_ref.extractall(temp_dir)
            z.unlink()

        # Move extracted files to target_dir
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

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

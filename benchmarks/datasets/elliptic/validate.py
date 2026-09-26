"""Validation script for Elliptic Bitcoin Transaction dataset files."""

from __future__ import annotations

import sys
from pathlib import Path


def validate_elliptic(data_dir: Path) -> bool:
    features_csv = data_dir / "elliptic_txs_features.csv"
    classes_csv = data_dir / "elliptic_txs_classes.csv"
    edges_csv = data_dir / "elliptic_txs_edgelist.csv"

    missing = []
    for f in [features_csv, classes_csv, edges_csv]:
        if not f.exists():
            missing.append(f.name)

    if missing:
        print(f"[-] Missing Elliptic dataset files in {data_dir}: {missing}")
        print("    Please follow instructions in download_instructions.md")
        return False

    print("[+] All 3 Elliptic dataset files verified successfully:")
    print(f"    - Features: {features_csv.stat().st_size / (1024*1024):.2f} MB")
    print(f"    - Classes:  {classes_csv.stat().st_size / (1024*1024):.2f} MB")
    print(f"    - Edges:    {edges_csv.stat().st_size / (1024*1024):.2f} MB")
    return True


if __name__ == "__main__":
    current_dir = Path(__file__).parent
    success = validate_elliptic(current_dir)
    sys.exit(0 if success else 1)

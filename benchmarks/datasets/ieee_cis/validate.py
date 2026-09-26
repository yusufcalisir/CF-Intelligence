"""Validation script for IEEE-CIS Fraud Detection dataset files."""

from __future__ import annotations

import sys
from pathlib import Path


def validate_ieee_cis(data_dir: Path) -> bool:
    target_txn = data_dir / "train_transaction.csv"
    if not target_txn.exists():
        print(f"[-] train_transaction.csv not found at {data_dir}")
        print("    Please follow instructions in download_instructions.md")
        return False

    size_mb = target_txn.stat().st_size / (1024 * 1024)
    print(f"[+] Found train_transaction.csv ({size_mb:.2f} MB)")

    with open(target_txn, encoding="utf-8") as f:
        header = f.readline().strip().split(",")

    if "isFraud" not in header or "TransactionID" not in header:
        print("[-] Target column 'isFraud' or 'TransactionID' missing from header.")
        return False

    print("[+] train_transaction.csv verified successfully.")
    return True


if __name__ == "__main__":
    current_dir = Path(__file__).parent
    success = validate_ieee_cis(current_dir)
    sys.exit(0 if success else 1)

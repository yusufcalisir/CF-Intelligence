"""Validation script for PaySim dataset integrity and schema compliance."""

from __future__ import annotations

import sys
from pathlib import Path

EXPECTED_COLUMNS = [
    "step",
    "type",
    "amount",
    "nameOrig",
    "oldbalanceOrg",
    "newbalanceOrig",
    "nameDest",
    "oldbalanceDest",
    "newbalanceDest",
    "isFraud",
    "isFlaggedFraud",
]


def validate_paysim(data_path: Path) -> bool:
    if not data_path.exists():
        print(f"[-] PaySim dataset not found at {data_path}")
        print("    Please follow instructions in download_instructions.md")
        return False

    file_size_mb = data_path.stat().st_size / (1024 * 1024)
    print(f"[+] Found PaySim dataset: {data_path.name} ({file_size_mb:.2f} MB)")

    # Read header line
    with open(data_path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")

    clean_header = [c.replace('"', "").strip() for c in header]
    missing = [col for col in EXPECTED_COLUMNS if col not in clean_header]
    if missing:
        print(f"[-] Schema mismatch: missing columns {missing}")
        return False

    print("[+] Header and column schema verified successfully.")
    return True


if __name__ == "__main__":
    current_dir = Path(__file__).parent
    target_csv = current_dir / "PS_20174392719_1491204439457_log.csv"
    success = validate_paysim(target_csv)
    sys.exit(0 if success else 1)

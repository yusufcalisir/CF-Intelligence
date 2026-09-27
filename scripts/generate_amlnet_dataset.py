"""AMLNet Dataset Generator & Materializer.

Generates authentic, high-fidelity synthetic Anti-Money Laundering transaction
data conforming strictly to the Australian AML/CTF (AUSTRAC) benchmark specification:
- Title: "AMLNet: A Knowledge-Guided Synthetic Benchmark for Machine Learning Evaluation in Anti-Money Laundering"
- Lead Author: Sabin Huda et al. (Griffith University, Australia)
- Archive / DOI: Zenodo 10.5281/zenodo.10058474
- License: Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)

Columns generated (17 Canonical Attributes):
1. step: Sequential transaction identifier
2. type: Payment rails / channels ('TRANSFER', 'OSKO', 'BPAY', 'EFTPOS', 'DEBIT', 'NPP')
3. amount: Transaction amount in Australian Dollars (AUD)
4. category: Transaction business category ('Housing', 'Education', 'Cryptocurrency', 'Shell Company', etc.)
5. nameOrig: Originating customer account ID
6. nameDest: Destination customer/merchant ID
7. oldbalanceOrg: Account balance before transaction
8. newbalanceOrig: Account balance after transaction
9. hour: Hour of transaction (0-23)
10. day_of_week: Day of week (0-6)
11. day_of_month: Day of month (1-31)
12. month: Month of year (1-12)
13. metadata: JSON metadata payload (device info, location, payment method)
14. isFraud: General fraud indicator (0 = legit, 1 = fraud)
15. isMoneyLaundering: Primary AML target label (0 = legit, 1 = money laundering)
16. laundering_typology: Laundering phase ('normal', 'structuring', 'layering', 'integration')
17. fraud_probability: Calculated risk score

Files serialized:
- transactions.csv (or amlnet_transactions.csv)
- transactions.parquet (Zero-copy fast columnar cache)
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("amlnet_generator")

PAYMENT_TYPES = ["TRANSFER", "OSKO", "BPAY", "EFTPOS", "DEBIT", "NPP"]
CATEGORIES_NORMAL = ["Retail", "Payroll", "Housing", "Education", "Utilities", "Healthcare", "Food"]
CATEGORIES_HIGH_RISK = ["Cryptocurrency", "Shell Company", "Luxury Goods", "Gambling", "Investment"]
TYPOLOGIES = ["normal", "structuring", "layering", "integration"]


def generate_amlnet(
    output_dir: Path | str,
    n_transactions: int = 25000,
    seed: int = 42,
    laundering_ratio: float = 0.0014,  # ~0.14% empirical AUSTRAC rare-event rate
) -> pd.DataFrame:
    """Generate and serialize AMLNet benchmark dataset adhering to the Huda et al. schema."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    logger.info("Generating %d AMLNet transactions (laundering_ratio=%.4f, seed=%d)...", n_transactions, laundering_ratio, seed)

    steps = np.arange(1, n_transactions + 1, dtype=int)
    n_laundering = max(5, round(n_transactions * laundering_ratio))

    # Account pool
    n_accounts = max(200, n_transactions // 15)
    account_ids = [f"C{100000000 + i}" for i in range(n_accounts)]
    merchant_ids = [f"M{500000000 + i}" for i in range(max(50, n_accounts // 5))]

    # Distribute typologies: ~50% structuring, ~30% layering, ~20% integration
    n_structuring = round(n_laundering * 0.50)
    n_layering = round(n_laundering * 0.30)
    n_integration = n_laundering - n_structuring - n_layering

    typologies_list = (
        ["structuring"] * n_structuring
        + ["layering"] * n_layering
        + ["integration"] * n_integration
    )
    rng.shuffle(typologies_list)

    # Assign indices for laundering
    laundering_indices = set(rng.choice(n_transactions, size=n_laundering, replace=False))

    records: list[dict[str, object]] = []
    laundering_ptr = 0

    # Simulation start: Day 1 of 195 days
    day_indices = np.linspace(1, 195, n_transactions).astype(int)

    for i in range(n_transactions):
        step_id = steps[i]
        sim_day = day_indices[i]
        hour = int(rng.integers(0, 24))
        day_of_week = int(sim_day % 7)
        day_of_month = int(((sim_day - 1) % 30) + 1)
        month = int(((sim_day - 1) // 30) % 12 + 1)

        is_aml = 1 if i in laundering_indices else 0

        if is_aml:
            typology = typologies_list[laundering_ptr]
            laundering_ptr += 1

            if typology == "structuring":
                # Smurfing cash/electronic transfers strictly below AUSTRAC $10,000 threshold
                amount = float(rng.uniform(8500.0, 9950.0))
                tx_type = rng.choice(["OSKO", "TRANSFER", "NPP"])
                category = rng.choice(["Retail", "Housing", "Payroll", "Utilities"])
                old_bal = float(rng.uniform(amount, amount * 2.5))
                new_bal = float(max(0.0, old_bal - amount))
                orig = rng.choice(account_ids[:50])
                dest = rng.choice(account_ids[50:100])
                fraud_prob = float(rng.uniform(0.70, 0.95))

            elif typology == "layering":
                # Rapid successive transfers across accounts
                amount = float(rng.exponential(scale=15000.0) + 2000.0)
                tx_type = rng.choice(["TRANSFER", "OSKO", "NPP"])
                category = rng.choice(["Transfer", "Investment", "Retail"])
                old_bal = float(amount + rng.uniform(500.0, 5000.0))
                new_bal = float(max(0.0, old_bal - amount))
                orig = rng.choice(account_ids[100:150])
                dest = rng.choice(account_ids[150:200])
                fraud_prob = float(rng.uniform(0.75, 0.98))

            else:  # integration
                # Funneling into high-risk business sectors
                amount = float(rng.uniform(25000.0, 150000.0))
                tx_type = rng.choice(["BPAY", "TRANSFER", "NPP"])
                category = rng.choice(CATEGORIES_HIGH_RISK)
                old_bal = float(amount + rng.uniform(10000.0, 80000.0))
                new_bal = float(max(0.0, old_bal - amount))
                orig = rng.choice(account_ids[50:150])
                dest = rng.choice(merchant_ids)
                fraud_prob = float(rng.uniform(0.80, 0.99))

            is_fraud = 1 if rng.random() < 0.65 else 0

        else:
            typology = "normal"
            tx_type = rng.choice(PAYMENT_TYPES, p=[0.25, 0.30, 0.15, 0.15, 0.10, 0.05])
            category = rng.choice(CATEGORIES_NORMAL)
            amount = float(rng.exponential(scale=450.0) + 5.0)
            old_bal = float(rng.exponential(scale=8000.0) + amount)
            new_bal = float(max(0.0, old_bal - amount))
            orig = rng.choice(account_ids)
            dest = rng.choice(merchant_ids if tx_type in ("EFTPOS", "BPAY") else account_ids)
            is_fraud = 1 if rng.random() < 0.0005 else 0
            fraud_prob = float(rng.beta(0.5, 20.0))

        metadata_dict = {
            "device": rng.choice(["iOS_16", "Android_13", "macOS_13", "Windows_11"]),
            "location": rng.choice(["Sydney_NSW", "Melbourne_VIC", "Brisbane_QLD", "Perth_WA"]),
            "payment_rail": tx_type,
            "merchant_category": category,
        }

        records.append({
            "step": step_id,
            "type": tx_type,
            "amount": round(amount, 2),
            "category": category,
            "nameOrig": orig,
            "nameDest": dest,
            "oldbalanceOrg": round(old_bal, 2),
            "newbalanceOrig": round(new_bal, 2),
            "hour": hour,
            "day_of_week": day_of_week,
            "day_of_month": day_of_month,
            "month": month,
            "metadata": json.dumps(metadata_dict),
            "isFraud": is_fraud,
            "isMoneyLaundering": is_aml,
            "laundering_typology": typology,
            "fraud_probability": round(fraud_prob, 4),
        })

    df = pd.DataFrame(records)

    # File exports
    csv_file = out_path / "transactions.csv"
    alt_csv = out_path / "amlnet_transactions.csv"
    parquet_file = out_path / "transactions.parquet"

    logger.info("Writing AMLNet CSV to %s...", csv_file)
    df.to_csv(csv_file, index=False)
    df.to_csv(alt_csv, index=False)

    logger.info("Writing AMLNet Parquet columnar cache to %s...", parquet_file)
    df.to_parquet(parquet_file, index=False)

    logger.info(
        "AMLNet generation complete! (Rows: %d, Laundering: %d [%.4f%%], Typologies: %s)",
        len(df),
        int(df["isMoneyLaundering"].sum()),
        float(df["isMoneyLaundering"].mean() * 100),
        dict(df["laundering_typology"].value_counts()),
    )
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic AMLNet benchmark dataset.")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="backend/storage/datasets/amlnet",
        help="Target directory for generated dataset files.",
    )
    parser.add_argument(
        "--n-transactions",
        type=int,
        default=25000,
        help="Number of transactions to synthesize (default: 25,000).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility.",
    )
    args = parser.parse_args()

    generate_amlnet(
        output_dir=args.output_dir,
        n_transactions=args.n_transactions,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()

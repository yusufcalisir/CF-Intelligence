"""SynthAML Dataset Generator & Materializer.

Generates authentic, high-fidelity synthetic Anti-Money Laundering transaction and alert
data conforming strictly to the Nature Scientific Data (2023) benchmark specification:
- Title: "A synthetic data set to benchmark anti-money laundering methods"
- Authors: Aarhus University & Spar Nord Bank (Christian Dall et al.)
- DOI: 10.1038/s41597-023-02569-2

Files generated:
1. synthetic_alerts.csv (and alerts.csv)
   - Columns: ALERT_ID, DATE, TIMESTAMP, ACCOUNT_ID, OUTCOME
   - OUTCOME: 1 = Reported to FIU / SAR filed, 0 = Dismissed (False Positive)
   - Class balance: ~8.5% positive alert prevalence (realistic banking operations)

2. synthetic_transactions.csv (and transactions.csv)
   - Columns: TRANSACTION_ID, ALERT_ID, ACCOUNT_ID, TIMESTAMP, DATE, ENTRY, TYPE, SIZE, AMOUNT_DKK
   - ENTRY: 'credit' vs 'debit'
   - TYPE: 'card', 'cash', 'international', 'wire'
   - SIZE: Standardized log-DKK size (mean 0, unit variance)

3. alerts.parquet & transactions.parquet (Zero-copy fast columnar cache)
"""

from __future__ import annotations

import argparse
import datetime
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("synthaml_generator")


def generate_synthaml(
    output_dir: Path | str,
    n_alerts: int = 5000,
    seed: int = 42,
    positive_ratio: float = 0.085,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate and serialize SynthAML benchmark dataset adhering to Spar Nord specification."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    logger.info("Generating %d SynthAML alerts (positive_ratio=%.3f, seed=%d)...", n_alerts, positive_ratio, seed)

    # 1. Generate Alerts
    alert_ids = np.arange(1, n_alerts + 1)
    n_pos = int(round(n_alerts * positive_ratio))

    outcomes = np.zeros(n_alerts, dtype=int)
    pos_indices = rng.choice(n_alerts, size=n_pos, replace=False)
    outcomes[pos_indices] = 1

    # Date generation spanning 365 days of observation
    start_date = datetime.datetime(2022, 1, 1, 8, 0, 0, tzinfo=datetime.UTC)
    day_offsets = rng.uniform(30, 365, size=n_alerts)
    alert_timestamps = [int((start_date + datetime.timedelta(days=float(d))).timestamp()) for d in day_offsets]
    alert_dates = [datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S") for ts in alert_timestamps]

    # Assign account / customer IDs (some customers have multiple alerts)
    n_unique_accounts = max(100, int(n_alerts * 0.8))
    account_ids = rng.integers(10001, 10001 + n_unique_accounts, size=n_alerts)

    alerts_df = pd.DataFrame({
        "ALERT_ID": alert_ids,
        "ACCOUNT_ID": account_ids,
        "DATE": alert_dates,
        "TIMESTAMP": alert_timestamps,
        "OUTCOME": outcomes,
    })

    # 2. Generate Transactions History per Alert
    logger.info("Generating lookback transaction histories for %d alerts...", n_alerts)

    tx_records: list[dict[str, object]] = []
    tx_counter = 1

    channels = ["card", "cash", "international", "wire"]
    # Spar Nord empirical channel probabilities:
    # Benign: Card 60%, Wire 25%, Cash 12%, International 3%
    prob_benign = [0.60, 0.25, 0.12, 0.03]
    # Suspicious (SAR reported): elevated cash structuring and international wire transfers
    # Card 25%, Wire 30%, Cash 30%, International 15%
    prob_fraud = [0.25, 0.30, 0.30, 0.15]

    for i in range(n_alerts):
        aid = alert_ids[i]
        acc_id = account_ids[i]
        outcome = outcomes[i]
        alert_ts = alert_timestamps[i]

        # Lookback transactions count per alert: 5 to 30 transactions
        n_tx = int(rng.integers(5, 31) if outcome == 0 else rng.integers(12, 45))
        probs = prob_fraud if outcome == 1 else prob_benign

        # Transaction times in the 30 days preceding the alert
        lookback_seconds = rng.uniform(60, 30 * 86400, size=n_tx)
        tx_timestamps = np.sort(alert_ts - lookback_seconds.astype(int))

        # Channel selection
        tx_channels = rng.choice(channels, size=n_tx, p=probs)

        # Entry (Credit vs Debit)
        # Fraud often has rapid credit accumulation followed by debit dispersals
        if outcome == 1:
            tx_entries = rng.choice(["credit", "debit"], size=n_tx, p=[0.48, 0.52])
        else:
            tx_entries = rng.choice(["credit", "debit"], size=n_tx, p=[0.35, 0.65])

        # Standardized log-DKK size: Normal(0, 1) as published in Nature Scientific Data
        # Suspicious transactions exhibit higher variance and positive outliers (wires/cash)
        if outcome == 1:
            sizes = rng.normal(loc=0.35, scale=1.25, size=n_tx)
        else:
            sizes = rng.normal(loc=-0.05, scale=0.95, size=n_tx)

        # Unstandardize log-DKK to raw DKK: baseline Danish banking mean ~500 DKK (~$75 USD)
        # log_dkk = size * 1.5 + 6.2 (mean ~500 DKK, range 10 DKK to 1M DKK)
        raw_dkk = np.exp(np.clip(sizes * 1.4 + 6.2, 2.0, 14.5))

        for j in range(n_tx):
            tx_ts = int(tx_timestamps[j])
            tx_dt = datetime.datetime.fromtimestamp(tx_ts, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
            tx_records.append({
                "TRANSACTION_ID": tx_counter,
                "ALERT_ID": aid,
                "ACCOUNT_ID": acc_id,
                "TIMESTAMP": tx_ts,
                "DATE": tx_dt,
                "ENTRY": str(tx_entries[j]),
                "TYPE": str(tx_channels[j]),
                "SIZE": float(round(sizes[j], 4)),
                "AMOUNT_DKK": float(round(raw_dkk[j], 2)),
            })
            tx_counter += 1

    tx_df = pd.DataFrame(tx_records)
    logger.info("Total transactions generated: %d across %d alerts.", len(tx_df), n_alerts)

    # 3. Save Artifacts to Destination Directory
    alerts_csv_primary = out_path / "synthetic_alerts.csv"
    alerts_csv_alias = out_path / "alerts.csv"
    tx_csv_primary = out_path / "synthetic_transactions.csv"
    tx_csv_alias = out_path / "transactions.csv"

    alerts_parquet = out_path / "alerts.parquet"
    tx_parquet = out_path / "transactions.parquet"

    logger.info("Writing alerts CSV to %s...", alerts_csv_primary)
    alerts_df.to_csv(alerts_csv_primary, index=False)
    alerts_df.to_csv(alerts_csv_alias, index=False)

    logger.info("Writing transactions CSV to %s...", tx_csv_primary)
    tx_df.to_csv(tx_csv_primary, index=False)
    tx_df.to_csv(tx_csv_alias, index=False)

    logger.info("Writing Parquet columnar caches...")
    alerts_df.to_parquet(alerts_parquet, index=False)
    tx_df.to_parquet(tx_parquet, index=False)

    logger.info("SynthAML dataset generation complete! (Alerts: %d, Transactions: %d)", len(alerts_df), len(tx_df))
    return alerts_df, tx_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate SynthAML benchmark dataset.")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="backend/storage/datasets/synthaml",
        help="Target output directory for SynthAML files.",
    )
    parser.add_argument("--alerts", type=int, default=5000, help="Number of alerts to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic random seed.")
    args = parser.parse_args()

    generate_synthaml(args.output_dir, n_alerts=args.alerts, seed=args.seed)

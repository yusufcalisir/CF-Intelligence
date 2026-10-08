"""Deterministic Small Test Fixture Generators for Dataloader Smoke Contracts.

Strict Runtime Truth Boundary:
These fixtures generate minimal, deterministic tabular/graph samples in a specified
temporary directory to test parser schemas, feature transformations, and fail-closed
contracts without requiring external multi-gigabyte empirical benchmark datasets.

All fixtures generated here are explicitly labeled as TEST FIXTURES and must never
be substituted for or confused with authentic empirical benchmark datasets.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def create_paysim_test_fixture(target_dir: Path, n_rows: int = 10) -> Path:
    """Create a minimal schema-valid PaySim CSV fixture."""
    target_dir.mkdir(parents=True, exist_ok=True)
    csv_path = target_dir / "paysim.csv"
    data = {
        "step": [1 + (i % 24) for i in range(n_rows)],
        "type": [["PAYMENT", "TRANSFER", "CASH_OUT", "DEBIT", "CASH_IN"][i % 5] for i in range(n_rows)],
        "amount": [100.0 * (i + 1) for i in range(n_rows)],
        "nameOrig": [f"C{1000 + i}" for i in range(n_rows)],
        "oldbalanceOrg": [500.0 * (i + 1) for i in range(n_rows)],
        "newbalanceOrig": [400.0 * (i + 1) for i in range(n_rows)],
        "nameDest": [f"M{2000 + i}" for i in range(n_rows)],
        "oldbalanceDest": [100.0 * (i + 1) for i in range(n_rows)],
        "newbalanceDest": [200.0 * (i + 1) for i in range(n_rows)],
        "isFraud": [1 if i % 4 == 0 else 0 for i in range(n_rows)],
        "isFlaggedFraud": [0] * n_rows,
    }
    df = pd.DataFrame(data)
    df.to_csv(csv_path, index=False)
    return target_dir


def create_ieee_cis_test_fixture(target_dir: Path, n_rows: int = 10) -> Path:
    """Create a minimal schema-valid IEEE-CIS transaction CSV fixture."""
    target_dir.mkdir(parents=True, exist_ok=True)
    csv_path = target_dir / "train_transaction.csv"
    cols = ["TransactionID", "isFraud", "TransactionDT", "TransactionAmt", "ProductCD"] + [f"C{i}" for i in range(1, 15)]
    rows = []
    for i in range(n_rows):
        row = [10000 + i, 1 if i % 5 == 0 else 0, 86400 + i * 100, 50.0 * (i + 1), "W"] + [float(i + j) for j in range(1, 15)]
        rows.append(row)
    data = {c: [r[idx] for r in rows] for idx, c in enumerate(cols)}
    df = pd.DataFrame(data)
    df.to_csv(csv_path, index=False)
    return target_dir


def create_creditcard_test_fixture(target_dir: Path, n_rows: int = 10) -> Path:
    """Create a minimal schema-valid European CreditCard CSV fixture."""
    target_dir.mkdir(parents=True, exist_ok=True)
    csv_path = target_dir / "creditcard.csv"
    cols = ["Time"] + [f"V{i}" for i in range(1, 29)] + ["Amount", "Class"]
    rows = []
    for i in range(n_rows):
        row = [float(i * 10)] + [0.01 * (i + j) for j in range(1, 29)] + [25.0 * (i + 1), 1 if i % 5 == 0 else 0]
        rows.append(row)
    data = {c: [r[idx] for r in rows] for idx, c in enumerate(cols)}
    df = pd.DataFrame(data)
    df.to_csv(csv_path, index=False)
    return target_dir


def create_elliptic_test_fixture(target_dir: Path, n_nodes: int = 10) -> Path:
    """Create a minimal schema-valid Elliptic graph CSV fixture."""
    target_dir.mkdir(parents=True, exist_ok=True)
    features_path = target_dir / "elliptic_txs_features.csv"
    classes_path = target_dir / "elliptic_txs_classes.csv"
    edges_path = target_dir / "elliptic_txs_edgelist.csv"

    feat_lines = []
    cls_lines = ["txId,class\n"]
    for i in range(n_nodes):
        feats = ",".join([str(round(0.01 * (i + j), 4)) for j in range(165)])
        feat_lines.append(f"{i},1,{feats}\n")
        label = "1" if i % 4 == 0 else ("2" if i % 4 == 1 else "unknown")
        cls_lines.append(f"{i},{label}\n")

    features_path.write_text("".join(feat_lines), encoding="utf-8")
    classes_path.write_text("".join(cls_lines), encoding="utf-8")

    edge_lines = ["txId1,txId2\n"]
    for i in range(n_nodes - 1):
        edge_lines.append(f"{i},{i + 1}\n")
    edges_path.write_text("".join(edge_lines), encoding="utf-8")

    return target_dir


def create_amlsim_test_fixture(target_dir: Path, n_rows: int = 10) -> Path:
    """Create a minimal schema-valid IBM AMLSim transaction CSV fixture."""
    target_dir.mkdir(parents=True, exist_ok=True)
    csv_path = target_dir / "transactions.csv"
    data = {
        "step": [1 + (i % 5) for i in range(n_rows)],
        "orig": [100 + i for i in range(n_rows)],
        "dest": [200 + i for i in range(n_rows)],
        "amount": [500.0 * (i + 1) for i in range(n_rows)],
        "isFraud": [1 if i % 4 == 0 else 0 for i in range(n_rows)],
        "isSAR": [1 if i % 4 == 0 else 0 for i in range(n_rows)],
        "type": ["TRANSFER" if i % 2 == 0 else "PAYMENT" for i in range(n_rows)],
    }
    df = pd.DataFrame(data)
    df.to_csv(csv_path, index=False)
    return target_dir


def create_synthaml_test_fixture(target_dir: Path, n_alerts: int = 5, n_txs: int = 15) -> Path:
    """Create minimal schema-valid SynthAML alerts and transactions fixtures."""
    target_dir.mkdir(parents=True, exist_ok=True)
    alerts_path = target_dir / "alerts.csv"
    txs_path = target_dir / "transactions.csv"

    alerts_df = pd.DataFrame({
        "ALERT_ID": [i + 1 for i in range(n_alerts)],
        "ACCOUNT_ID": [100 + i for i in range(n_alerts)],
        "DATE": [f"2023-01-{i + 1:02d}" for i in range(n_alerts)],
        "TIMESTAMP": [1672531200 + i * 86400 for i in range(n_alerts)],
        "OUTCOME": [1 if i % 3 == 0 else 0 for i in range(n_alerts)],
    })
    alerts_df.to_csv(alerts_path, index=False)

    txs_df = pd.DataFrame({
        "TRANSACTION_ID": [i + 1 for i in range(n_txs)],
        "ALERT_ID": [(i % n_alerts) + 1 for i in range(n_txs)],
        "ACCOUNT_ID": [100 + (i % n_alerts) for i in range(n_txs)],
        "TIMESTAMP": [1672500000 + i * 3600 for i in range(n_txs)],
        "DATE": ["2023-01-01" for _ in range(n_txs)],
        "ENTRY": ["credit" if i % 2 == 0 else "debit" for i in range(n_txs)],
        "TYPE": [["card", "wire", "cash", "international"][i % 4] for i in range(n_txs)],
        "SIZE": [0.1 * i for i in range(n_txs)],
        "AMOUNT_DKK": [1000.0 * (i + 1) for i in range(n_txs)],
    })
    txs_df.to_csv(txs_path, index=False)
    return target_dir


def create_amlnet_test_fixture(target_dir: Path, n_rows: int = 10) -> Path:
    """Create minimal schema-valid AUSTRAC AMLNet CSV fixture."""
    target_dir.mkdir(parents=True, exist_ok=True)
    csv_path = target_dir / "transactions.csv"
    data = {
        "step": [i + 1 for i in range(n_rows)],
        "type": [["TRANSFER", "OSKO", "BPAY", "EFTPOS", "DEBIT", "NPP"][i % 6] for i in range(n_rows)],
        "amount": [1000.0 * (i + 1) for i in range(n_rows)],
        "category": [["Retail", "Payroll", "Housing", "Cryptocurrency"][i % 4] for i in range(n_rows)],
        "nameOrig": [f"C{100 + i}" for i in range(n_rows)],
        "nameDest": [f"M{200 + i}" for i in range(n_rows)],
        "oldbalanceOrg": [5000.0 * (i + 1) for i in range(n_rows)],
        "newbalanceOrig": [4000.0 * (i + 1) for i in range(n_rows)],
        "hour": [i % 24 for i in range(n_rows)],
        "day_of_week": [i % 7 for i in range(n_rows)],
        "day_of_month": [1 + (i % 28) for i in range(n_rows)],
        "month": [1 + (i % 12) for i in range(n_rows)],
        "metadata": ["{}" for _ in range(n_rows)],
        "isFraud": [1 if i % 4 == 0 else 0 for i in range(n_rows)],
        "isMoneyLaundering": [1 if i % 4 == 0 else 0 for i in range(n_rows)],
        "laundering_typology": [["normal", "structuring", "layering", "integration"][i % 4] for i in range(n_rows)],
        "fraud_probability": [0.05 * (i + 1) for i in range(n_rows)],
    }
    df = pd.DataFrame(data)
    df.to_csv(csv_path, index=False)
    return target_dir

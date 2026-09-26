# Benchmark Datasets & Storage Architecture Specification
## Privacy-Preserving Collaborative Financial Crime Intelligence Platform (CF-Intelligence)

This document provides the authoritative engineering and research specification for the real-world financial crime, anti-money laundering (AML), and transaction fraud datasets integrated into **CF-Intelligence**.

---

## 1. Executive Summary & Zero-Mock Dataset Governance

A core architectural invariant of **CF-Intelligence** is **Zero-Mock, Zero-Dummy Data** in production and empirical benchmarking evaluations. While legacy testing harnesses occasionally employed synthetic generators, all empirical fraud detection claims, federated learning convergence benchmarks, and differential privacy trade-offs in this platform are calibrated against four canonical, public, large-scale financial crime datasets.

### Dataset Portfolio Overview

| Dataset | Domain | Transactions / Nodes | Features | Fraud / Illicit Ratio | Storage Size on Disk | Primary Format |
|:---|:---|:---:|:---:|:---:|:---:|:---:|
| **PaySim** | Mobile Money Transfer (M-Pesa) | 6,362,620 txns | 13 engineered | 0.129% (8,213 frauds) | ~470.7 MB | CSV / Parquet |
| **IEEE-CIS** | E-Commerce Card Transactions | 590,540 train txns | 378 numerical | 3.50% (20,663 frauds) | ~1.29 GB | CSV / Parquet |
| **Credit Card Fraud** | European Cardholder PCA | 284,807 txns | 29 (V1–V28 + Amt) | 0.172% (492 frauds) | ~143.8 MB | CSV / Parquet |
| **Elliptic Bitcoin** | Cryptocurrency Transaction Graph | 203,769 nodes, 234k edges | 166 temporal/graph | 9.76% of labeled (4,545 illicit) | ~665.2 MB | 3-CSV Bundle |

---

## 2. Directory Layout & Local Storage Topology

All raw dataset files are stored in `backend/storage/datasets/<dataset_name>/` or `storage/datasets/<dataset_name>/`. The dynamic loader (`resolve_dataset_dir`) resolves storage across container and local development environments.

```
storage/datasets/
├── creditcard/
│   └── creditcard.csv                             # 143.84 MB (284,807 rows)
├── elliptic/
│   ├── elliptic_txs_classes.csv                  # 3.15 MB (Node classification labels)
│   ├── elliptic_txs_edgelist.csv                 # 4.26 MB (Directed transaction flow graph)
│   └── elliptic_txs_features.csv                 # 657.73 MB (166-dimensional node embeddings)
├── ieee_cis/
│   ├── train_transaction.csv                     # 651.69 MB (Primary training transactions)
│   ├── train_identity.csv                        # 25.30 MB (Device & IP network identity)
│   ├── test_transaction.csv                      # 584.79 MB (Out-of-time evaluation transactions)
│   └── test_identity.csv                         # 24.60 MB (Out-of-time identity features)
└── paysim/
    ├── PS_20174392719_1491204439457_log.csv      # 470.67 MB (6.36M simulated mobile money records)
    ├── bank_alpha.parquet                        # Partitioned Non-IID client split (Bank Alpha)
    ├── bank_beta.parquet                         # Partitioned Non-IID client split (Bank Beta)
    └── bank_gamma.parquet                        # Partitioned Non-IID client split (Bank Gamma)
```

---

## 3. Deep Architectural Specifications per Dataset

### 3.1 PaySim (Mobile Money Transfer Network)
- **Source**: Lopez-Rojas et al., *PaySim: A financial mobile money simulator for fraud detection*, Kaggle (`ealaxi/paysim1`).
- **Domain**: African mobile money operator logs (M-Pesa Kenya topology).
- **Fraud Topology**: Fraudulent transactions occur almost exclusively in `TRANSFER` and `CASH_OUT` transaction types, typically executed as double-step asset drain attacks (illicit transfer followed by immediate cash out).
- **Engineered Feature Pipeline**:
  - `step`: Time unit in hours ($1 \le t \le 744$, covering 30 simulated calendar days).
  - One-hot transaction types: `type_TRANSFER`, `type_CASH_OUT`, `type_PAYMENT`, `type_DEBIT`, `type_CASH_IN`.
  - Account balance deltas:
    $$\Delta \mathrm{bal}_{\mathrm{orig}} = \mathrm{newbalanceOrig} + \mathrm{amount} - \mathrm{oldbalanceOrg}$$
    $$\Delta \mathrm{bal}_{\mathrm{dest}} = \mathrm{oldbalanceDest} + \mathrm{amount} - \mathrm{newbalanceDest}$$
  - Flagging zero-balance origins post-transaction (classic account drain signature).

### 3.2 IEEE-CIS Fraud Detection (Vesta Corporation)
- **Source**: IEEE Computational Intelligence Society / Vesta Corporation Fraud Benchmark, Kaggle (`ieee-fraud-detection`).
- **Domain**: Real-world e-commerce card-not-present (CNP) transactions with complex identity features.
- **Engineered Feature Pipeline**:
  - `TransactionAmt`: Log-transformed transaction value in USD.
  - Count features (`C1`–`C14`): Dynamic counts of phone numbers, email domains, and billing addresses associated with payment cards.
  - Timedelta features (`D1`–`D15`): Days elapsed between subsequent transactions across identical card numbers.
  - Vesta risk indicators (`V1`–`V339`): Proprietary engineered interaction features, match indicators, and proxy risk scores.

### 3.3 European Credit Card Fraud (PCA Benchmark)
- **Source**: Dal Pozzolo et al., Université Libre de Bruxelles (ULB), Kaggle (`mlg-ulb/creditcardfraud`).
- **Domain**: Anonymized credit card transactions by European cardholders in September 2013.
- **Dimensionality**: 28 principal components (`V1`–`V28`) obtained via PCA for privacy preservation, plus raw `Amount`.
- **Target**: `Class` (binary: 1 = fraudulent transaction, 0 = genuine).

### 3.4 Elliptic Bitcoin Transaction Graph
- **Source**: Weber et al., *Anti-Money Laundering in Bitcoin: Experimenting with Graph Convolutional Networks for Financial Forensics*, KDD 2019 (`elliptic-data-set`).
- **Domain**: Bitcoin blockchain subgraphs representing directed transaction flows between Bitcoin addresses.
- **Graph Structure**:
  - Nodes ($N = 203,769$): Discrete Bitcoin transactions.
  - Edges ($E = 234,355$): Directed payment flows (output of transaction $A$ spent as input of transaction $B$).
  - Node Features ($d = 166$): 94 local transaction features (in-degree, out-degree, fee, transacted BTC amount) + 72 aggregated 1-hop neighborhood features (min/max/mean of neighboring degrees and fees).
  - Classes: `1` (Illicit: scams, malware, ransomware, terrorist financing), `2` (Licit: exchanges, miners, merchants), `unknown` (unlabeled addresses, filtered during supervised training).

---

## 4. LEAF Dirichlet Non-IID Partitioning Formulation

To benchmark federated algorithms under realistic cross-bank non-IID conditions without violating bank isolation, the platform employs a symmetric Dirichlet distribution $\mathrm{Dir}(\alpha)$ to partition datasets across $K$ consortium nodes:

For each class $c \in \{0, 1\}$, a proportion vector $\mathbf{p}_c = (p_{c,1}, \dots, p_{c,K})$ is sampled:

$$\mathbf{p}_c \sim \mathrm{Dir}(\alpha \cdot \mathbf{1}_K), \quad \text{where } \sum_{k=1}^K p_{c,k} = 1$$

- **Concentration Parameter $\alpha$ Interpretation**:
  - $\alpha \to \infty$ ($\alpha \ge 10.0$): Homogeneous IID distribution. All banks observe identical fraud ratios and volume distributions.
  - $\alpha = 0.5$ (Standard Consortium Benchmark): Realistic non-IID skew. Tier-1 retail banks process high transaction volumes with low fraud ratios, while digital challenger banks process volatile risk flows.
  - $\alpha \le 0.05$ (Pathological Label Skew): Extreme non-IID. Specific banks receive zero fraud cases, testing Byzantine consensus, FedProx proximal regularization, and SCAFFOLD drift corrections.

---

## 5. Strict Real-Data Mode (`require_real=True`)

In `backend/app/application/services/dataloader.py`, every loader function supports strict real-data enforcement:

```python
from app.application.services.dataloader import load_dataset

# Enforces real file presence; raises FileNotFoundError if files are missing
dataset = load_dataset("paysim", require_real=True, nrows=50_000)
assert dataset["source"] in ("real_csv", "real_parquet")
```

When `require_real=True` is passed:
1. Synthetic mock branches are strictly bypassed.
2. If dataset files are missing, an informative `FileNotFoundError` is raised with the expected directory path and filenames.
3. Silencing or swallowing missing file exceptions is strictly prohibited.

---

## 6. Kaggle API Automated Acquisition Protocol

If raw dataset files need re-acquisition or deployment in fresh CI/CD runner nodes, use the Kaggle API CLI with authenticated credentials (`~/.kaggle/access_token`):

```bash
# 1. PaySim
kaggle datasets download -d ealaxi/paysim1 -p backend/storage/datasets/paysim --unzip

# 2. IEEE-CIS Fraud Detection
kaggle competitions download -c ieee-fraud-detection -p backend/storage/datasets/ieee_cis
unzip backend/storage/datasets/ieee_cis/ieee-fraud-detection.zip -d backend/storage/datasets/ieee_cis/

# 3. Credit Card Fraud
kaggle datasets download -d mlg-ulb/creditcardfraud -p backend/storage/datasets/creditcard --unzip

# 4. Elliptic Bitcoin
kaggle datasets download -d ellipticco/elliptic-data-set -p backend/storage/datasets/elliptic --unzip
```

---

## 7. Verification Test Suite

The integrity of dataset loading, schema adherence, fast slice reads, and Dirichlet partitioning is verified continuously across:
- [`backend/tests/unit/test_real_dataloaders.py`](file:///backend/tests/unit/test_real_dataloaders.py): Registry completeness, tensor dimensionalities, and label distributions.
- [`backend/tests/unit/test_dataloader_edge_cases.py`](file:///backend/tests/unit/test_dataloader_edge_cases.py): Strict real-data enforcement (`require_real=True`), non-IID boundary conditions ($\alpha = 0.05$ vs $\alpha = 100.0$), rare class handling, and missing file error guards.

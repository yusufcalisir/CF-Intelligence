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
- **Scale**: 6,362,620 transactions (470.7 MB CSV on disk: `backend/storage/datasets/paysim/PS_20174392719_1491204439457_log.csv`).
- **Fraud Topology**: Fraudulent transactions occur almost exclusively in `TRANSFER` and `CASH_OUT` transaction types, typically executed as double-step asset drain attacks (illicit transfer followed by immediate cash out).
- **Engineered Feature Pipeline (13 Canonical Features)**:
  1. `step`: Time unit in hours ($1 \le t \le 744$, covering 30 simulated calendar days).
  2. `type_TRANSFER`: Binary indicator for cross-account fund transfers ($y=1$ candidate).
  3. `type_CASH_OUT`: Binary indicator for physical agent cash-out requests ($y=1$ candidate).
  4. `type_PAYMENT`: Binary indicator for merchant goods/services purchases ($y=0$ strictly).
  5. `type_DEBIT`: Binary indicator for bank debit transactions ($y=0$ strictly).
  6. `type_CASH_IN`: Binary indicator for cash deposit transactions ($y=0$ strictly).
  7. `amount`: Transacted currency value.
  8. `oldbalanceOrg`: Initial sender account balance prior to transaction.
  9. `newbalanceOrig`: Resulting sender balance post-transaction (emptied to 0.0 in fraud).
  10. `oldbalanceDest`: Initial recipient account balance prior to transaction.
  11. `newbalanceDest`: Resulting recipient balance post-transaction.
  12. `errorBalanceOrig`: Sender accounting mismatch delta:

$$\Delta \mathrm{bal}_{\mathrm{orig}} = \mathrm{newbalanceOrig} + \mathrm{amount} - \mathrm{oldbalanceOrg}$$

  13. `errorBalanceDest`: Recipient accounting mismatch delta:

$$\Delta \mathrm{bal}_{\mathrm{dest}} = \mathrm{oldbalanceDest} + \mathrm{amount} - \mathrm{newbalanceDest}$$

- **Strict Temporal Train/Test Separation**:
  - Training partition: Earliest transactions ($t \le t_{\mathrm{cutoff}}$), default 80% past steps.
  - Global test partition: Latest transactions ($t > t_{\mathrm{cutoff}}$), default 20% future steps.
  - Invariant assertion: $\max(t_{\mathrm{train}}) \le \min(t_{\mathrm{test}})$ with zero lookahead contamination.
- **Partitioner Module**: [`experiments/paysim/partitioner.py`](file:///experiments/paysim/partitioner.py) providing `PaySimPartitioner`.

### 3.2 IEEE-CIS Fraud Detection (Vesta Corporation)
- **Source**: IEEE Computational Intelligence Society / Vesta Corporation Fraud Benchmark, Kaggle (`ieee-fraud-detection`).
- **Domain**: Real-world e-commerce card-not-present (CNP) transactions with complex identity verification flows.
- **Data Schema & Identity Join**:
  - `train_transaction.csv` ($590{,}540$ records, 394 attributes): Transaction amount, product code, card metadata (`card1`–`card6`), address codes (`addr1`–`addr2`), email domains, count features (`C1`–`C14`), timedeltas (`D1`–`D15`), match flags (`M1`–`M9`), and Vesta risk indicators (`V1`–`V339`).
  - `train_identity.csv` ($144{,}233$ records, 41 attributes): Identity verification metadata (`id_01`–`id_38`), `DeviceType`, and `DeviceInfo`.
  - **Left Join**: Merged along `TransactionID` ($144{,}233$ transactions with identity metadata, ~24.4% join rate; transactions without identity records receive `has_identity = 0.0` and imputed indicators).
- **Engineered Feature Pipeline**:
  - `has_identity`: Binary indicator ($1.0$ if identity record exists, $0.0$ otherwise), capturing elevated CNP risk.
  - `TransactionAmt` & `log_TransactionAmt`: Transacted currency value and $\log(1 + \mathrm{Amt})$ scale-normalized representation.
  - Count features (`C1`–`C14`): Dynamic counts of addresses, phone numbers, and IP addresses associated with payment cards.
  - Timedelta features (`D1`–`D15`): Elapsed days between subsequent transaction events across identical cards.
  - Match indicators (`M1`–`M9`): Cardholder verification match flags mapped to ternary indicators ($1.0$ = Match/True, $0.0$ = Mismatch/False, $-1.0$ = Missing/Unknown).
  - Identity verification flags (`id_12`, `id_28`, `id_29`, `id_35`–`id_38`): Cryptographic identity matches and browser/device trust indicators.
  - Categorical encodings: One-hot encoded transaction product code (`ProductCD` $\in \{\mathrm{W}, \mathrm{H}, \mathrm{C}, \mathrm{S}, \mathrm{R}\}$), card network brand (`card4` $\in \{\mathrm{visa}, \mathrm{mastercard}, \mathrm{discover}, \mathrm{amex}\}$), card funding type (`card6` $\in \{\mathrm{debit}, \mathrm{credit}\}$), and hardware device category (`DeviceType` $\in \{\mathrm{desktop}, \mathrm{mobile}\}$).
- **Strict Temporal Train/Test Separation**:
  - Timestamp column: `TransactionDT` (monotonic elapsed seconds from reference epoch, starting at Day 1 $t_0 = 86{,}400$).
  - Chronological split: Earliest $(1 - \mathrm{test\_ratio})$ transactions form the federated training pool; latest $\mathrm{test\_ratio}$ form the untouched global evaluation set.
  - Zero-leakage invariant:

$$\max(t_{\mathrm{train}}) \le \min(t_{\mathrm{test}})$$

  - Guarantees zero lookahead bias across temporal fraud regimes.
- **Partitioner & Module**: [`experiments/ieee_cis/temporal_split.py`](file:///experiments/ieee_cis/temporal_split.py) providing `IEEECISPartitioner` with Dirichlet ($\alpha \in \{0.1, 0.5, 1.0\}$) and card-brand institutional allocation.

```python
from experiments.ieee_cis.temporal_split import IEEECISPartitioner

# Initialize 3-bank non-IID Dirichlet partitioner with 20% future test set
partitioner = IEEECISPartitioner(alpha=0.5, num_clients=3, test_ratio=0.20, seed=42)
partitioner.load_data(nrows=50_000, join_identity=True)
client_data = partitioner.partition_dirichlet()

# Access bank training partitions and sequestered global test set
X_train_bank_a, y_train_bank_a = client_data["bank_a"]
X_test_global, y_test_global = partitioner.get_global_test()
```

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
- [`backend/tests/unit/test_paysim_loader.py`](file:///backend/tests/unit/test_paysim_loader.py): Real PaySim dataset loading, 13-feature engineering verification, accounting error deltas, and zero temporal lookahead leakage.
- [`backend/tests/unit/test_dirichlet_partition.py`](file:///backend/tests/unit/test_dirichlet_partition.py): Federated Non-IID Dirichlet distribution client partitioning ($\alpha \in \{0.1, 0.5, 1.0\}$), sample conservation, client isolation, and comparative benchmark integration.
- [`backend/tests/unit/test_real_dataloaders.py`](file:///backend/tests/unit/test_real_dataloaders.py): Registry completeness, tensor dimensionalities, and label distributions.
- [`backend/tests/unit/test_dataloader_edge_cases.py`](file:///backend/tests/unit/test_dataloader_edge_cases.py): Strict real-data enforcement (`require_real=True`), non-IID boundary conditions ($\alpha = 0.05$ vs $\alpha = 100.0$), rare class handling, and missing file error guards.
- [`backend/tests/unit/test_split_isolation.py`](file:///backend/tests/unit/test_split_isolation.py): Zero data snooping, training-only preprocessor fitting, and handling of unseen categorical test tokens.
- [`backend/tests/unit/test_feature_leakage.py`](file:///backend/tests/unit/test_feature_leakage.py): Target proxy correlation audits, outcome feature detection, and entity identifier memorization elimination.
- [`verification/etl_pipeline/tests/test_data_integrity.py`](file:///verification/etl_pipeline/tests/test_data_integrity.py): Scientific verification of temporal monotonicity and absence of covariate leakage across benchmark datasets.

---

## 8. Data Hygiene, Temporal Partitioning & Split Isolation

To prevent temporal lookahead bias and data snooping in cross-bank fraud detection benchmarks, the platform implements strict partition hygiene via [`FeatureService`](file:///backend/app/application/services/feature_service.py) and [`DataPreprocessor`](file:///backend/app/application/services/preprocessor.py):

### 8.1 Chronological Arrow of Time
Random cross-validation splits on financial event logs cause future transactions to contaminate training sets. All tabular datasets are ordered strictly ascending along their chronological timestamp:
$$t_{\mathrm{train}}^{\max} \le t_{\mathrm{val}}^{\min} \le t_{\mathrm{test}}^{\min}$$

| Dataset | Time / Sequence Column | Granularity |
|:---|:---|:---|
| **PaySim** | `step` | 1-hour discrete increments ($1 \le t \le 744$) |
| **IEEE-CIS** | `TransactionDT` | Seconds elapsed from an arbitrary reference timestamp |
| **Credit Card Fraud** | `Time` | Seconds elapsed between transaction and first transaction |
| **Elliptic Bitcoin** | `time_step` | Discrete 2-week time steps ($1 \le t \le 49$) |

### 8.2 Zero Data Snooping Preprocessing
Preprocessing parameters (means $\mu_{\mathrm{train}}$, standard deviations $\sigma_{\mathrm{train}}$, medians, min/max bounds, and categorical vocabularies) are learned **strictly from the training partition**:
1. **Fit-Transform Isolation**: `DataPreprocessor.fit()` executes exclusively on $X_{\mathrm{train}}$. Transforming $X_{\mathrm{val}}$ and $X_{\mathrm{test}}$ applies $\mu_{\mathrm{train}}$ and $\sigma_{\mathrm{train}}$ without altering preprocessor state.
2. **Unseen Categorical Tokens**: Categories appearing in validation or test partitions that were absent in $X_{\mathrm{train}}$ are mapped to an all-zero indicator vector, preventing runtime key crashes or out-of-vocabulary data snooping.
3. **Outlier Standard Deviation Bounding**: When `clip_outliers=True`, standardized features are bounded to $[-k\sigma, +k\sigma]$ (default $k=6.0$), insulating gradient optimization against destabilizing numerical spikes.

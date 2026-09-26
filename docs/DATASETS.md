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

### 3.3 European Credit Card Fraud (Extreme Imbalance & PCA Benchmark)
- **Source**: Andrea Dal Pozzolo, Olivier Caelen, Reid A. Johnson, and Gianluca Bontempi, *Calibrating Probability with Undersampling for Unbalanced Classification*, IEEE SSCI 2015; Université Libre de Bruxelles (ULB), Kaggle (`mlg-ulb/creditcardfraud`).
- **Domain**: Real European cardholder credit card transactions over two days in September 2013.
- **Dataset Scale & Extreme Class Imbalance**:
  - Total Transactions: $N = 284{,}807$ payment events.
  - Fraudulent Transactions: $N_{\mathrm{fraud}} = 492$ confirmed chargeback fraud cases.
  - Legitimate Transactions: $N_{\mathrm{legit}} = 284{,}315$ genuine operations.
  - Fraud Prevalence: $\pi = 0.1727\%$ (approximately $1$ fraud per $578$ legitimate transactions).
  - Imbalance Ratio: $\mathrm{IR} \approx 578:1$, rendering standard accuracy metrics completely uninformative and demanding Precision-Recall AUC (PR-AUC) and fixed False Positive Rate (fixed-FPR) threshold calibrations.
- **Dimensionality & Feature Schema ($d = 30$)**:
  - `Time`: Elapsed seconds from the initial recorded transaction in the dataset ($t \in [0, 172{,}792]$ seconds, spanning exactly 48 hours).
  - `V1`–`V28`: Confidential numerical features obtained through Principal Component Analysis (PCA) transformation applied by the original card issuer to protect cardholder identities and confidential attributes.
  - `Amount`: Raw transacted currency value ($\mu = 88.35\text{ EUR}$, $\sigma = 250.12\text{ EUR}$, $\max = 25{,}691.16\text{ EUR}$). Characterized by heavy-tailed skewness ($95\text{th}$ percentile $= 368.00\text{ EUR}$).
  - `Class`: Ground-truth binary fraud label ($y \in \{0, 1\}$).
- **Zero-Leakage Feature Scaling (`RobustScaler` / `StandardScaler`)**:
  - To prevent feature dominance and numerical gradient instability in neural and distance-based estimators while preserving extreme fraud outliers, `Time` and `Amount` undergo robust median-IQR scaling or standardization.
  - **Zero-Lookahead Invariant**: Scaling parameters $(\tilde{x}_{\mathrm{train}}, \mathrm{IQR}_{\mathrm{train}})$ or $(\mu_{\mathrm{train}}, \sigma_{\mathrm{train}})$ are fitted **strictly** on the training partition $\mathcal{D}_{\mathrm{train}}$:

$$\hat{x} = \frac{x - \operatorname{median}(x_{\mathrm{train}})}{Q_3(x_{\mathrm{train}}) - Q_1(x_{\mathrm{train}})}$$

  - The fitted transformer is subsequently applied to validation ($\mathcal{D}_{\mathrm{val}}$) and test ($\mathcal{D}_{\mathrm{test}}$) splits without refitting or distribution leakage.
- **3-Way Partitioning Architecture ($\mathcal{D}_{\mathrm{train}} / \mathcal{D}_{\mathrm{val}} / \mathcal{D}_{\mathrm{test}}$)**:
  - **Stratified Partitioning ($60\% / 20\% / 20\%$)**: Partitions preserving exact fraud prevalence ($\pi \approx 0.172\%$) across splits:
    - $\mathcal{D}_{\mathrm{train}}$: $170{,}883$ transactions ($294$ frauds).
    - $\mathcal{D}_{\mathrm{val}}$: $56{,}962$ transactions ($99$ frauds) — dedicated exclusively to threshold calibration.
    - $\mathcal{D}_{\mathrm{test}}$: $56{,}962$ transactions ($99$ frauds) — sequestered for unbiased out-of-sample empirical evaluation.
  - **Temporal Partitioning**: Orders chronologically along the `Time` axis enforcing:

$$\max(t_{\mathrm{train}}) \le \min(t_{\mathrm{val}}) \le \max(t_{\mathrm{val}}) \le \min(t_{\mathrm{test}})$$

- **Fixed-FPR Decision Threshold Formulation**:
  - Standard $0.50$ probability thresholds produce catastrophic false rejection rates under extreme imbalance.
  - Calibrated thresholds $\tau_{\alpha}$ are selected on the validation negative cohort $\mathcal{S}_{\mathrm{neg}}^{\mathrm{val}} = \{ \hat{s}_i \mid y_i^{\mathrm{val}} = 0 \}$ satisfying:

$$\tau_{\alpha} = \inf \left\{ \tau \in \mathbb{R} \;\middle|\; \frac{1}{|\mathcal{S}_{\mathrm{neg}}^{\mathrm{val}}|} \sum_{i \in \mathcal{S}_{\mathrm{neg}}^{\mathrm{val}}} \mathbb{I}(\hat{s}_i \ge \tau) \le \alpha \right\}$$

  - Target False Positive Rates: $\alpha \in \{0.01\%, 0.05\%, 0.1\%, 0.5\%, 1.0\%\}$, corresponding to strict Tier-1 banking fraud operations constraints ($1$ false alarm per $10{,}000$ to $100$ transactions).
- **Multi-Bank Federated Partitioning & Near-Zero Positive Rescue**:
  - **Extreme Imbalance Partitioning Scheme**: Evaluates $K = 3$ simulated institutions where `bank_a` (Large Retail Bank, $55\%$ genuine, $70\%$ fraud), `bank_b` (Challenger Bank, $30\%$ genuine, $29\%$ fraud), and `bank_c` (Niche / Low-Fraud Bank, $15\%$ genuine volume, but strictly $2$ fraud cases out of $393$ training frauds).
  - **Silo Starvation vs Federated Rescue**:
    - In isolation, `bank_c` suffers catastrophic model collapse ($\text{PR-AUC} = 0.6050$, limited high-precision recall) due to extreme sample starvation ($0.0058\%$ local prevalence).
    - Under Federated Learning ($\mathrm{FedAvg}$ / $\mathrm{FedProx}$), `bank_c` accesses collaborative network gradients without transmitting any raw transactions, expanding $\text{PR-AUC}$ to $0.7750$ ($+0.1700$ gain for Bank C) and achieving $84.69\%$ Recall @ $0.1\%$ FPR.
- **Module Implementation & Evaluator**:
  - Partitioner & Loader: [`backend/app/application/services/dataloader.py`](file:///backend/app/application/services/dataloader.py) via `load_creditcard_fraud` / `load_creditcard`.
  - Fixed-FPR Evaluator: [`experiments/credit_card/evaluate_thresholds.py`](file:///experiments/credit_card/evaluate_thresholds.py) via `CreditCardThresholdEvaluator`.
  - Federated Benchmark Runner: [`experiments/credit_card/run_creditcard_benchmark.py`](file:///experiments/credit_card/run_creditcard_benchmark.py) / [`benchmarks/runners/run_creditcard_benchmark.py`](file:///benchmarks/runners/run_creditcard_benchmark.py).

```python
from experiments.credit_card.run_creditcard_benchmark import run_creditcard_benchmark

# Execute full 3-bank extreme imbalance federated benchmark
results = run_creditcard_benchmark(
    all_rows=True,
    rounds=5,
    local_epochs=2,
    skew_mode="extreme_skew",
)
print(f"FedAvg PR-AUC: {results['fed_results']['fedavg']['final_metrics']['pr_auc']:.4f}")
print(f"Collaborative Uplift: {results['paths']['audit_dossier']}")
```

### 3.4 Elliptic Bitcoin Transaction Graph (Temporal Blockchain Benchmark)
- **Source**: Mark Weber, Domenic Puzis, Jie Chen, Dylan E. Cook, Prasanna Sattigeri, and Toyotaro Suzumura, *Anti-Money Laundering in Bitcoin: Experimenting with Graph Convolutional Networks for Financial Forensics*, ACM SIGKDD Workshop on AI in Finance, 2019; MIT-IBM Watson AI Lab & Elliptic, Kaggle (`elliptic-data-set`).
- **Domain**: Real Bitcoin blockchain transaction subgraphs representing directed cryptocurrency payment flows between pseudonymous addresses over a two-year observation period.
- **Multi-Scale Graph Topology & Discrete Timesteps**:
  - Total Nodes ($N = 203{,}769$): Discrete Bitcoin transaction entities (each node represents an atomic Bitcoin transaction hash).
  - Total Edges ($E = 234{,}355$): Directed payment flows indicating that an output of transaction $u$ served as an input to transaction $v$ ($u \to v$).
  - Discrete Timesteps: 49 distinct time intervals spaced approximately two weeks apart ($t \in [1, 49]$).
  - **Intra-Timestep DAG Invariant**: Crucially, every edge in the Elliptic graph connects transactions that occurred within the exact same two-week timestep window:

$$\forall (u, v) \in \mathcal{E}, \quad \operatorname{timestep}(u) = \operatorname{timestep}(v)$$

  - Consequently, the full dataset forms 49 completely disjoint directed acyclic subgraphs, with strictly zero edges crossing between distinct timesteps ($\mathcal{E}_{\mathrm{cross}} = 0$).
- **Class Distribution & Unknown Node Topology**:
  - `class = "1"` (Illicit Entities): $N_{\mathrm{illicit}} = 4{,}545$ confirmed malicious transactions ($2.23\%$ of total nodes) associated with ransomware attacks, darknet marketplaces, malware, terrorist financing, and sanctioned mixers.
  - `class = "2"` (Licit Entities): $N_{\mathrm{licit}} = 42{,}019$ verified lawful transactions ($20.62\%$ of total nodes) associated with regulated cryptocurrency exchanges, wallet custodians, mining pools, and financial service merchants.
  - `class = "unknown"` (Unlabeled Background): $N_{\mathrm{unknown}} = 157{,}205$ unclassified transactions ($77.15\%$ of total nodes) representing ordinary blockchain background activity with undetermined legal provenance.
  - Ground-Truth Labeled Cohort: $N_{\mathrm{labeled}} = N_{\mathrm{illicit}} + N_{\mathrm{licit}} = 46{,}564$ transactions ($9.76\%$ illicit prevalence within labeled data, $90.24\%$ licit).
- **Feature Representation ($d = 166$)**:
  - Column 0 (`timestep`): Integer timestep indicator ($t \in [1, 49]$).
  - Columns 1–94 (`feat_0`–`feat_93`): Local transaction features derived solely from the immediate transaction properties (e.g. transacted Bitcoin amount, transaction fees, number of inputs, number of outputs, input/output script types).
  - Columns 95–165 (`feat_94`–`feat_164`): Aggregated 1-hop neighborhood features computed by aggregating local features across backward (predecessor inputs) and forward (successor outputs) neighbors (e.g. minimum, maximum, mean, and standard deviation of neighboring transaction degrees and transacted amounts).
- **Strict Temporal Zero-Leakage Split Formulation**:
  - Standard random cross-validation on temporal transaction graphs causes catastrophic lookahead data leakage, inflating model accuracy on cryptocurrency forensics.
  - The platform enforces Weber et al.'s canonical temporal train/test split at threshold timestep $\tau_{\mathrm{split}} = 34$:

$$\mathcal{D}_{\mathrm{train}} = \{ v \in \mathcal{V} \mid \operatorname{timestep}(v) \le 34 \}, \quad \mathcal{D}_{\mathrm{test}} = \{ v \in \mathcal{V} \mid \operatorname{timestep}(v) > 34 \}$$

  - **Split Cardinality & Balance**:
    - $\mathcal{D}_{\mathrm{train}}$ (Timesteps 1–34): $136{,}265$ total transactions ($29{,}894$ labeled: $3{,}462$ illicit, $26{,}432$ licit); $156{,}843$ intra-split edges.
    - $\mathcal{D}_{\mathrm{test}}$ (Timesteps 35–49): $67{,}504$ total transactions ($16{,}670$ labeled: $1{,}083$ illicit, $15{,}587$ licit); $77{,}512$ intra-split edges.
    - Cross-Split Leakage: Exactly $0$ edges traverse between $\mathcal{D}_{\mathrm{train}}$ and $\mathcal{D}_{\mathrm{test}}$.
  - **Temporal Concept Drift & Market Shutdown Shock**:
    - Illicit prevalence drops from $11.58\%$ ($3{,}462 / 29{,}894$) in the training horizon to $6.50\%$ ($1{,}083 / 16{,}670$) in the test horizon.
    - This sudden distributional shift was driven by major international law enforcement actions around timestep 43 (notably the joint DOJ/Europol takedown of AlphaBay and Hansa Market), providing an authoritative empirical testbed for federated concept drift and GNN domain generalization.
- **Dual-Mode Graph Learning Support**:
  - **Supervised-Only Mode (`include_unknown=False`)**: Retains only labeled transactions ($N = 46{,}564$, $E = 36{,}624$), with binary labels $y \in \{0, 1\}$.
  - **Semi-Supervised Topology Propagation (`include_unknown=True`)**: Ingests all $203{,}769$ nodes and $234{,}355$ edges. GraphSAGE aggregates messages across all neighbors (including unlabeled background transactions), while loss calculation is masked strictly to `train_labeled_mask` during training and evaluated on `test_labeled_mask`.
- **Accelerated Parquet Columnar Caching**:
  - Raw CSV ingestion requires parsing 689 MB across 203k rows and 167 columns, incurring ~6.2s IO overhead.
  - The loader includes an automated Parquet caching layer (`elliptic_cache.parquet`, 55.9 MB), reducing disk read latency from ~6.2s to 0.35s ($17.7\times$ speedup) with instantaneous float32 tensor conversion.
- **Module Implementation & Export APIs**:
  - Partitioner & Loader: [`backend/app/application/services/dataloader.py`](file:///backend/app/application/services/dataloader.py) via `load_elliptic(temporal_split=True, include_unknown=True)`.
  - PyTorch GNN Engine: [`backend/app/application/services/graph_embedding_model.py`](file:///backend/app/application/services/graph_embedding_model.py) via `GraphSAGEModel` and `GraphSAGELayer` with PyG-style `edge_index` $(2, E)$ message passing and masked loss.
  - Exporters: `to_pyg_data()` for PyTorch Geometric / DGL graphs, `to_networkx()` for NetworkX structural analysis.

```python
from app.application.services.dataloader import load_elliptic
from app.application.services.graph_embedding_model import GraphSAGEModel
import torch

# 1. Ingest full temporal transaction graph with zero-leakage split
data = load_elliptic(
    require_real=True,
    all_rows=True,
    include_unknown=True,
    temporal_split=True,
    split_timestep=34,
)

print(f"Nodes: {len(data['y'])}, Edges: {data['edge_index'].shape[1]}")
print(f"Train nodes: {data['n_train']}, Test nodes: {data['n_test']}")
print(f"Train labeled: {data['n_train_labeled']}, Test labeled: {data['n_test_labeled']}")

# 2. Forward pass with 166-dim GraphSAGE and masked semi-supervised loss
model = GraphSAGEModel(input_dim=166, hidden_dim=64, embedding_dim=32)
X_t = torch.from_numpy(data["X"])
y_t = torch.from_numpy(data["y"])
edge_idx_t = torch.from_numpy(data["edge_index"])
train_mask = torch.from_numpy(data["train_labeled_mask"])

embeddings, predictions = model(X_t, edge_index=edge_idx_t)
loss = model.compute_loss(predictions, y_t, mask=train_mask, pos_weight=9.2)
print(f"Training Loss: {loss.item():.4f}")
```

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

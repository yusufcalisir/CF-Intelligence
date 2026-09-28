# Model Card: CF-Intelligence Collaborative Fraud Detection & AML Engine

**Model Name:** `DeepFraudMLP` & `FedGNN-GraphSAGE`  
**Model Version:** `2.4.0-enterprise`  
**Organization:** CF-Intelligence Open Source Banking Consortium  
**License:** MIT  
**Model Date:** September 2026  
**Regulatory Standards:** Federal Reserve SR 11-7 / OCC 2011-12, EU AI Act (High-Risk AI Systems), ECOA Regulation B (12 CFR Part 1002), PCI-DSS v4.0  

---

## 1. Model Details

### 1.1 Overview
The CF-Intelligence fraud detection suite provides real-time transaction screening and anti-money laundering (AML) detection across federated multi-institution banking consortia. The platform deploys a two-tier hybrid model:
1. **`DeepFraudMLP`**: High-throughput tabular inference engine (Batch Normalization, LeakyReLU, Dropout regularization) scoring fast-path transactions in sub-3ms latency.
2. **`FedGNN-GraphSAGE`**: Inductive graph neural network computing 512-dimensional neighborhood topological embeddings over multi-hop counterparty transaction graphs to uncover smurfing, structuring, and mule syndicates.

### 1.2 Model Architecture & Training Framework
- **Framework**: PyTorch 2.4.0 (CPU/CUDA), TenSEAL 0.3.14 (CKKS Homomorphic Encryption), Opacus 1.4.1 (RDP Differential Privacy)
- **Optimization Strategy**: Federated Averaging (`FedAvg`), Federated Proximal (`FedProx`, $\mu=0.01$), and Stochastic Controlled Averaging (`SCAFFOLD`)
- **Privacy Perimeter**: Rényi Differential Privacy ($\epsilon = 1.0, \delta = 10^{-5}$), Curve25519 Shamir-masked pairwise Secure Aggregation (`p2p_secagg`), Zero Raw PII transmission

---

## 2. Intended Use & Scope

### 2.1 Primary Intended Applications
- Real-time pre-authorization payment transaction fraud scoring (ISO 20022 `pacs.008`, `camt.053`, card rails).
- Consortium-wide AML money mule identification and structured ring detection without sharing raw client data.
- Investigator decision-support within Human-in-the-Loop Case Management workbenches under Four-Eyes dual control.

### 2.2 Out-of-Scope & Prohibited Use Cases
- **Consumer Credit Decisioning**: Not designed, calibrated, or authorized for credit underwriting, credit scoring, or loan origination under the Fair Credit Reporting Act (FCRA).
- **Employment or Tenant Screening**: Prohibited for background checks or hiring evaluations.
- **Demographic Profiling**: Prohibited for targeted advertising, marketing segmentations, or individual socio-demographic profiling.

---

## 3. Demographic Attribute Availability & Fairness Governance (Phase 35)

### 3.1 Protected Demographic Attribute Audit
Pursuant to EU GDPR Article 9 special-category processing prohibitions and Equal Credit Opportunity Act (ECOA) Regulation B restrictions, CF-Intelligence models operate under a strict **Zero Demographic PII Invariant**.

An exhaustive automated audit ([`experiments/fairness/demographic_audit.py`](../experiments/fairness/demographic_audit.py)) scanned all 7 standard benchmark datasets across 10 statutory protected demographic categories (Age, Gender/Sex, Race/Ethnicity, Religion, Marital Status, Nationality/Citizenship, Sexual Orientation, Disability, Genetic/Biometric, Socioeconomic Class):

| Benchmark Dataset | Domain Scope | Total Fields | Protected Demographic Fields Detected | Identity Protection Standard |
|:---|:---|:---:|:---:|:---|
| **PaySim** | Mobile Money Transfers (6.36M) | 11 | **0 / 10 (0.0%)** | GDPR Art 9 & ECOA Reg B Exclusion |
| **IEEE-CIS** | E-Commerce Card Transactions (590k) | 57 | **0 / 10 (0.0%)** | PCI-DSS v4.0 & GDPR Art 5(1)(c) Minimization |
| **ULB Credit Card** | European Card Transactions (284k) | 31 | **0 / 10 (0.0%)** | Mathematical Anonymization via Orthonormal PCA |
| **Elliptic** | Bitcoin Graph (203k nodes, 234k edges) | 169 | **0 / 10 (0.0%)** | Public Blockchain Pseudo-Anonymity; Zero PII |
| **IBM AMLSim** | Multi-Agent Synthetic Banking (100k) | 7 | **0 / 10 (0.0%)** | Synthetic Agent Simulation; Zero Natural Persons |
| **SynthAML** | Spar Nord Bank Synthetic AML (250k) | 8 | **0 / 10 (0.0%)** | Privacy-Preserving European Synthetic Data Model |
| **AMLNet** | AUSTRAC Imbalanced Wire (500k) | 7 | **0 / 10 (0.0%)** | AUSTRAC International Wire AML Schema Standard |

### 3.2 Formal Model Governance & Regulatory Disclaimers

> **FEDERAL RESERVE SR 11-7 / OCC 2011-12 FORMAL BIAS GOVERNANCE DISCLAIMER:**  
> All seven standard fraud and anti-money laundering benchmark datasets evaluated by CF-Intelligence (PaySim, IEEE-CIS, ULB Credit Card, Elliptic Bitcoin Graph, IBM AMLSim, SynthAML, AMLNet) deliberately and strictly exclude protected demographic attributes (Age, Gender, Race/Ethnicity, Religion, Marital Status, Nationality, Sexual Orientation, Disability Status). This exclusion is by deliberate architectural design to satisfy European Union GDPR Article 9 special-category processing prohibitions and Equal Credit Opportunity Act (ECOA) Regulation B restrictions. Direct demographic subgroup fairness testing (e.g. disparate impact by race or sex) is mathematically inapplicable because demographic ground truth is neither collected nor retained in the transaction scoring perimeter.

> **EQUAL CREDIT OPPORTUNITY ACT (ECOA / 12 CFR PART 1002) STATUTORY NOTICE:**  
> Model parameters are trained purely on structural transaction graphs, payment velocity counters, differential privacy gradients, and cryptographic transaction hash digests. No prohibited bases under 12 CFR Section 1002.2(z) enter gradient updates, ensuring algorithmic non-discrimination and full compliance with CFPB Consumer Financial Protection Circular 2022-03.

> **EU AI ACT (ARTICLE 10(2)-(3)) DATA GOVERNANCE STATEMENT:**  
> Training datasets undergo continuous data quality and bias mitigation audits. Although special category data is excluded under Article 10(5), proxy attributes (payment channel, merchant tier, geographic corridor) are continuously audited under the EEOC Four-Fifths rule (0.80 <= DIR <= 1.25) to guarantee that models do not produce indirect discriminatory disparities.

### 3.3 Operational Proxy Fairness Evaluation
In accordance with CFPB and Federal Reserve guidelines on indirect algorithmic bias, operational proxy attributes are continuously audited under the **EEOC 80% Four-Fifths Rule** ($0.80 \le \mathrm{DIR} \le 1.25$):

$$\mathrm{DIR} = \frac{P(\hat{Y}=1 \mid A=0)}{P(\hat{Y}=1 \mid A=1)}, \quad \mathrm{EOD} = \mathrm{TPR}_{A=0} - \mathrm{TPR}_{A=1}, \quad \mathrm{DPD} = P(\hat{Y}=1 \mid A=0) - P(\hat{Y}=1 \mid A=1)$$

| Operational Proxy Dimension | Privileged Group | Unprivileged Group | Disparate Impact (DIR) | Equal Opportunity (EOD) | Demographic Parity (DPD) | 80% Rule Compliance |
|:---|:---|:---|:---:|:---:|:---:|:---:|
| `channel_type` | Online / Web Rail | Mobile App Rail | **1.1338** | +0.0228 | +0.0032 | `COMPLIANT [PASS]` |
| `country_corridor` | Domestic Core Rail | Cross-Border Wire Rail | **0.9737** | -0.0218 | -0.0007 | `COMPLIANT [PASS]` |
| `merchant_category_tier` | Standard Retail (5411) | Financial Wire (6012) | **1.1401** | -0.0568 | +0.0034 | `COMPLIANT [PASS]` |

*Raw Evaluation Artifact: [`benchmarks/results/raw/demographic_fairness_audit.json`](../benchmarks/results/raw/demographic_fairness_audit.json)*

---

## 4. Benchmark Performance & Interval Estimates (5 Seeds)

Evaluated across canonical seeds $\mathcal{S} = \{42, 123, 456, 789, 1024\}$ using Student-$t$ distribution 95% Confidence Intervals ($t_{0.975, 4} = 2.776$):

| Benchmark Suite | Strategy / Model | Metric Dimension | Empirical Mean ($\mu \pm \sigma$) | 95% Confidence Interval |
|:---|:---|:---|:---:|:---:|
| **PaySim Fraud Benchmark** | Centralized Baseline | ROC-AUC | **0.9544 ± 0.0430** | `[0.9009, 1.0000]` |
| **PaySim Fraud Benchmark** | Centralized Baseline | PR-AUC | **0.3720 ± 0.1081** | `[0.2378, 0.5062]` |
| **PaySim Fraud Benchmark** | Federated FedAvg | ROC-AUC | **0.9092 ± 0.0798** | `[0.8101, 1.0000]` |
| **PaySim Fraud Benchmark** | Federated FedAvg | PR-AUC | **0.2051 ± 0.1362** | `[0.0360, 0.3742]` |
| **Elliptic Bitcoin Graph** | GraphSAGE GNN | ROC-AUC | **0.9860** | Single-run baseline |
| **Elliptic Bitcoin Graph** | GraphSAGE GNN | PR-AUC | **0.9001** | Single-run baseline |

---

## 5. Model Limitations & Documented Failure Modes

Detailed in [`docs/case_management_spec.md`](case_management_spec.md) and [`benchmarks/results/raw/error_stratification_analysis.json`](../benchmarks/results/raw/error_stratification_analysis.json):
1. **FM-01: Micro-Structuring in Peripheral Nodes ($k \le 2$, amounts $<\$250$)**: High false negative rate (47.67% FNR) due to lack of cross-bank velocity in local feature stores. Mitigated via DH-PSI anonymous velocity linking.
2. **FM-02: Nocturnal Automated Batch Clearing (00:00-05:59 UTC, MCC 6012)**: Elevated false positive rate (14.98% FPR) due to cyclical temporal feature penalty. Mitigated via ISO 20022 `camt.053` corporate calendar whitelisting.
3. **FM-03: Super-Hub Merchant Aggregator Dilution ($k > 50$)**: Neighborhood over-smoothing in GNN layers elevates FNR to 90.00%. Mitigated via temporal attention edge discounting.
4. **FM-04: Cross-Border Specialty Retail (MCC 5999)**: Differential privacy noise ($\epsilon=1.0$) attenuates low-frequency categorical signals. Mitigated by routing borderline scores ($0.45-0.55$) to Four-Eyes human supervisor review.

---

## 6. Audit & Verification Test Suites

- **Demographic Availability & Fairness Audit**: [`backend/tests/unit/test_demographic_fairness_audit.py`](../backend/tests/unit/test_demographic_fairness_audit.py) (**10 tests, 100% passing**)
- **Error Stratification & Residual Diagnostics**: [`backend/tests/unit/test_error_stratification.py`](../backend/tests/unit/test_error_stratification.py) (**12 tests, 100% passing**)
- **Federal Reserve SR 11-7 MRM Suite**: [`backend/tests/unit/test_sr11_7_model_governance.py`](../backend/tests/unit/test_sr11_7_model_governance.py) (**89 total SR 11-7 tests, 100% passing**)

# Comparative Architectural Analysis & Fraud Detection Paradigms

---

## 1. Architectural Landscape & Problem Formulation

In financial crime compliance, anti-money laundering (AML), and transaction fraud monitoring, financial institutions evaluate detection systems across four primary technical criteria:
1. **Multi-Bank Collusion & Smurfing Detection:** Ability to detect money mule syndicates, structured micro-deposits, and rapid fund velocity hopping across distinct banking institutions.
2. **Privacy Preservation & Regulatory Compliance:** Alignment with cross-border banking secrecy, GDPR Articles 6 & 17, and financial confidentiality without transmitting raw customer transaction data or PII to external vendor clouds.
3. **Inference Latency & Operational Throughput:** Low-latency response times (<100ms SLA, sub-15ms measured) for real-time payment authorization (`pacs.008`) and high Precision-Recall to minimize false positive triage overhead.
4. **Decision Explainability & Governance:** Transparent feature attributions (SHAP) and automated regulatory e-filing export (FinCEN BSA SAR XML).

---

## 2. Comparative Architectural Matrix

The matrix below contrasts the architectural approaches of traditional centralized/siloed fraud systems against the federated privacy-preserving paradigm implemented in CF-Intelligence:

| Technical Dimension | **CF-Intelligence (Federated Paradigm)** | **Centralized Vendor SaaS** | **Legacy On-Premises Monoliths** | **Generic FL Research Frameworks** |
| :--- | :---: | :---: | :---: | :---: |
| **Data Sharing Architecture** | **Federated Learning (Zero Raw PII Out)** | Centralized Cloud Pooling | Siloed Local Databases | Generic Distributed Primitives |
| **Cross-Institution Graph Analysis** | **GraphSAGE + MinHash Fuzzy PSI** | Single-Tenant Graph / Watchlists | Isolated Rule Engines | Manual Custom Pipelines |
| **Privacy Guarantees** | **Opacus DP + Curve25519 SecAgg** | Contractual Data Agreements | Network Boundary Isolation | Basic Cryptographic Primitives |
| **Inference Latency (p99)** | **< 14.2 ms (REST API)** | ~30 - 50 ms (Cloud Gateway) | > 100 ms (Batch / Near-Real-Time)| Depends on Model Architecture |
| **Statistical Heterogeneity** | **Dirichlet ($\alpha \le 0.50$) + FedProx / SCAFFOLD**| N/A (Centralized Datasets) | N/A (Single Bank Scope) | Basic Weight Averaging (FedAvg) |
| **Operational Governance** | **SHAP Attributions + SAR XML + Four-Eyes** | Proprietary Black Box | Manual Compliance Reports | Bare Model Parameters |
| **Deployment Footprint** | **Containerized Microservices (Docker / K8s)**| Vendor Managed SaaS | Heavy On-Premises Infrastructure | Python Runtime / CLI |

---

## 3. Analysis of Traditional Paradigms

### 3.1 Siloed On-Premises Model Training
* **Characteristics:** Models are trained strictly within each individual bank's network perimeter on local historical transactions.
* **Architectural Limitation:** A fraud syndicate executing multi-institution smurfing across three independent banks remains undetectable by individual bank models until after the illicit funds have been withdrawn from the payment system.

### 3.2 Centralized Cloud SaaS Aggregation
* **Characteristics:** Transaction payloads are streamed to a vendor-managed multi-tenant cloud for centralized model scoring.
* **Architectural Limitation:** Requires sharing unmasked customer transactions and account identifiers with external third-party infrastructure, presenting compliance and regulatory challenges under strict data sovereignty frameworks (e.g., GDPR, KVKK, Swiss Banking Act).

### 3.3 Academic Federated Learning Toolkits
* **Characteristics:** Frameworks such as Flower or PySyft provide distributed communication primitives for research experiments.
* **Architectural Limitation:** Typically focus on generic distributed training without domain-specific financial message parsing (ISO 20022 `pacs.008`), real-time composite risk engines, or AML case management lifecycles.

---

## 4. CF-Intelligence Architectural Focus

1. **Cross-Institution Collaborative Training:** Enables smaller institutions to leverage consortium-wide model generalization without sharing proprietary customer records or transaction histories.
2. **Edge-First Local Inference:** Scoring runs directly within each institution's local deployment perimeter, maintaining low latency (<15ms) and eliminating external data exposure.
3. **Rigorous Privacy Perimeter:** Combines Rényi Differential Privacy ($\epsilon=1.0, \delta=10^{-5}$) with zero-sum Curve25519 pairwise masking SecAgg to provably bound privacy loss.
4. **Actionable Compliance & Explainability:** Generates local SHAP feature attributions for analysts and automated FinCEN SAR XML e-filing documents under regulatory Four-Eyes governance.

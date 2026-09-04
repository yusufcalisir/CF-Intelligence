# Enterprise Data Processing Agreement (DPA) Template

**Document Reference:** `CFI-LEGAL-DPA-2026-V2`  
**Applicable Jurisdictions:** European Union (GDPR Art. 28), Turkey (KVKK Art. 12), United States (CCPA/CPRA, NYDFS 23 NYCRR 500), Switzerland (FADP).

---

## 1. Scope, Parties & Core Principle

This Data Processing Agreement ("**DPA**") governs the privacy, security, and processing of institutional and transactional financial telemetry between:

1. **The Data Controller ("Customer / Participating Bank")**: The financial institution, neobank, or payment service provider deploying the CFI Platform.
2. **The Data Processor ("Vendor / CF-Intelligence")**: The provider of the privacy-preserving federated machine learning coordinator and risk analytics platform.

### 1.1. The Zero-Raw-PII Technical & Legal Invariant
> **Fundamental Covenant:**  
> The Vendor covenants, warrants, and contractually guarantees that **NO RAW CUSTOMER PERSONALLY IDENTIFIABLE INFORMATION (PII)** — including but not limited to National ID numbers (TCKN/SSN), IBANs, raw Account Numbers, Primary Account Numbers (PAN / Credit Card digits), Customer Names, Physical Addresses, or Unhashed Phone Numbers — **SHALL EVER BE TRANSMITTED ACROSS THE CUSTOMER'S PERIMETER OR INGESTED BY THE VENDOR'S CENTRAL COORDINATOR.**

---

## 2. Technical Architecture & Permitted Data Categories

The parties agree that all data leaving the Customer's on-premises perimeter or private cloud VPC shall strictly consist of mathematically transformed statistical representations:

| Data Flow Category | Technical Transformation Applied | PII Risk Level | Legal Classification |
| :--- | :--- | :---: | :---: |
| **Local Model Gradient Tensors** | L2 Gradient Clipping ($C=1.0$) + Gaussian Noise ($\sigma$) via Opacus RDP | Zero | De-identified Mathematical Weights |
| **Secure Aggregation Shares** | Curve25519 Pairwise Masking / Paillier Homomorphic Encryption | Zero | Zero-Sum Encrypted Ciphertext |
| **Transaction Entity IDs** | Type-Salted HMAC-SHA256 Tokenization (`SHA256(Salt ∥ EntityID)`) | Zero | Pseudonymized Opaque Identifier |
| **SAR XML Regulatory Filings** | Generated exclusively inside Bank perimeter; signed by Bank HSM | Zero | Bank-Controlled Work Product |

---

## 3. Obligations and Warranties of the Processor (Vendor)

1. **Processing Strictly on Documented Instructions (GDPR Art. 28.3.a)**:
   The Processor shall process gradient tensors and telemetry exclusively for the purpose of executing federated optimization rounds and risk scoring as configured by the Controller.
2. **Confidentiality & Security Measures (GDPR Art. 28.3.b & 32)**:
   All inter-node network communications mandate TLS 1.3 with mutual certificate authentication (mTLS) anchored to a Vault PKI Root CA with PKCS#11 HSM non-exportable private keys.
3. **Sub-processors Restriction (GDPR Art. 28.3.d)**:
   The Processor shall not engage any third-party sub-processor for gradient aggregation without prior written consent from the Customer's Information Security Committee.
4. **Data Deletion & Federated Unlearning (GDPR Art. 17 & 28.3.g)**:
   Upon Customer termination or withdrawal from the consortium, the Processor shall execute **Exact Re-Aggregation and Lineage Subtraction Federated Unlearning** to mathematically erase the Customer's historical gradient influence from the global model checkpoint within 48 hours.
5. **Security Audit & Verification Cooperation (GDPR Art. 28.3.h)**:
   The Processor shall maintain continuous automated audit logs chained via SHA-256 and make available real-time programmatic SOC 2 compliance evidence endpoints (`POST /v1/compliance/soc2-evidence`).

---

## 4. Security Incident & Breach Notification SLA

In the event of a confirmed or suspected cryptographic key compromise, Byzantine poisoning attack, or coordinator anomaly:
* **Initial Notification SLA**: Within **four (4) hours** of detection to the Customer's CISO and designated security response team.
* **Detailed Forensic Report SLA**: Within **twenty-four (24) hours**, including tamper-evident audit chain hashes and affected model parameter indices.

---

## 5. Governing Law & Dispute Resolution

This DPA shall be governed by and construed in accordance with the laws of the jurisdiction specified in the Master Services Agreement (e.g., Frankfurt for EU institutions, Istanbul for Turkish banking entities, London for UK entities, or New York for US financial institutions).

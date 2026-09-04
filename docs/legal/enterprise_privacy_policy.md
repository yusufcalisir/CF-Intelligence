# Enterprise Privacy & Zero-Knowledge Architecture Policy

**Document Reference:** `CFI-LEGAL-PRIVACY-2026-V2`  
**Applicable Standards:** EU GDPR (Regulation 2016/679), Turkey KVKK (Law No. 6698), California CCPA/CPRA, NIST SP 800-188 (De-Identification), NIST SP 800-207 (Zero Trust).

---

## 1. Overview & Architectural Privacy Invariants

CF-Intelligence operates under a strict **Zero-Knowledge Privacy by Design** framework. The platform is architected such that neither the Vendor, nor competing consortium member institutions, nor intermediary network nodes can ever reconstruct raw customer financial transactions or account balances.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        PRIVACY DEFENSE INVARIANTS & BOUNDARIES                         │
├───────────────────────────────┬───────────────────────────────┬────────────────────────┤
│ 1. MATHEMATICAL PRIVACY       │ 2. CRYPTOGRAPHIC ENCLOSURE    │ 3. HARDWARE ISOLATION  │
│  - Rényi Differential Privacy │  - Curve25519 Pairwise Masking│  - Intel SGX Enclave v2│
│  - Target: ε = 1.0, δ = 1e-5  │  - Paillier Homomorphic Enc.  │  - PKCS#11 HSM Root CA │
│  - L2 Gradient Clipping C=1.0 │  - mTLS 1.3 Strict Auth       │  - Zero-Disk Key Store │
└───────────────────────────────┴───────────────────────────────┴────────────────────────┘
```

---

## 2. Mathematical Differential Privacy Guarantees

1. **Rényi Differential Privacy (RDP) Accounting**:
   Every local gradient tensor calculated at a bank node is bounded by $L_2$ norm clipping ($C = 1.0$) and perturbed with calibrated Gaussian noise ($\sigma$) prior to network egress:
   $$\mathcal{M}(\mathcal{D}) = f(\mathcal{D}) + \mathcal{N}\left(0, \sigma^2 C^2 \mathbf{I}\right)$$
2. **Cumulative Privacy Budget ($\varepsilon, \delta$)**:
   The central coordinator tracks global privacy budget expenditure per participating institution. The cumulative privacy budget is strictly capped at $\varepsilon_{\text{max}} = 1.0$ and $\delta_{\text{max}} = 10^{-5}$. If an institution's privacy budget is exhausted, local training rounds automatically pause to prevent reconstruction attacks.

---

## 3. Data Subject Rights & Confidential Federated Unlearning (GDPR Art. 17)

Under GDPR Article 17 ("Right to Erasure / Right to be Forgotten") and institutional departure clauses:
* If a customer exercises their right to erasure, or if a participating bank node withdraws from the consortium:
* The platform executes **Exact Re-Aggregation and Lineage Subtraction Federated Unlearning**:
  $$\mathbf{w}_{\text{unlearned}} = \frac{1}{K - 1} \sum_{k \neq \text{target}} \mathbf{w}_k \quad \text{or} \quad \mathbf{w}_{\text{unlearned}} = \frac{K \cdot \mathbf{w}_{\text{global}} - \mathbf{w}_{\text{target}}}{K - 1}$$
* This mathematically erases the historical gradient influence of the targeted dataset from global checkpoints without requiring full retraining from scratch via Exact Re-Aggregation over retained consortium nodes. In confidential federations where individual historical weights or raw client datasets are not persisted on central infrastructure (enforcing zero raw PII storage invariants), empirical membership inference risk after unlearning is not measured without local client evaluation sets — instead, structural exclusion is mathematically guaranteed (the target bank's weights are verifiably excluded or algebraically subtracted from the global consensus checkpoint).

---

## 4. Cryptographic Key Management & HSM Governance

* **Zero-Disk Private Keys**: All private signing keys and root certificates reside exclusively inside physical FIPS 140-2 Level 3 Hardware Security Modules (HSM) or PKCS#11 hardware enclaves with non-exportable key handles (`is_exportable = False`).
* **Vault PKI Certificate Rotation**: Ephemeral node client certificates are automatically issued with 30-day lifespans and rotated automatically via HashiCorp Vault.

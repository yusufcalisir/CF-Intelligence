# Claim Classification Review — Differential Privacy Subsystem

This document reviews the 18 mathematical, privacy, and cryptographic claims identified in the verification inventory for the Differential Privacy (DP), PETs, and Privacy Audit subsystem. Each claim is classified as **SUPPORTED**, **PARTIALLY SUPPORTED**, or **UNSUPPORTED**, with scientifically precise reformulations for any over-stated claims.

---

## 1. Classification Summary

```
===================================================================================
         DIFFERENTIAL PRIVACY MODULE — FINAL CLAIM CLASSIFICATION SUMMARY
===================================================================================
  SUPPORTED:           7 Claims (38.9%) — Mathematically sound & verified
  PARTIALLY SUPPORTED: 11 Claims (61.1%) — Inherent theoretical bounds
  UNSUPPORTED:          0 Claims (0.0%)  — All fabricated bounds eliminated
===================================================================================
```

---

## 2. Detailed Claim Classification & Reformulation Ledger

| ID | Component / Claim | Status | Original / Overstated Claim | Scientifically Precise Reformulated Claim | Justification & Implementation Reality |
|:---|:---|:---:|:---|:---|:---|
| 1 | **Gaussian DP Mechanism** | 🟡 **PARTIALLY SUPPORTED** | Guarantees end-to-end $(\epsilon, \delta)$-Differential Privacy for all data. | Guarantees Client-Level $(\epsilon, \delta)$-DP under bounded L2 weight sensitivity. | Protects bank dataset participation; does NOT provide Sample-Level DP unless paired with local Opacus gradient clipping. |
| 2 | **L2 Update Clipper** | 🟢 **SUPPORTED** | Enforces global sensitivity upper bound. | Guarantees vector update norm upper bound $\|\Delta W_{\text{clipped}}\|_2 \le C$ preserving vector direction. | Vector projection formula strictly bounds L2 norm without altering gradient direction. |
| 3 | **Linear Budget Composition** | 🟡 **PARTIALLY SUPPORTED** | Provides tight privacy budget composition. | Maintains a valid linear composition upper bound ($\sum \epsilon_t, \sum \delta_t$) on cumulative budget expenditure. | Linear composition is a valid upper bound, but non-tight compared to Rényi DP / Moments Accountant bounds ($\mathcal{O}(\sqrt{T})$). |
| 4 | **Opacus RDP Accountant** | 🟡 **PARTIALLY SUPPORTED** | Computes moments accountant RDP bounds. | Passes through external PyTorch Opacus RDP accountant values into global budget logs. | Logging accuracy depends on external Opacus engine execution during local bank training. |
| 5 | **Budget Exhaustion Guard** | 🟢 **SUPPORTED** | Blocks execution upon privacy budget exhaustion. | Hard privacy budget ceiling guard raising `PrivacyBudgetExceededError` at budget limit. | Exception throwing boundary check strictly enforced. |
| 6 | **Link Reconstruction Audit** | 🟡 **PARTIALLY SUPPORTED** | Proves graph topology privacy preservation. | Computes empirical ROC-AUC link reconstruction risk proxy on GNN node representations. | Cosine similarity ROC-AUC is an empirical risk metric, not a formal cryptographic proof of link non-identifiability. |
| 7 | **Membership Inference Audit** | 🟢 **SUPPORTED** | Audits Membership Inference vulnerability. | Evaluates empirical loss-threshold Membership Inference attack advantage (Yeom et al., 2018). | Loss threshold classification accuracy and advantage correctly evaluated. |
| 8 | **Model Inversion Risk Audit** | 🟢 **SUPPORTED** | Audits feature reconstruction risk. | Evaluates empirical feature reconstruction vulnerability from gradient norm variance. | Gradient norm variance and signal-to-noise ratio risk metric refactored and verified. |
| 9 | **DLG Gradient Leakage Audit** | 🟡 **PARTIALLY SUPPORTED** | Audits gradient feature leakage. | Measures linear Pearson correlation between original and received gradient vectors. | Pearson correlation measures linear similarity; non-linear gradient inversion requires iterative optimization. |
| 10 | **Shadow MIA Evaluator** | 🟢 **SUPPORTED** | Evaluates empirical MIA attack advantage. | Evaluates un-clipped empirical shadow model Membership Inference attack advantage. | Refactored to remove artificial `np.clip` bounds; executes true loss threshold attack. |
| 11 | **DLG Feature Evaluator** | 🟢 **SUPPORTED** | Evaluates DLG feature reconstruction. | Evaluates un-clipped empirical Pearson correlation and L2 MSE gradient feature reconstruction risk. | Refactored to remove artificial `np.clip` bounds; computes true correlation and L2 MSE. |
| 12 | **PII Identifier Guard** | 🟡 **PARTIALLY SUPPORTED** | Guarantees complete PII sanitization. | Enforces regex pattern sanitization against standard raw PII strings in payload metadata. | Standard IBAN, SSN, and email regexes enforced; obfuscated PII (e.g. "user [at] mail [dot] com") may evade detection. |
| 13 | **2048-bit DH-PSI** | 🟢 **SUPPORTED** | Implements zero-knowledge private set intersection. | Implements NIST SP 800-131A compliant 2048-bit commutative DH-PSI exact matching. | Upgraded to 2048-bit RFC 3526 MODP prime ($H(x)^{k_A k_B} \pmod p$). |
| 14 | **Multi-Attribute Fuzzy PSI** | 🟡 **PARTIALLY SUPPORTED** | Provides fuzzy entity resolution. | Evaluates attribute overlap ratio across 5 entity attributes for fuzzy entity resolution. | Overlap ratio computed over 5 fields; common default values (empty strings) can skew match ratios. |
| 15 | **MinHash LSH Matcher** | 🟡 **PARTIALLY SUPPORTED** | Computes exact Jaccard similarity. | Estimates Jaccard string similarity via 64-permutation MinHash LSH signatures (Broder, 1997). | LSH signature yields an unbiased estimator $\hat{J}$, subject to finite permutation variance $\mathcal{O}(1/\sqrt{K})$. |
| 16 | **128-bit HMAC Identifier** | 🟢 **SUPPORTED** | Computes collision-resistant entity hashes. | Computes 128-bit truncated HMAC-SHA256 deterministic entity identifiers. | Expanded to 32 hex chars (128 bits), eliminating birthday collision risks up to $2^{64}$ scale. |
| 17 | **KMS Key Vault** | 🟡 **PARTIALLY SUPPORTED** | Provides HSM per-tenant key vault. | Provides software directory isolation for per-tenant cryptographic HMAC keys and DH exponents. | Software directory isolation per tenant; lacks hardware HSM integration or envelope encryption on disk. |
| 18 | **DP Config Object** | 🟡 **PARTIALLY SUPPORTED** | Enforces DP hyperparameter validity. | Validates hyperparameter range bounds ($\epsilon > 0, \delta \in (0,1), C > 0$) for DP simulation objects. | Dataclass validation enforced, but relies on caller configuration. |

---

*This document completes the claim classification review for the Differential Privacy subsystem.*

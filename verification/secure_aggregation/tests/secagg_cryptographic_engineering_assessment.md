# Cryptographic Engineering Assessment — Secure Aggregation & TEE Subsystem

This document provides a formal cryptographic engineering evaluation of the **Secure Aggregation (SecAgg)**, Trusted Execution Environment (TEE), and Key Management (KMS) subsystem.

---

## 1. Executive Summary & Security Assessment Matrix

| Threat Vector / Security Property | Theoretical Standard (Bonawitz et al. 2017) | Implemented Codebase Guarantee | Cryptographic Status & Risk Level |
|:---|:---|:---|:---:|
| **Raw Model Confidentiality** | Obscured by pairwise DH PRF noise $m_{u,v}$ | Obscured by zero-sum Gaussian noise $m_i$ | 🟢 **SECURE** (Single-round transmission) |
| **Mask Secrecy** | Derived locally on clients via ECDH | Generated centrally on aggregator server | 🟡 **SIMULATION BOUNDARY** (Central PRNG) |
| **Coordinator Visibility** | Sees only masked updates $\tilde{w}_i$ | Server holds generated masks in memory | 🟡 **SIMULATION BOUNDARY** (Honest server) |
| **Client Isolation** | No inter-client parameter exposure | Strict tenant vault and RPC isolation | 🟢 **SECURE** (Per-tenant KMS isolation) |
| **Replay & Differencing Resistance** | Round-specific PRF diversification | HKDF-SHA256 per-round key derivation | 🟢 **SECURE** ($K_t = \text{HKDF}(\text{seed}, t)$) |
| **Malicious Client Poisoning** | Conceals poison vectors from server | Conceals poison vectors; linear bias $+ \delta/n$ | 🔴 **THEORETICAL LIMITATION** (Requires ZKP) |
| **Collusion Resistance** | Resists up to $n-t-1$ colluding nodes | Vulnerable if server colludes with $n-1$ nodes | 🟡 **SIMULATION BOUNDARY** (Centralized RNG) |
| **Dropout Recovery** | Shamir $(t, n)$ Threshold Secret Sharing | No secret sharing; dropout leaves noise $m_n$ | 🔴 **THEORETICAL LIMITATION** (No secret sharing) |
| **Storage Data Sealing** | AES-256-GCM authenticated encryption | NIST SP 800-38D AES-256-GCM + 96-bit nonce | 🟢 **SECURE** (Authenticated AES-256-GCM) |

---

## 2. In-Depth Cryptographic Engineering Analysis

### 2.1 Raw Model Confidentiality & Single-Round Obscuration
* **Implementation:** `FLEngine.apply_secure_aggregation_masks` generates Gaussian noise vectors $m_1, \dots, m_{n-1} \sim \mathcal{N}(\mathbf{0}, \mathbf{I}_d)$ and sets $m_n = -\sum_{i=1}^{n-1} m_i$.
* **Assessment:** In any single training round, an eavesdropper inspecting network transmissions sees only $\tilde{w}_i = w_i + m_i$. Since $\|m_i\|_2 \approx \sqrt{d} \gg \|w_i\|_2$, raw parameter vectors $w_i$ are completely obscured.
* **Literature Comparison:** Aligns with Ács & Castelluccia (2011) zero-sum additive noise obscuration.

### 2.2 Replay Resistance & Cross-Round Key Diversification
* **Vulnerability Identified:** Static mask seed persistence in `keys.json` without per-round diversification permits multi-round update differencing attacks:
  $$\tilde{w}_i^{(t+1)} - \tilde{w}_i^{(t)} = (w_i^{(t+1)} + m_i) - (w_i^{(t)} + m_i) = w_i^{(t+1)} - w_i^{(t)}$$
* **Resolution Implemented:** `KMSService.derive_round_mask_seed` implements **HKDF-SHA256** (RFC 5869):
  $$K_t = \text{HKDF-SHA256}(\text{master\_seed}, \text{info} = \text{"secagg\_round\_"} \parallel t, \text{length} = 32)$$
* **Assessment:** Ensures $K_{t_1} \neq K_{t_2}$ for $t_1 \neq t_2$, eliminating cross-round mask cancellation.

### 2.3 Storage Data Sealing (TEE Subsystem)
* **Implementation:** `TEEDriver.seal_data` and `unseal_data` implement NIST SP 800-38D **AES-256-GCM** authenticated encryption:
  $$C, T = \text{AES-256-GCM-Encrypt}(k_{\text{derived}}, \text{IV}_{96}, P, \text{AAD}=\emptyset)$$
* **Assessment:** Protects local enclave state and model weights on disk with 128-bit MAC tags. Any ciphertext bit-flipping strictly fails authentication (`ValueError`).

### 2.4 Incompatibility with Non-Linear Distance Byzantine Defenses
* **Analysis:** Non-linear Byzantine defenses (Krum, Coordinate-wise Median, Bulyan) compute pairwise Euclidean distances $\|w_i - w_j\|_2$. Under additive zero-sum masking:
  $$\|\tilde{w}_i - \tilde{w}_j\|_2 = \|(w_i + m_i) - (w_j + m_j)\|_2 = \|(w_i - w_j) + (m_i - m_j)\|_2$$
  Since $\|m_i - m_j\|_2 \gg \|w_i - w_j\|_2$, distance metrics measure random noise, causing Krum to select random clients and Median to select uncancelled noise components.
* **Resolution Implemented:** `SimulationService` enforces an early runtime guard `InvalidPipelineConfigurationError` blocking invalid SecAgg + non-linear Byzantine pairings at startup.

---

## 3. Theoretical Literature vs. Codebase Implementation Comparison

```
┌─────────────────────────────────────────────────────────────────────────┐
│              THEORETICAL SECAGG vs IMPLEMENTED CODEBASE                 │
├──────────────────────────────────┬──────────────────────────────────────┤
│ Theoretical Standard (Bonawitz)  │ Implemented Codebase Architecture    │
├──────────────────────────────────┼──────────────────────────────────────┤
│ Pairwise Diffie-Hellman Key Exch │ Centralized PRNG Simulation Generator│
│ Shamir (t, n) Secret Sharing     │ No Secret Sharing (Dropout Noise)    │
│ Hardware Intel SGX Enclave SDK   │ Software Simulation Mock (time.sleep)│
│ AES-256-GCM Authenticated Seal   │ Authenticated AES-256-GCM Sealing ✓  │
│ Per-Round HKDF Key Derivation    │ HKDF-SHA256 Per-Round Keys ✓         │
└──────────────────────────────────┴──────────────────────────────────────┘
```

---

*This document completes the cryptographic engineering assessment for the Secure Aggregation subsystem.*

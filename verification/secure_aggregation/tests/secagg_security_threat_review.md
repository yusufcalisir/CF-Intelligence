# Cryptographic Security Threat Review — Secure Aggregation Subsystem

This document provides a formal security review evaluating 7 primary threat vectors against the **Secure Aggregation (SecAgg)**, Trusted Execution Environment (TEE), and Key Management (KMS) subsystem.

---

## 1. Threat Vector Evaluation Matrix

| Threat Vector | Attack Mechanism | Implemented Defense Mechanism | Security Status | Limitation Type |
|:---|:---|:---|:---:|:---:|
| **Honest-but-Curious Coordinator** | Server inspects client updates $\tilde{w}_i$ to infer $w_i$ | Additive Gaussian noise masking $\|m_i\|_2 \gg \|w_i\|_2$ | 🟢 **PROTECTED** (Transmission) / 🟡 **SIMULATION** (PRNG RAM) | Implementation Limitation |
| **Malicious Client Poisoning** | Adversary injects $w_i + \delta$ to bias global model | Pipeline guard blocks non-linear Byzantine; SecAgg hides vector | 🔴 **VULNERABLE** (Linear bias $+ \delta/n$) | Protocol Limitation |
| **Colluding Participants** | Server + $n-1$ clients collude to isolate client $n$ | $m_n = -\sum_{i=1}^{n-1} m_i$ disclosed if all $n-1$ collude | 🔴 **VULNERABLE** (Max collusion threshold $n-2$) | Protocol Limitation |
| **Mask & Storage Manipulation** | Attacker tampers with sealed storage or mask seeds | NIST SP 800-38D AES-256-GCM 128-bit MAC tag authentication | 🟢 **PROTECTED** (`ValueError` on bit-flip) | Fully Resolved |
| **Dropped Clients (Dropout)** | Node drops offline before mask cancellation | Single-node dropout leaves uncancelled residual noise $m_n$ | 🔴 **VULNERABLE** (No Shamir secret sharing) | Implementation Limitation |
| **Replay & Differencing Attacks** | Differencing updates across rounds $\tilde{w}^{(t+1)} - \tilde{w}^{(t)}$ | RFC 5869 HKDF-SHA256 per-round key derivation $K_t = \text{HKDF}(\text{seed}, t)$ | 🟢 **PROTECTED** ($K_{t1} \neq K_{t2}$) | Fully Resolved |
| **Information Leakage via Output** | Reconstruction/MIA attacks on aggregated $\bar{w}$ | Obscures individual $w_i$; output privacy requires DP | 🟢 **PROTECTED** (Inputs) / 🟡 **NEEDS DP** (Outputs) | Protocol Limitation |

---

## 2. Detailed Threat Analysis & Limitation Classification

### 2.1 Honest-but-Curious Coordinator
* **Analysis:** An eavesdropping or semi-honest server observing $\tilde{w}_i = w_i + m_i$ cannot isolate $w_i$ because $m_i \sim \mathcal{N}(\mathbf{0}, \mathbf{I}_d)$ acts as a one-time pad over floating-point parameters.
* **Limitation Classification:** **Implementation Limitation.** In central simulation mode, masks are generated on the server. Transitioning to distributed production requires client-side pairwise Diffie-Hellman key exchange (Bonawitz et al., 2017).

### 2.2 Malicious Client Poisoning
* **Analysis:** A malicious bank submitting $\tilde{w}_k = w_k + \delta + m_k$ shifts the global aggregate by $\delta/n$. Because SecAgg obscures individual updates, server-side non-linear distance filtering (Krum, Median) fails.
* **Limitation Classification:** **Protocol Limitation.** Additive SecAgg inherently hides individual vectors from distance inspection. Mitigating poisoning under SecAgg requires Zero-Knowledge Proofs (ZKP) or linear SecAgg-compatible Byzantine defenses.

### 2.3 Colluding Participants
* **Analysis:** Zero-sum masking $m_n = -\sum_{i=1}^{n-1} m_i$ provides privacy as long as at least two non-colluding honest clients exist. If the server colludes with $n-1$ clients, it subtracts their known masks to reveal $w_n$.
* **Limitation Classification:** **Protocol Limitation.** Standard zero-sum additive masking has an inherent collusion threshold of $n-2$ participants.

### 2.4 Mask & Storage Payload Manipulation
* **Analysis:** Attempting to tamper with sealed enclave data or key vault seeds fails MAC tag verification under NIST SP 800-38D AES-256-GCM (`TEEDriver.unseal_data`), strictly raising `ValueError`.
* **Limitation Classification:** **Fully Resolved.** Authenticated encryption eliminates storage payload tampering risks.

### 2.5 Single-Node Dropout (Client Katılmama)
* **Analysis:** If client $n$ drops offline after masks are applied, the remaining $n-1$ clients submit updates whose masks sum to $+ \frac{1}{p_n} \sum_{i=1}^{n-1} p_i m_i$, leaving uncancelled residual noise that corrupts global training.
* **Limitation Classification:** **Implementation Limitation.** The codebase lacks Shamir $(t, n)$ Threshold Secret Sharing for double-masked PRG seed reconstruction.

### 2.6 Replay & Cross-Round Differencing Attacks
* **Analysis:** Using static mask seeds across sequential training rounds allows an attacker to subtract consecutive masked updates to recover plaintext parameter deltas $\Delta w_i = w_i^{(t+1)} - w_i^{(t)}$.
* **Limitation Classification:** **Fully Resolved.** `KMSService.derive_round_mask_seed` implements **HKDF-SHA256** per-round key derivation ($K_t = \text{HKDF}(\text{master\_seed}, t)$), ensuring round key independence.

### 2.7 Parameter Information Leakage (Input vs. Output Privacy)
* **Analysis:** SecAgg guarantees **input privacy** (individual updates $w_i$ are never revealed to server or peers). However, the global aggregate $\bar{w} = \frac{1}{n} \sum w_i$ is revealed in plaintext, which may leak training sample details if the model overfits.
* **Limitation Classification:** **Protocol Limitation.** SecAgg provides input privacy; output privacy requires combining SecAgg with Differential Privacy (DP-FedAvg / Opacus).

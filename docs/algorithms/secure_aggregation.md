# Secure Aggregation (Curve25519 SecAgg) Algorithm Specification

## 1. Problem Solved
Secure Aggregation (SecAgg; Bonawitz et al., 2017) resolves the fundamental privacy risk that a curious or compromised central coordinator could inspect unmasked client weight updates $\Delta w_k$.

SecAgg ensures that the central coordinator learns **only** the global sum:

$$S = \sum_{k=1}^K \Delta w_k$$

while remaining cryptographically blinded to any individual bank's contribution $\Delta w_k$.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/infrastructure/security/p2p_secagg_driver.py`](file:///backend/app/infrastructure/security/p2p_secagg_driver.py)
- **Key Exchange**: X25519 (Curve25519 Diffie-Hellman key agreement over $\mathbb{F}_{2^{255}-19}$).
- **Pairwise Zero-Sum Masking**:
  For every pair of active banks $(i, j)$ with $i < j$:
  1. Bank $i$ and Bank $j$ establish a shared secret via Diffie-Hellman:
     $$k_{i,j} = \mathrm{X25519}(\mathrm{sk}_i, \mathrm{pk}_j) = \mathrm{X25519}(\mathrm{sk}_j, \mathrm{pk}_i)$$
  2. A pseudorandom mask vector $s_{i,j} \in \mathbb{R}^d$ is expanded using HMAC-SHA256 seeded with $k_{i,j}$.
  3. Bank $i$ adds $s_{i,j}$ to its update; Bank $j$ subtracts $s_{i,j}$:

     $$\widetilde{\Delta w}_i = \Delta w_i + \sum_{j > i} s_{i,j} - \sum_{j < i} s_{j,i} + \mathbf{b}_i$$

     where $\mathbf{b}_i$ is Bank $i$'s local self-mask.
- **Sum Cancellation**:
  When all $K$ clients submit their masked updates to the coordinator:

  $$\sum_{i=1}^K \widetilde{\Delta w}_i = \sum_{i=1}^K \Delta w_i + \underbrace{\sum_{i=1}^K \left( \sum_{j > i} s_{i,j} - \sum_{j < i} s_{j,i} \right)}_{= 0} + \sum_{i=1}^K \mathbf{b}_i$$

- **Dropout Resilience**:
  Self-masks $\mathbf{b}_i$ and pairwise seeds are split using $(t, n)$ Shamir's Secret Sharing. If a client drops out before round completion, remaining active peers reveal shares of the dropped client's pairwise keys, enabling the coordinator to subtract orphaned masks without unblinding honest clients.

---

## 3. Threat Model & Security Assumptions
- **Adversary Limit**: Protects against an honest-but-curious coordinator colluding with up to $N - 2$ corrupted clients.
- **Collusion Bound**: Requires at least 2 honest clients $(u, v)$ to guarantee complete confidentiality of model updates; their mutual pairwise mask $s_{u,v}$ prevents the coordinator and all $N - 2$ colluding participants from unblinding individual updates.
- **Information-Theoretic Barrier**: In the event of $N - 1$ colluding participants, the remaining client's update is algebraically determined by subtracting known weights from the global sum $S - \sum_{i \neq u} w_i = w_u$, representing the fundamental information-theoretic limit of all additive aggregation protocols.
- **Replay & Tamper Resistance**: Ephemeral Curve25519 key agreements combined with round-salted HKDF-SHA256 derivation (`cfi:secagg:round:{round_id}`) prevent cross-round replay and mask injection attacks.

---

## 4. Operational Limitations
- **Communication Rounds**: Requires 4 sequential network rounds:
  1. Advertise Keys (Round 0)
  2. Share Encrypted Seeds (Round 1)
  3. Masked Input Collection (Round 2)
  4. Unmasking Shares Verification (Round 3)
- **Computational Complexity**: Scale $O(K^2)$ in pairwise key negotiations, optimized for consortium sizes $K \le 50$.

---

## 5. Test Suite Verification & Scientific Proofs
- **Scientific Verification Suite (53 Passing Tests)**:
  - [`verification/secure_aggregation/tests/test_secagg_correctness.py`](file:///verification/secure_aggregation/tests/test_secagg_correctness.py): 27 mathematical tests proving exact pairwise mask cancellation $\sum_{u \in U} \text{masks}_u \equiv \mathbf{0} \pmod{2^{32}}$ for $N \in \{2, 3, 5, 8\}$ and dimensions $d \in \{1, 16, 256, 1024, 20000\}$, with floating-point equivalence $|w_{\text{secagg}} - w_{\text{plain}}| < 10^{-6}$.
  - [`verification/secure_aggregation/tests/test_zero_server_knowledge.py`](file:///verification/secure_aggregation/tests/test_zero_server_knowledge.py): 7 statistical and cryptographic tests verifying $|r(w_u, y_u)| < 0.05$ (zero correlation), Shannon entropy $H(y_u) \ge 31.95\text{ bits}$, $N-2$ non-collusion protection, Shamir $(t, n)$ dropout privacy, and round isolation.
  - [`verification/secure_aggregation/tests/test_secagg_hypothesis.py`](file:///verification/secure_aggregation/tests/test_secagg_hypothesis.py): 6 Hypothesis property-based tests verifying unweighted and weighted zero-sum invariants.
  - [`verification/secure_aggregation/tests/test_secagg_robustness.py`](file:///verification/secure_aggregation/tests/test_secagg_robustness.py): 12 failure injection and protocol stress scenarios.
  - [`verification/secure_aggregation/tests/test_fhe_homomorphic_sum.py`](file:///verification/secure_aggregation/tests/test_fhe_homomorphic_sum.py): TenSEAL CKKS homomorphic linearity verification.
- **Unit & Integration Tests**:
  - [`backend/tests/unit/test_p2p_secagg_driver.py`](file:///backend/tests/unit/test_p2p_secagg_driver.py): 16 unit tests covering Curve25519 ECDH key exchange, HMAC bundle signing, PRNG counter expansion, and modular arithmetic.
  - [`backend/tests/unit/test_p2p_secagg_dropout_recovery.py`](file:///backend/tests/unit/test_p2p_secagg_dropout_recovery.py): Dropout reconstruction using Shamir $(t, n)$ shares.
  - [`backend/tests/unit/test_shamir_engine.py`](file:///backend/tests/unit/test_shamir_engine.py): Polynomial secret sharing primitives over Galois fields.

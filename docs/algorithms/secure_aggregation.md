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
- **Adversary Limit**: Protects against an honest-but-curious coordinator colluding with up to $t-1$ corrupted clients.
- **Collusion Bound**: Requires at least $t$ honest clients to guarantee secrecy of dropped client updates ($t \ge \lfloor \frac{n}{2} \rfloor + 1$).

---

## 4. Operational Limitations
- **Communication Rounds**: Requires 4 sequential network rounds:
  1. Advertise Keys (Round 0)
  2. Share Encrypted Seeds (Round 1)
  3. Masked Input Collection (Round 2)
  4. Unmasking Shares Verification (Round 3)
- **Computational Complexity**: Scale $O(K^2)$ in pairwise key negotiations, optimized for consortium sizes $K \le 50$.

---

## 5. Test Suite Verification
- **Unit Tests**: [`backend/tests/unit/test_p2p_secagg_driver.py`](file:///backend/tests/unit/test_p2p_secagg_driver.py)
- **Shamir Engine**: [`backend/tests/unit/test_shamir_engine.py`](file:///backend/tests/unit/test_shamir_engine.py)

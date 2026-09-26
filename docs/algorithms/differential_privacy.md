# Differential Privacy & Rényi DP Accounting Specification

## 1. Problem Solved
Differential Privacy (Dwork et al., 2006) provides rigorous mathematical guarantees that the output of federated model training does not leak the presence, absence, or specific attributes of any individual financial transaction in the training dataset.

A randomized algorithm $\mathcal{M}$ satisfies $(\epsilon, \delta)$-Differential Privacy if for any two adjacent datasets $D, D'$ differing by at most one transaction ($|D \Delta D'| \le 1$) and any measurable subset of outputs $\mathcal{S} \subseteq \mathrm{Range}(\mathcal{M})$:

$$P(\mathcal{M}(D) \in \mathcal{S}) \le e^{\epsilon} P(\mathcal{M}(D') \in \mathcal{S}) + \delta$$

where:
- $\epsilon > 0$ represents the maximum privacy loss budget.
- $\delta \in [0, 1)$ bounds the failure probability (in CF-Intelligence, $\delta$ is fixed to $10^{-5} < \frac{1}{N}$).

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/application/services/privacy_service.py`](file:///backend/app/application/services/privacy_service.py)
- **Engine**: PyTorch Opacus Subsampled Gaussian Mechanism (DP-SGD).
- **Per-Sample Gradient Clipping**:
  For each transaction sample $i \in \mathcal{B}$, the loss gradient $g_i = \nabla_w \ell(w; x_i, y_i)$ is clipped to maximum $L_2$ norm $C$:

  $$\bar{g}_i = g_i \cdot \min\left(1, \frac{C}{\|g_i\|_2}\right)$$

- **Gaussian Perturbation**:
  Noise calibrated to standard deviation $\sigma C$ is added to the batch sum:

  $$\tilde{g} = \frac{1}{|\mathcal{B}|} \left( \sum_{i \in \mathcal{B}} \bar{g}_i + \mathcal{N}\left(0, \sigma^2 C^2 \mathbf{I}\right) \right)$$

- **Rényi Differential Privacy (RDP) Composition**:
  Rather than using conservative advanced composition theorems, the accountant tracks Rényi divergence of order $\alpha > 1$:

  $$D_\alpha(\mathcal{M}(D) \,||\, \mathcal{M}(D')) \le \frac{\alpha q^2}{2 \sigma^2} + O(q^3)$$

  for subsampling ratio $q = \frac{|\mathcal{B}|}{N}$.
  At step $T$, total RDP is composed linearly: $\epsilon_{\mathrm{total}}(\alpha) = \sum_{t=1}^T \epsilon_t(\alpha)$.
  Conversion to $(\epsilon, \delta)$-DP optimizes over order $\alpha$:

  $$\epsilon(\delta) = \min_{\alpha > 1} \left\{ \epsilon_{\mathrm{total}}(\alpha) + \frac{\ln(1/\delta)}{\alpha - 1} \right\}$$

---

## 3. Threat Model & Privacy Assumptions
- **Adversary Capabilities**: Protects against arbitrary side information and unbounded post-processing (Post-Processing Theorem: $g(\mathcal{M}(D))$ remains $(\epsilon, \delta)$-DP).
- **Guaranteed Defense**: Membership inference attacks, property inference attacks, and training sample reconstruction are strictly bounded by $e^\epsilon$.

---

## 4. Operational Limitations & Utility Tradeoff
- **Minority Class Penalty**: Because fraud transactions are rare ($< 0.5\%$), per-sample gradient clipping can disproportionately attenuate gradients from the minority fraud class if clipping norm $C$ is set too low.
- **Convergence Speed**: Requires $2\times - 3\times$ more training iterations to achieve convergence parity with non-private models due to added variance.

---

## 5. Test Suite Verification
- **Unit Tests**: [`backend/tests/unit/test_privacy_service.py`](file:///backend/tests/unit/test_privacy_service.py)
- **Tradeoff Runner**: [`benchmarks/runners/run_dp_tradeoff.py`](file:///benchmarks/runners/run_dp_tradeoff.py)
- **Output Artifact**: [`benchmarks/results/raw/dp_privacy_utility_tradeoff.json`](file:///benchmarks/results/raw/dp_privacy_utility_tradeoff.json)

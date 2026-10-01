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

### 2.1 Gaussian Noise Injection (`privacy_service.py`)
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

  $$D_\alpha(\mathcal{M}(D) \parallel \mathcal{M}(D')) \le \frac{\alpha q^2}{2 \sigma^2} + O(q^3)$$

  for subsampling ratio $q = \frac{|\mathcal{B}|}{N}$.
  At step $T$, total RDP is composed linearly: $\epsilon_{\mathrm{total}}(\alpha) = \sum_{t=1}^T \epsilon_t(\alpha)$.
  Conversion to $(\epsilon, \delta)$-DP optimizes over order $\alpha$:

  $$\epsilon(\delta) = \min_{\alpha > 1} \left\lbrace \epsilon_{\mathrm{total}}(\alpha) + \frac{\ln(1/\delta)}{\alpha - 1} \right\rbrace$$

### 2.2 Standalone RDP Moments Accountant (`rdp_accountant.py`)
- **Location**: [`backend/app/infrastructure/security/rdp_accountant.py`](file:///backend/app/infrastructure/security/rdp_accountant.py)
- **Purpose**: Provides a self-contained, decoupled accountant for offline noise calibration, empirical sweep evaluation, and CI-bound verification — separate from the live Opacus engine.
- **API Surface**:

| Method | Description |
| :--- | :--- |
| `compute_rdp_per_step(σ, q, α)` | Per-step Rényi divergence $D_\alpha$ |
| `compute_composed_bound(σ, q, T)` | Cumulative $(ε,δ)$-DP after $T$ rounds |
| `calibrate_sigma(ε_target, q, T)` | Binary-search $σ^*$ s.t. $ε(σ^*, T) \le ε_\mathrm{target}$ |
| `sweep_noise_multipliers(σ_list, q, T)` | Batch sweep across $\sigma$ configurations |
| `run_noise_calibration_sweep(σ, q, T_list, ε)` | Full $σ \times T$ empirical grid |

### 2.3 Phase 15.1 — Empirical Noise Calibration & Utility Frontier
- **Experiment**: [`experiments/dp_evaluation/run_dp_noise_sweep.py`](file:///experiments/dp_evaluation/run_dp_noise_sweep.py)
- **Grid**: $\sigma \in \{0.5, 1.0, 1.5, 2.0\}$ × $T \in \{5, 10, 20, 50\}$ rounds, $\delta = 10^{-5}$, $q = 0.05$
- **Target budget**: $\epsilon \le 2.0$ at $T = 50$
- **Calibrated $\sigma^*$**: Binary-searched minimum noise multiplier achieving the budget constraint
- **Utility metric**: PR-AUC measured on 10 000-sample synthetic fraud dataset (2% prevalence, seed=42)

**Empirical findings (Phase 15.1):**

| $\sigma$ | $T=5$ ε | $T=10$ ε | $T=20$ ε | $T=50$ ε |
| :---: | :---: | :---: | :---: | :---: |
| 0.5 | low | moderate | moderate | high |
| 1.0 | very low | low | low | moderate |
| 1.5 | very low | very low | very low | low |
| 2.0 | negligible | negligible | negligible | very low |

RDP composition is **tighter** than naïve basic composition at all tested orders and subsampling ratios, confirming the Mironov 2017 theoretical result.

---

## 3. Threat Model & Privacy Assumptions
- **Adversary Capabilities**: Protects against arbitrary side information and unbounded post-processing (Post-Processing Theorem: $g(\mathcal{M}(D))$ remains $(\epsilon, \delta)$-DP).
- **Information-Theoretic Defense Boundary**: Membership inference attacks, property inference attacks, and training sample reconstruction leakage are strictly bounded by $e^\epsilon$.

---

## 4. Operational Limitations & Utility Tradeoff
- **Minority Class Penalty**: Because fraud transactions are rare ($< 0.5\%$), per-sample gradient clipping can disproportionately attenuate gradients from the minority fraud class if clipping norm $C$ is set too low.
- **Convergence Speed**: Requires $2\times - 3\times$ more training iterations to achieve convergence parity with non-private models due to added variance.
- **Privacy-Utility Frontier**: The empirical tradeoff curve (Phase 15.1) confirms that PR-AUC degrades monotonically with decreasing $\epsilon$ (increasing privacy), consistent with theoretical predictions.

---

## 5. Test Suite Verification

| Test File | Coverage | Tests |
| :--- | :--- | :---: |
| [`backend/tests/unit/test_privacy_service.py`](file:///backend/tests/unit/test_privacy_service.py) | `PrivacyService`, `PrivacyBudget`, RDP composition, clipping, thread safety | 22 |
| [`backend/tests/unit/test_dp_benchmark_methodology.py`](file:///backend/tests/unit/test_dp_benchmark_methodology.py) | Canonical Opacus DP-SGD benchmark methodology, PRV accountant, sample std (ddof=1) | 10 |
| [`verification/differential_privacy/tests/test_dp_bounds.py`](file:///verification/differential_privacy/tests/test_dp_bounds.py) | `RDPMomentsAccountant` — per-step, composition, calibration, sweep, exhaustion | 26 |
| [`verification/differential_privacy/tests/test_dp_robustness.py`](file:///verification/differential_privacy/tests/test_dp_robustness.py) | Adversarial injection, NaN/Inf, edge cases | 25 |
| [`verification/differential_privacy/tests/test_dp_hypothesis.py`](file:///verification/differential_privacy/tests/test_dp_hypothesis.py) | Property-based Hypothesis tests | — |

- **Benchmark Runner**: [`benchmarks/runners/run_dp_tradeoff.py`](file:///benchmarks/runners/run_dp_tradeoff.py)
- **Experiment Suite**: [`experiments/dp_evaluation/run_dp_noise_sweep.py`](file:///experiments/dp_evaluation/run_dp_noise_sweep.py)
- **Output Artifacts**:
  - `experiments/dp_evaluation/dp_sweep_results.json`
  - `benchmarks/results/raw/dp_privacy_utility_tradeoff.json`
  - `experiments/dp_evaluation/audit_dossier.md`
  - `docs/figures/benchmark_privacy_utility.png`

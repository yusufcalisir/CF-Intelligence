# FedProx Algorithm Specification

## 1. Problem Solved
FedProx (Li et al., 2020) addresses two major bottlenecks in federated banking networks:
1. **Statistical Heterogeneity (Non-IID Skew)**: Varying client fraud rates (e.g., retail bank vs commercial credit card issuer).
2. **System Heterogeneity (Stragglers)**: Variable computing resources and network speeds across participating institutions.

FedProx adds a proximal regularization term to the local objective function to constrain local updates from drifting too far from the global consensus model.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/application/services/fl_engine.py`](../../backend/app/application/services/fl_engine.py)
- **Mathematical Formulation**:
  Bank $k$ optimizes the regularized surrogate objective at round $t$:

  $$\min_{w \in \mathbb{R}^d} h_k(w; w_t) \triangleq F_k(w) + \frac{\mu}{2} \|w - w_t\|_2^2$$

  where $\mu \ge 0$ is the proximal regularization hyperparameter (default $\mu = 0.01$).
- **Gradient Update**:
  During local SGD on mini-batch $\mathcal{B}$, the effective gradient is:

  $$g_k(w) = \nabla F_k(w; \mathcal{B}) + \mu (w - w_t)$$

  When a bank experiences system slowdown or timeout, it can return an inexact solution ($\gamma$-inexact) after completing partial local epochs without destabilizing the global model.

---

## 3. Threat Model & Security Assumptions
- **Client Drift Bounds**: Guarantees bounded deviation $\|w_k - w_t\|_2 \le \frac{G}{\mu}$ even when local distributions are disjoint.
- **Privacy Stance**: Does not inherently encrypt or noise gradients; must be paired with Opacus Differential Privacy and Curve25519 SecAgg.

---

## 4. Operational Limitations
- **Hyperparameter Sensitivity**: Setting $\mu$ too large inhibits local learning, reducing convergence speed. Setting $\mu$ too small degenerates FedProx into standard FedAvg.
- **Gradient Computation**: Incurs marginal additional memory and compute overhead to store and compute Euclidean distance against the frozen global weight snapshot $w_t$.

---

## 5. Non-IID Dirichlet Sweep Empirical Evaluation

The platform evaluates FedProx ($\mu = 0.01$) against FedAvg and SCAFFOLD under varying degrees of Dirichlet label and feature skew $\alpha \in \{0.1, 0.5, 1.0\}$:
- **Experiment Runner**: [`experiments/ablations/dirichlet_sweep.py`](../../experiments/ablations/dirichlet_sweep.py)
- **Empirical Dossier**: [`experiments/ablations/audit_dossier.md`](../../experiments/ablations/audit_dossier.md)
- **Publication Figure**: [`docs/figures/benchmark_fl_convergence.png`](../figures/benchmark_fl_convergence.png)

### Empirical Results Summary across 10 Federation Rounds

| Skew Regime | Dirichlet $\alpha$ | FedProx PR-AUC | FedProx ROC-AUC | Validation Loss | Communication (MB) | Drift Suppression |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Pathological Extreme Skew** | $\alpha = 0.1$ | **0.2776** | 0.8628 | 0.0969 | 0.147 MB | Bounded ($\|w_k - w_t\|_2 \le 0.18$) |
| **Moderate Consortium Skew** | $\alpha = 0.5$ | **0.2285** | 0.9185 | 0.0969 | 0.147 MB | Stable convergence |
| **Mild Statistical Skew** | $\alpha = 1.0$ | **0.2180** | 0.8959 | 0.1068 | 0.147 MB | Baseline alignment |

*Key Takeaway*: Under extreme statistical heterogeneity ($\alpha = 0.1$), FedProx restrains client gradient drift, preventing the loss oscillation characteristic of standard FedAvg without requiring the stateful control variate memory of SCAFFOLD.

---

## 6. Test Suite Verification
- **Unit Tests**: [`backend/tests/unit/test_fl_engine.py`](../../backend/tests/unit/test_fl_engine.py)
- **Dirichlet Sensitivity Tests**: [`backend/tests/unit/test_dirichlet_sweep.py`](../../backend/tests/unit/test_dirichlet_sweep.py)
- **Benchmark Runner**: [`benchmarks/runners/run_fl_benchmark.py`](../../benchmarks/runners/run_fl_benchmark.py)

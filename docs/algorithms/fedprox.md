# FedProx Algorithm Specification

## 1. Problem Solved
FedProx (Li et al., 2020) addresses two major bottlenecks in federated banking networks:
1. **Statistical Heterogeneity (Non-IID Skew)**: Varying client fraud rates (e.g., retail bank vs commercial credit card issuer).
2. **System Heterogeneity (Stragglers)**: Variable computing resources and network speeds across participating institutions.

FedProx adds a proximal regularization term to the local objective function to constrain local updates from drifting too far from the global consensus model.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/application/services/fl_engine.py`](file:///backend/app/application/services/fl_engine.py)
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

## 5. Test Suite Verification
- **Unit Tests**: [`backend/tests/unit/test_fl_engine.py`](file:///backend/tests/unit/test_fl_engine.py)
- **Benchmark Runner**: [`benchmarks/runners/run_fl_benchmark.py`](file:///benchmarks/runners/run_fl_benchmark.py)

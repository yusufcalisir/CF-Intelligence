# Federated Averaging (FedAvg) Algorithm Specification

## 1. Problem Solved
Federated Averaging (McMahan et al., 2017) resolves the distributed optimization problem of fitting a global fraud classification model across $K$ decentralized banking institutions without transferring or pooling raw transaction data.

Mathematically, it seeks to minimize the global empirical objective:

$$\min_{w \in \mathbb{R}^d} f(w) \quad \text{where} \quad f(w) \triangleq \sum_{k=1}^K \frac{n_k}{N} F_k(w)$$

where $n_k$ is the number of local transaction samples at Bank $k$, $N = \sum_{k=1}^K n_k$, and $F_k(w) = \frac{1}{n_k} \sum_{i \in \mathcal{D}_k} \ell(w; x_i, y_i)$ is Bank $k$'s local empirical risk.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/application/services/fl_engine.py`](file:///backend/app/application/services/fl_engine.py)
- **Aggregation Formulation**:
  At round $t$, the central coordinator broadcasts global weights $w_t$. Each participant bank executes $E$ local epochs of mini-batch SGD:

  $$w_k^{t+1} \leftarrow \mathrm{ClientUpdate}(k, w_t)$$

  The coordinator receives model update vectors $\Delta w_k = w_k^{t+1} - w_t$ and computes the weighted aggregate:

  $$w_{t+1} = w_t + \sum_{k \in \mathcal{S}_t} \frac{n_k}{\sum_{j \in \mathcal{S}_t} n_j} \Delta w_k$$

  where $\mathcal{S}_t \subseteq \{1, \dots, K\}$ is the active cohort selected for round $t$.

---

## 3. Threat Model & Security Assumptions
- **Honest-but-Curious Coordinator**: FedAvg assumes the coordinator faithfully aggregates weights but may inspect unmasked gradients.
- **Privacy Limitation**: Raw FedAvg without Differential Privacy or Secure Aggregation is vulnerable to gradient inversion attacks (Deep Leakage from Gradients) and membership inference.
- **Robustness Limitation**: Assumes all selected clients are honest ($f = 0$ malicious clients). A single adversarial client can poison $w_{t+1}$ by submitting arbitrarily scaled gradients.

---

## 4. Operational Limitations
- **Client Drift under Non-IID Data**: When local label distributions diverge significantly ($\alpha < 0.5$ in Dirichlet skew), local SGD steps pull weights towards local minima, degrading global convergence.
- **System Stragglers**: In synchronous FedAvg, round completion latency is bounded by the slowest banking node.

---

## 5. Test Suite Verification
- **Unit Tests**: [`backend/tests/unit/test_fl_engine.py`](file:///backend/tests/unit/test_fl_engine.py)
- **Benchmark Runner**: [`benchmarks/runners/run_fl_benchmark.py`](file:///benchmarks/runners/run_fl_benchmark.py)
